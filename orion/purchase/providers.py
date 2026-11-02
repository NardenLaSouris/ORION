"""Prestataire de paiement et marchand : interfaces, et leurs seules réalisations dans cette version, en bac à sable.

- ORION ne voit jamais la carte : le prestataire détient le moyen de paiement (idéalement une carte virtuelle dédiée,
  plafonnée) et ne rend qu'une référence de transaction.
- Authentification forte (3-D Secure) demandée : ``requires_action`` ; la commande est suspendue et l'utilisateur
  s'authentifie lui-même. ORION ne la contourne jamais et n'a aucun moyen de la remplir.
- Aucun prestataire ni marchand réel n'est branché : brancher un vrai paiement exige une autorisation explicite de
  l'utilisateur et la validation de l'environnement de paiement (``[purchase] sandbox = false`` est refusé tant
  qu'aucun prestataire réel n'existe).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Protocol

APPROVED, DECLINED, REQUIRES_ACTION, ERROR = "approved", "declined", "requires_action", "error"


@dataclass(frozen=True)
class PaymentResult:
    status: str
    reference: str = ""
    message: str = ""


class PaymentProvider(Protocol):
    name: str
    sandbox: bool

    def authorize(self, order_ref: str, amount_cents: int, currency: str, mandate_ref: str) -> PaymentResult: ...

    def complete(self, reference: str, authenticated: bool) -> PaymentResult: ...

    def void(self, reference: str) -> None: ...


@dataclass(frozen=True)
class Quote:
    seller: str
    title: str
    price: float | None
    shipping: float | None
    available: bool | None
    condition: str | None
    warranty_months: int | None
    returns_days: int | None
    seller_rating: float | None
    currency: str = "EUR"

    def as_offer(self) -> dict:
        return {"vendeur": self.seller, "titre": self.title, "prix": self.price, "livraison": self.shipping,
                "disponible": self.available, "état": self.condition, "garantie_mois": self.warranty_months,
                "retour_jours": self.returns_days, "vendeur_note": self.seller_rating, "devise": self.currency}


@dataclass(frozen=True)
class MerchantResult:
    ok: bool
    order_number: str = ""
    message: str = ""


class Merchant(Protocol):
    name: str
    sandbox: bool

    def quote(self, offer: dict) -> Quote: ...

    def place(self, order_ref: str, quote: Quote, payment_ref: str) -> MerchantResult: ...


@dataclass
class SandboxPayment:
    """Prestataire simulé : ``outcomes`` dit ce que rend chaque autorisation, dans l'ordre (approved par défaut)."""
    outcomes: list[str] = field(default_factory=list)
    name: str = "bac à sable"
    sandbox: bool = True
    authorized: list[tuple[str, int]] = field(default_factory=list)
    voided: list[str] = field(default_factory=list)

    def authorize(self, order_ref: str, amount_cents: int, currency: str, mandate_ref: str) -> PaymentResult:
        status = self.outcomes.pop(0) if self.outcomes else APPROVED
        self.authorized.append((order_ref, amount_cents))
        return PaymentResult(status, f"sbx_{uuid.uuid4().hex[:12]}", {"declined": "paiement refusé par la banque",
                                                                      "requires_action": "authentification forte "
                                                                      "requise", "error": "prestataire indisponible"}
                             .get(status, ""))

    def complete(self, reference: str, authenticated: bool) -> PaymentResult:
        return PaymentResult(APPROVED if authenticated else DECLINED, reference)

    def void(self, reference: str) -> None:
        self.voided.append(reference)


@dataclass
class SandboxMerchant:
    """Marchand simulé : ``quotes`` (vendeur -> devis du moment) ; ``on_quote`` permet de simuler un événement pendant
    la commande (révocation, changement de prix) ; ``fail_place`` : la commande échoue chez le marchand."""
    quotes: dict[str, Quote] = field(default_factory=dict)
    name: str = "bac à sable"
    sandbox: bool = True
    fail_place: bool = False
    on_quote: Callable[[], None] | None = None
    placed: list[str] = field(default_factory=list)

    def quote(self, offer: dict) -> Quote:
        if self.on_quote is not None:
            self.on_quote()
        seller = str(offer.get("vendeur") or "").lower()
        if seller in self.quotes:
            return self.quotes[seller]
        return Quote(offer.get("vendeur", ""), offer.get("titre", ""), offer.get("prix"), offer.get("livraison"),
                     offer.get("disponible"), offer.get("état"), offer.get("garantie_mois"), offer.get("retour_jours"),
                     offer.get("vendeur_note"))

    def place(self, order_ref: str, quote: Quote, payment_ref: str) -> MerchantResult:
        if self.fail_place:
            return MerchantResult(False, message="rupture au moment de commander")
        self.placed.append(order_ref)
        return MerchantResult(True, f"SBX-{order_ref}")
