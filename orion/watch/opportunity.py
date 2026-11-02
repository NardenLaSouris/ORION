"""Score d'opportunité d'une offre, sur 100, et historique des prix.

Six composantes, chacune entre 0 et 1, pondérées (``[watch] opportunity``, total 100 par défaut) :
- marché : écart avec le prix de marché (médiane des offres fiables, neuves et disponibles du moment) ;
- historique : position par rapport aux prix observés ces 30 derniers jours (minimum, médiane) ;
- vendeur : note et nombre d'avis ;
- disponibilité : en stock ou non ;
- conditions : état (neuf), garantie, délai de retour ;
- livraison : frais connus et faibles.

Garde-fous (règles, pas le LLM) :
- donnée manquante (marché, vendeur, disponibilité, livraison) : score plafonné à 69, jamais « exceptionnelle » ;
- offre suspecte (prix anormalement bas, gros rabais d'un vendeur peu fiable, prix barré gonflé) : plafonné à 40 ;
- une offre indisponible n'est jamais une opportunité.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

from orion.platform.store import Migration, Store
from orion.watch.connectors import Offer

WEIGHTS = {"market": 35.0, "history": 20.0, "seller": 20.0, "availability": 10.0, "conditions": 10.0,
           "shipping": 5.0}
LABELS = ((85, "exceptionnelle"), (70, "très bonne affaire"), (50, "correcte"), (0, "sans intérêt"))
REQUIRED = ("market", "seller", "availability", "shipping")
MIN_MARKET_OFFERS = 3
MIN_HISTORY = 5
FULL_DISCOUNT = 0.25  # 25 % sous le marché : composante « marché » au maximum
SUSPICIOUS_DISCOUNT = 0.40  # plus de 40 % sous le marché : trop beau pour être vrai
INSUFFICIENT_CAP, SUSPICIOUS_CAP = 69, 40

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL,
            seller TEXT NOT NULL,
            total REAL NOT NULL,
            at REAL NOT NULL,
            url TEXT NOT NULL
        );
        CREATE INDEX price_history_product ON price_history (product, at)
    """),
)


@dataclass(frozen=True)
class PriceStats:
    count: int
    minimum: float
    median: float
    maximum: float


class PriceHistory:
    """Prix finaux (livraison comprise) observés, par produit ; une observation par vendeur et par heure au plus."""

    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self._store, self._clock = store, clock
        store.migrate("price_history", MIGRATIONS)

    def record(self, product: str, offer: Offer) -> bool:
        total = offer.total
        if total is None or offer.available is False:
            return False
        now = self._clock()
        recent = self._store.one("SELECT id FROM price_history WHERE product = ? AND seller = ? AND at > ?",
                                 (product, offer.seller.lower(), now - 3600))
        if recent is not None:
            return False
        self._store.write("INSERT INTO price_history (product, seller, total, at, url) VALUES (?, ?, ?, ?, ?)",
                          (product, offer.seller.lower(), total, now, offer.url))
        return True

    def stats(self, product: str, days: float = 30) -> PriceStats | None:
        rows = self._store.query("SELECT total FROM price_history WHERE product = ? AND at > ?",
                                 (product, self._clock() - days * 86400))
        totals = [r["total"] for r in rows]
        if not totals:
            return None
        return PriceStats(len(totals), min(totals), statistics.median(totals), max(totals))

    def series(self, product: str, days: float = 90) -> list[dict]:
        return self._store.query("SELECT seller, total, at, url FROM price_history WHERE product = ? AND at > ? "
                                 "ORDER BY at", (product, self._clock() - days * 86400))


def market_price(offers: list[Offer]) -> float | None:
    """Médiane des offres neuves, disponibles, au prix final connu et de vendeurs fiables ; None sous 3 offres."""
    totals = [o.total for o in offers if o.total is not None and o.available is True and o.condition == "new"
              and o.seller_rating is not None and o.seller_rating >= 0.8]
    return statistics.median(totals) if len(totals) >= MIN_MARKET_OFFERS else None


@dataclass(frozen=True)
class Opportunity:
    score: int
    label: str
    components: dict = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def sufficient(self) -> bool:
        return not self.missing

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def evaluate(offer: Offer, market: float | None, history: PriceStats | None,
             weights: dict | None = None) -> Opportunity:
    w = {**WEIGHTS, **{k: float(v) for k, v in (weights or {}).items() if k in WEIGHTS}}
    total = offer.total
    parts: dict[str, float | None] = {}
    reasons: list[str] = []
    flags: list[str] = []

    if total is None or market is None:
        parts["market"] = None
    else:
        gap = (market - total) / market
        parts["market"] = _clamp(gap / FULL_DISCOUNT)
        reasons.append(f"{abs(gap) * 100:.0f} % {'sous' if gap >= 0 else 'au-dessus du'} le prix du marché "
                       f"({market:.0f} €)".replace("au-dessus du le", "au-dessus du"))
        if gap > SUSPICIOUS_DISCOUNT:
            flags.append(f"prix {gap * 100:.0f} % sous le marché : anormalement bas")

    if total is None or history is None or history.count < MIN_HISTORY:
        parts["history"] = None
    else:
        if total <= history.minimum:
            parts["history"] = 1.0
        elif total >= history.median:
            parts["history"] = 0.0
        else:
            parts["history"] = _clamp((history.median - total) / max(1e-6, history.median - history.minimum))
        reasons.append(f"plus bas observé sur 30 jours : {history.minimum:.0f} €")
        if offer.reference_price is not None and offer.reference_price > history.maximum * 1.10:
            flags.append(f"prix barré ({offer.reference_price:.0f} €) au-dessus du plus haut observé "
                         f"({history.maximum:.0f} €) : fausse promotion probable")

    if offer.seller_rating is None:
        parts["seller"] = 0.2 if offer.marketplace else None
    else:
        confidence = min(1.0, (offer.seller_reviews or 0) / 100)
        parts["seller"] = _clamp(offer.seller_rating * (0.5 + 0.5 * confidence))
        risky = offer.seller_rating < 0.8 or (offer.seller_reviews or 0) < 20
        if risky and parts.get("market") is not None and market and total is not None \
                and (market - total) / market > 0.15:
            flags.append("gros rabais d'un vendeur peu noté ou peu connu")

    parts["availability"] = None if offer.available is None else (1.0 if offer.available else 0.0)

    if offer.condition is None:
        parts["conditions"] = None
    else:
        base = {"new": 0.7, "refurbished": 0.35, "used": 0.15}[offer.condition]
        base += 0.15 if (offer.warranty_months or 0) >= 24 else 0.0
        base += 0.15 if (offer.returns_days or 0) >= 14 else 0.0
        parts["conditions"] = _clamp(base)

    parts["shipping"] = None if offer.shipping is None else _clamp(1 - offer.shipping / 30)

    missing = tuple(k for k in REQUIRED if parts.get(k) is None)
    score = sum(w[k] * (v or 0.0) for k, v in parts.items()) * 100 / max(1e-6, sum(w.values()))
    if offer.available is False:
        score, reasons = 0.0, [*reasons, "indisponible"]
    if missing:
        score = min(score, INSUFFICIENT_CAP)
        reasons.append("données insuffisantes : " + ", ".join(missing))
    if flags:
        score = min(score, SUSPICIOUS_CAP)
    score_int = int(round(score))
    label = next(name for floor, name in LABELS if score_int >= floor)
    return Opportunity(score_int, label, {k: (None if v is None else round(v, 2)) for k, v in parts.items()},
                       missing, tuple(flags), tuple(reasons))
