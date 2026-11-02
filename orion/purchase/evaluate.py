"""Vérification d'une offre contre un mandat, avant tout achat. Chaque critère est vrai, faux ou inconnu ; un seul
critère faux **ou inconnu** bloque l'achat (une information manquante n'est jamais supposée favorable)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from orion.purchase.models import ACTIVE, Mandate
from orion.watch.products import matches

SUSPICIOUS_DISCOUNT = 0.40


@dataclass
class Verdict:
    checks: list[tuple[str, bool | None, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def allowed(self) -> bool:
        return bool(self.checks) and all(ok is True for _, ok, _ in self.checks)

    @property
    def blocking(self) -> list[str]:
        return [f"{name} : {'inconnu' if ok is None else 'non'}" + (f" ({detail})" if detail else "")
                for name, ok, detail in self.checks if ok is not True]

    def as_list(self) -> list[dict]:
        return [{"critère": n, "ok": ok, "détail": d} for n, ok, d in self.checks]


def evaluate(mandate: Mandate, offer: dict, now: float | None = None, market: float | None = None) -> Verdict:
    """``offer`` : vendeur, titre, prix, livraison, disponible, état, garantie_mois, retour_jours, vendeur_note,
    vendeur_avis, place_de_marché, devise (forme de l'événement ``watch.found``)."""
    now = time.time() if now is None else now
    v = Verdict()
    v.add("mandat actif", mandate.state == ACTIVE, mandate.state)
    v.add("mandat non expiré", now < mandate.expires_at)
    v.add("achats restants", mandate.purchases < mandate.max_purchases, f"{mandate.purchases}/{mandate.max_purchases}")
    title = str(offer.get("titre") or offer.get("title") or "")
    found = matches(mandate.product, title)
    v.add("produit exact", found is not None, title[:80])
    if found is not None:
        if mandate.brands:
            v.add("marque autorisée", found.brand in mandate.brands if found.brand else None, found.brand)
        if mandate.models:
            v.add("modèle autorisé", found.model in mandate.models if found.model else None, found.model)
    condition = offer.get("état")
    v.add("état neuf", None if condition is None else condition == "new", str(condition))
    seller = str(offer.get("vendeur") or "").strip().lower()
    v.add("vendeur autorisé", bool(seller) and seller in mandate.sellers, seller)
    rating = offer.get("vendeur_note")
    v.add("fiabilité du vendeur", None if rating is None else rating >= mandate.min_seller_rating, f"{rating}")
    currency = str(offer.get("devise") or "EUR").upper()
    v.add("devise euro", currency == "EUR", currency)
    price, shipping = offer.get("prix"), offer.get("livraison")
    v.add("prix", None if price is None else price <= mandate.max_price, f"{price} / {mandate.max_price}")
    v.add("frais de livraison", None if shipping is None else shipping <= mandate.max_shipping,
          f"{shipping} / {mandate.max_shipping}")
    total = None if price is None or shipping is None else round(price + shipping, 2)
    v.add("budget restant", None if total is None else total <= mandate.remaining, f"{total} / {mandate.remaining}")
    available = offer.get("disponible")
    v.add("disponibilité", available if isinstance(available, bool) else None)
    warranty, returns = offer.get("garantie_mois"), offer.get("retour_jours")
    v.add("garantie", None if warranty is None else warranty >= mandate.min_warranty_months, f"{warranty} mois")
    v.add("conditions de vente (retour)", None if returns is None else returns >= mandate.min_returns_days,
          f"{returns} jours")
    risky = offer.get("place_de_marché") and rating is None
    too_cheap = market is not None and total is not None and total < market * (1 - SUSPICIOUS_DISCOUNT)
    flagged = bool(offer.get("suspecte"))
    v.add("risque de fraude ou de contrefaçon", not (risky or too_cheap or flagged),
          "prix anormalement bas" if too_cheap else "vendeur tiers sans note" if risky else
          "offre signalée suspecte" if flagged else "")
    return v
