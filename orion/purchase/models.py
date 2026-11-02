"""Mandats et commandes d'ORION PURCHASE.

Un mandat est l'autorisation précise, donnée par l'utilisateur, d'acheter un produit à des conditions fixées. Une
surveillance (WATCH) n'est jamais un mandat : sans mandat actif qui correspond, rien n'est acheté ni préparé.
Aucune donnée de carte bancaire n'est jamais acceptée dans un mandat, une commande, l'historique ou un journal
(``contains_card_data`` : suite de 13 à 19 chiffres valide selon Luhn, cryptogramme, date d'expiration de carte).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from orion.platform.states import StateMachine
from orion.watch.products import wanted

ACTIVE, REVOKED, EXPIRED, EXHAUSTED = "ACTIVE", "REVOKED", "EXPIRED", "EXHAUSTED"
MANDATE_STATES = StateMachine({ACTIVE: {REVOKED, EXPIRED, EXHAUSTED}, REVOKED: set(), EXPIRED: set(),
                               EXHAUSTED: {ACTIVE}}, ACTIVE)

PREPARED, AWAITING_CONFIRMATION, CONFIRMED, PAYING = "PREPARED", "AWAITING_CONFIRMATION", "CONFIRMED", "PAYING"
AWAITING_AUTH, PLACED, REFUSED, FAILED, CANCELLED, BLOCKED = ("AWAITING_AUTH", "PLACED", "REFUSED", "FAILED",
                                                               "CANCELLED", "BLOCKED")
ORDER_STATES = StateMachine({
    PREPARED: {AWAITING_CONFIRMATION, CONFIRMED, CANCELLED, BLOCKED},
    AWAITING_CONFIRMATION: {CONFIRMED, CANCELLED, BLOCKED},
    CONFIRMED: {PAYING, CANCELLED, BLOCKED},
    PAYING: {AWAITING_AUTH, PLACED, REFUSED, FAILED, CANCELLED},
    AWAITING_AUTH: {PLACED, REFUSED, FAILED, CANCELLED},
    PLACED: set(), REFUSED: set(), FAILED: set(), CANCELLED: set(), BLOCKED: set(),
}, PREPARED)
OPEN_ORDERS = (PREPARED, AWAITING_CONFIRMATION, CONFIRMED, PAYING, AWAITING_AUTH)

PAN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
CVV = re.compile(r"(?i)\b(?:cvv|cvc|cryptogramme|code de s[ée]curit[ée])\b")
EXPIRY = re.compile(r"(?i)\b(?:exp(?:iration)?|valable jusqu)[^\d]{0,12}(0[1-9]|1[0-2])\s*/\s*\d{2,4}\b")


class MandateError(ValueError):
    pass


def luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, char in enumerate(digits):
        d = int(char)
        if i % 2 == parity:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def contains_card_data(value: Any) -> bool:
    """Vrai si une chaîne (ou un dictionnaire, une liste) contient un numéro de carte, un cryptogramme ou une date
    d'expiration de carte."""
    if isinstance(value, dict):
        return any(contains_card_data(k) or contains_card_data(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(contains_card_data(v) for v in value)
    if not isinstance(value, str):
        return False
    for match in PAN.finditer(value):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and luhn(digits):
            return True
    return bool(CVV.search(value) or EXPIRY.search(value))


def _money(value: Any, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or (value == 0 and not allow_zero) \
            or value > 100_000:
        raise MandateError(f"{label} : montant en euros attendu")
    return round(float(value), 2)


def _names(value: Any, label: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or len(value) > 20 or not all(isinstance(v, str) and v.strip()
                                                                         for v in value):
        raise MandateError(f"{label} : liste de noms attendue")
    return tuple(v.strip().lower() for v in value)


@dataclass
class Mandate:
    product: str  # « rtx 5090 »
    max_price: float  # prix de l'article
    max_shipping: float  # frais de livraison
    total_budget: float  # dépenses totales permises par ce mandat
    expires_at: float
    sellers: tuple[str, ...]  # vendeurs autorisés (noms) ; vide : aucun vendeur, donc aucun achat
    brands: tuple[str, ...] = ()  # vide : toutes les marques de la carte
    models: tuple[str, ...] = ()
    condition: str = "new"
    max_purchases: int = 1
    min_seller_rating: float = 0.9
    min_warranty_months: int = 24
    min_returns_days: int = 14
    level: int = 1
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: float = field(default_factory=time.time)
    state: str = ACTIVE
    spent: float = 0.0
    reserved: float = 0.0
    purchases: int = 0

    def __post_init__(self) -> None:
        family, variant = wanted(str(self.product))
        if not family.startswith("rtx "):
            raise MandateError("produit : seules les cartes RTX sont identifiables dans cette version")
        self.product = family + (f" {variant}" if variant else "")
        self.max_price = _money(self.max_price, "prix maximum")
        self.max_shipping = _money(self.max_shipping, "frais de livraison maximum", allow_zero=True)
        self.total_budget = _money(self.total_budget, "budget total")
        if self.total_budget < self.max_price:
            raise MandateError("budget total inférieur au prix maximum : aucun achat possible")
        self.sellers, self.brands, self.models = (_names(self.sellers, "vendeurs"), _names(self.brands, "marques"),
                                                  _names(self.models, "modèles"))
        if not self.sellers:
            raise MandateError("au moins un vendeur autorisé")
        if self.condition not in ("new",):
            raise MandateError("seul l'état « neuf » est permis")
        for name, value, low, high in (("nombre d'achats", self.max_purchases, 1, 5),
                                       ("garantie minimale (mois)", self.min_warranty_months, 0, 60),
                                       ("délai de retour minimal (jours)", self.min_returns_days, 0, 60),
                                       ("niveau", self.level, 1, 3)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise MandateError(f"{name} : entier de {low} à {high}")
        if isinstance(self.min_seller_rating, bool) or not 0 <= float(self.min_seller_rating) <= 1:
            raise MandateError("note minimale du vendeur : de 0 à 1")
        if not isinstance(self.expires_at, (int, float)) or self.expires_at <= self.created_at \
                or self.expires_at > self.created_at + 365 * 86400:
            raise MandateError("expiration : dans l'année")
        self.note = str(self.note or "")[:300]
        if contains_card_data(asdict(self)):
            raise MandateError("données de carte bancaire refusées : ORION n'en garde jamais")

    @property
    def remaining(self) -> float:
        return round(self.total_budget - self.spent - self.reserved, 2)

    def as_dict(self) -> dict:
        data = asdict(self)
        for key in ("sellers", "brands", "models"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Mandate":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Order:
    mandate_id: str
    key: str  # mandat + vendeur + adresse + total : la même offre n'est jamais commandée deux fois
    offer: dict
    total: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = PREPARED
    created_at: float = field(default_factory=time.time)
    payment_ref: str = ""
    merchant_ref: str = ""
    reason: str = ""
    history: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)
