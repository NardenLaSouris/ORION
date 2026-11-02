"""Connecteurs de WATCH : d'où viennent les observations (offres d'un produit, articles, annonces, événements).

Deux sortes d'observations :
- ``Offer`` : une offre d'un vendeur (prix, livraison, disponibilité, état, vendeur, garantie) ;
- ``Item`` : un article ou une annonce (titre, résumé, date, fiabilité de la source).

Une valeur inconnue reste ``None`` : jamais une disponibilité, un prix ou des frais devinés. Une source qui échoue lève
``SourceError`` (la vérification la note comme indisponible, sans rien inventer à la place).

Connecteurs fournis :
- ``FeedConnector`` : flux RSS/Atom (annonces officielles, actualités de jeux, sorties logicielles) ;
- ``OffersJsonConnector`` : un flux JSON d'offres au format documenté ci-dessous (API d'un marchand, d'un comparateur
  autorisé, ou petit service intermédiaire) ; ORION ne « gratte » aucun site marchand ;
- ``StaticConnector`` : observations fixes (essais, démonstration, bac à sable).

Format ``offers_json`` : ``{"offers": [{"seller", "title", "price", "shipping", "currency", "available", "condition",
"url", "seller_rating" (0 à 1), "seller_reviews", "warranty_months", "returns_days", "reference_price",
"marketplace"}]}`` ; tout champ absent vaut « inconnu ».
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from orion.platform.feeds import Fetcher, SourceError, fetch_feed, http_get, text_of

OFFERS, ITEMS = "offers", "items"


@dataclass(frozen=True)
class Offer:
    source: str
    seller: str
    title: str
    url: str
    price: float | None = None
    shipping: float | None = None
    currency: str = "EUR"
    available: bool | None = None
    condition: str | None = None  # "new" | "used" | "refurbished"
    seller_rating: float | None = None  # 0 à 1
    seller_reviews: int | None = None
    warranty_months: int | None = None
    returns_days: int | None = None
    reference_price: float | None = None  # prix barré annoncé par le vendeur
    marketplace: bool = False  # vendeur tiers d'une place de marché
    observed_at: float = field(default_factory=time.time)

    @property
    def total(self) -> float | None:
        """Prix final livraison comprise ; inconnu si l'un des deux l'est."""
        return None if self.price is None or self.shipping is None else round(self.price + self.shipping, 2)


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    summary: str
    url: str
    published_at: float | None
    reliability: str  # "official" | "agency" | "media" | "community" | "unknown"
    guid: str = ""


class Connector:
    name = "connecteur"
    kind = ITEMS

    def fetch(self, query: dict) -> list:
        raise NotImplementedError


class FeedConnector(Connector):
    kind = ITEMS

    def __init__(self, name: str, url: str, reliability: str = "unknown", fetcher: Fetcher = http_get):
        self.name, self.url, self.reliability, self._fetcher = name, url, reliability, fetcher

    def fetch(self, query: dict) -> list[Item]:
        return [Item(self.name, e.title, e.summary, e.url, e.published_at, self.reliability, e.guid)
                for e in fetch_feed(self.url, self._fetcher)]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def offer_from(source: str, raw: dict) -> Offer | None:
    """Offre lue d'un flux ; None si elle n'a même pas de titre, de vendeur ou d'adresse."""
    if not isinstance(raw, dict):
        return None
    title, seller, url = (text_of(str(raw.get(k) or ""), 300) for k in ("title", "seller", "url"))
    if not title or not seller or not url.startswith(("http://", "https://")):
        return None
    available = raw.get("available")
    condition = raw.get("condition")
    rating = _num(raw.get("seller_rating"))
    return Offer(source, seller, title, url, price=_num(raw.get("price")), shipping=_num(raw.get("shipping")),
                 currency=str(raw.get("currency") or "EUR")[:3].upper(),
                 available=available if isinstance(available, bool) else None,
                 condition=condition if condition in ("new", "used", "refurbished") else None,
                 seller_rating=rating if rating is not None and rating <= 1 else None,
                 seller_reviews=_int(raw.get("seller_reviews")), warranty_months=_int(raw.get("warranty_months")),
                 returns_days=_int(raw.get("returns_days")), reference_price=_num(raw.get("reference_price")),
                 marketplace=bool(raw.get("marketplace", False)))


class OffersJsonConnector(Connector):
    kind = OFFERS

    def __init__(self, name: str, url: str, fetcher: Fetcher = http_get):
        self.name, self.url, self._fetcher = name, url, fetcher

    def fetch(self, query: dict) -> list[Offer]:
        try:
            data = json.loads(self._fetcher(self.url))
        except ValueError as exc:
            raise SourceError("JSON illisible") from exc
        offers = data.get("offers") if isinstance(data, dict) else None
        if not isinstance(offers, list):
            raise SourceError("liste « offers » absente")
        return [o for o in (offer_from(self.name, raw) for raw in offers[:500]) if o is not None]


class StaticConnector(Connector):
    def __init__(self, name: str, observations: list | Callable[[], list], kind: str = ITEMS):
        self.name, self.kind, self._observations = name, kind, observations

    def fetch(self, query: dict) -> list:
        data = self._observations() if callable(self._observations) else self._observations
        if isinstance(data, Exception):
            raise data
        return list(data)


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        if connector.name in self._connectors:
            raise ValueError(f"Source déjà enregistrée : {connector.name}")
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def names(self, kind: str | None = None) -> list[str]:
        return [n for n, c in self._connectors.items() if kind is None or c.kind == kind]

    def describe(self) -> list[dict]:
        return [{"name": c.name, "kind": c.kind, "reliability": getattr(c, "reliability", ""),
                 "url": getattr(c, "url", "")} for c in self._connectors.values()]


def build_connectors(sources: dict, fetcher: Fetcher = http_get) -> ConnectorRegistry:
    """Sources déclarées dans ``[watch] sources`` ; une entrée invalide est refusée nommément."""
    registry = ConnectorRegistry()
    for name, spec in (sources or {}).items():
        if not isinstance(spec, dict) or not isinstance(spec.get("url"), str):
            raise ValueError(f"[watch] sources.{name} : « url » attendue")
        kind = spec.get("type", "rss")
        if kind == "rss":
            registry.register(FeedConnector(name, spec["url"], spec.get("reliability", "unknown"), fetcher))
        elif kind == "offers_json":
            registry.register(OffersJsonConnector(name, spec["url"], fetcher))
        else:
            raise ValueError(f"[watch] sources.{name} : type inconnu {kind!r} (rss, offers_json)")
    return registry


__all__ = ["ITEMS", "OFFERS", "Connector", "ConnectorRegistry", "FeedConnector", "Item", "Offer",
           "OffersJsonConnector", "SourceError", "StaticConnector", "build_connectors", "offer_from"]
