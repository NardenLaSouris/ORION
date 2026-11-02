"""ORION PURCHASE, bac à sable : mandats, vérification stricte des offres, niveaux d'autorisation, paiement simulé
(refus, authentification forte), révocation, arrêt global, double commande, données de carte jamais acceptées,
intégration avec WATCH. Aucune commande ni aucun paiement réels."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.events import Event, EventBus  # noqa: E402
from orion.platform import Store  # noqa: E402
from orion.purchase import (  # noqa: E402
    MandateError, PurchaseEngine, PurchaseError, PurchaseSettings, Quote, SandboxMerchant, SandboxPayment,
    contains_card_data,
)
from orion.purchase.tools import purchase_tools  # noqa: E402
from orion.watch.engine import WATCH_FOUND  # noqa: E402

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def offer(**changes):
    base = {"vendeur": "LDLC", "titre": "ASUS ROG Astral GeForce RTX 5090 OC 32 Go", "prix": 1899.0,
            "livraison": 0.0, "disponible": True, "état": "new", "garantie_mois": 24, "retour_jours": 14,
            "vendeur_note": 0.97, "url": "https://ldlc.fr/rtx5090", "place_de_marché": False}
    return {**base, **changes}


MANDATE = {"product": "RTX 5090", "max_price": 2000, "max_shipping": 20, "total_budget": 2020,
           "expires_at": NOW + 30 * 86400, "sellers": ["LDLC", "Materiel"]}


class World:
    def __init__(self, level=1, outcomes=None, settings=None, **merchant):
        self.clock = Clock()
        self.payment = SandboxPayment(list(outcomes or []))
        self.merchant = SandboxMerchant(**merchant)
        self.alerts = []
        self.events = EventBus()
        self.engine = PurchaseEngine(Store(), self.payment, self.merchant, events=self.events, clock=self.clock,
                                     settings=settings or PurchaseSettings(level=level),
                                     alerts=type("A", (), {"deliver": lambda _, a: self.alerts.append(a)})())
        self.engine.start()

    def mandate(self, **changes):
        return self.engine.create_mandate({**MANDATE, **changes})

    def state(self):
        return [o["state"] for o in self.engine.orders()]


# --- Vérification : un critère faux ou inconnu bloque ------------------------------------------------------

@pytest.mark.parametrize("changes, blocking", [
    ({"prix": 2100.0}, "prix : non"),
    ({"titre": "Palit RTX 5080 GameRock"}, "produit exact : non"),
    ({"titre": "PC Gamer RTX 5090"}, "produit exact : non"),
    ({"vendeur": "Inconnu Shop"}, "vendeur autorisé : non"),
    ({"disponible": None}, "disponibilité : inconnu"),
    ({"livraison": None}, "frais de livraison : inconnu"),
    ({"livraison": 35.0}, "frais de livraison : non"),
    ({"garantie_mois": None}, "garantie : inconnu"),
    ({"état": "used"}, "état neuf : non"),
    ({"vendeur_note": None}, "fiabilité du vendeur : inconnu"),
    ({"devise": "USD"}, "devise euro : non"),
])
def test_any_false_or_unknown_criterion_blocks(changes, blocking):
    w = World()
    m = w.mandate()
    assert w.engine.consider(offer(**changes)) == []
    checks = w.engine.opportunities()[0]
    assert not checks["allowed"] and w.state() == []
    from orion.purchase.evaluate import evaluate
    assert any(b.startswith(blocking) for b in evaluate(m, offer(**changes), NOW).blocking)


def test_expired_mandate_and_exceeded_budget_block():
    w = World()
    w.mandate(expires_at=NOW + 3600)
    w.clock.t += 7200
    assert w.engine.consider(offer()) == [] and w.engine.mandates()[0].state == "EXPIRED"
    w3 = World()
    w3.mandate(total_budget=2000)
    assert w3.engine.consider(offer(prix=1995.0, livraison=10.0)) == []  # 2005 € > budget de 2000 €


def test_suspiciously_cheap_offer_is_blocked_as_fraud_risk():
    w = World()
    w.mandate()
    assert w.engine.consider(offer(prix=990.0), market=2400.0) == []
    assert any(c["critère"].startswith("risque de fraude") and c["ok"] is False
               for c in w.engine.opportunities()[0]["checks"])


# --- Niveaux ----------------------------------------------------------------------------------------------

def test_level_one_prepares_and_waits_for_the_users_confirmation():
    w = World(level=1)
    w.mandate(level=2)  # le mandat seul ne suffit pas : le réglage reste au niveau 1
    order = w.engine.consider(offer())[0]
    assert order["state"] == "AWAITING_CONFIRMATION" and w.payment.authorized == []
    assert w.alerts[-1].title == "Achat à confirmer"
    with pytest.raises(PurchaseError):
        w.engine.confirm(order["id"], by="llm")
    placed = w.engine.confirm(order["id"])
    assert placed["state"] == "PLACED" and w.payment.authorized == [(order["id"], 189900)]
    mandate = w.engine.mandates()[0]
    assert mandate.spent == 1899.0 and mandate.purchases == 1 and mandate.state == "EXHAUSTED"


def test_level_two_buys_automatically_only_when_setting_and_mandate_allow_it():
    w = World(level=2)
    w.mandate(level=1)
    assert w.engine.consider(offer())[0]["state"] == "AWAITING_CONFIRMATION"
    w2 = World(level=2)
    w2.mandate(level=2)
    assert w2.engine.consider(offer())[0]["state"] == "PLACED" and w2.merchant.placed


def test_level_three_needs_an_explicit_envelope():
    w = World(settings=PurchaseSettings(level=3, allow_level3=False))
    m = w.mandate(level=3)
    assert w.engine.effective_level(m, 1899.0) == 2
    w2 = World(settings=PurchaseSettings(level=3, allow_level3=True, per_purchase_max=1500, period_max=3000))
    m2 = w2.mandate(level=3)
    assert w2.engine.effective_level(m2, 1899.0) == 2  # au-dessus du plafond par achat
    assert w2.engine.effective_level(m2, 1400.0) == 3


# --- Commande : paiement, authentification, révocation, double commande ------------------------------------

def test_same_offer_is_never_ordered_twice():
    w = World(level=2)
    w.mandate(level=2, max_purchases=2, total_budget=4100)
    assert len(w.engine.consider(offer())) == 1
    assert w.engine.consider(offer()) == []  # même offre signalée de nouveau
    assert w.state() == ["PLACED"] and len(w.payment.authorized) == 1


def test_declined_payment_releases_the_budget():
    w = World(level=2, outcomes=["declined"])
    w.mandate(level=2)
    order = w.engine.consider(offer())[0]
    assert order["state"] == "REFUSED" and w.merchant.placed == []
    mandate = w.engine.mandates()[0]
    assert mandate.reserved == 0 and mandate.spent == 0 and mandate.state == "ACTIVE"


def test_strong_authentication_suspends_the_order_until_the_user_authenticates():
    w = World(level=2, outcomes=["requires_action"])
    w.mandate(level=2)
    order = w.engine.consider(offer())[0]
    assert order["state"] == "AWAITING_AUTH" and w.merchant.placed == []
    assert w.alerts[-1].title == "Authentification bancaire requise"
    assert w.engine.mandates()[0].reserved == 1899.0
    with pytest.raises(PurchaseError):
        w.engine.complete_auth(order["id"], True, by="evolve")
    assert w.engine.complete_auth(order["id"], True)["state"] == "PLACED"


def test_failed_authentication_or_cancellation_voids_the_payment():
    w = World(level=2, outcomes=["requires_action"])
    w.mandate(level=2)
    order = w.engine.consider(offer())[0]
    assert w.engine.complete_auth(order["id"], False)["state"] == "REFUSED"
    assert w.engine.mandates()[0].reserved == 0
    w2 = World(level=2, outcomes=["requires_action"])
    w2.mandate(level=2)
    order2 = w2.engine.consider(offer())[0]
    assert w2.engine.cancel(order2["id"])["state"] == "CANCELLED" and w2.payment.voided


def test_revocation_during_an_order_cancels_it_before_payment():
    w = World(level=2)
    m = w.mandate(level=2)
    w.merchant.on_quote = lambda: w.engine.revoke(m.id)  # révoqué pendant le devis du marchand
    order = w.engine.consider(offer())[0]
    assert order["state"] == "CANCELLED" and "révoqué" in order["reason"] and w.payment.authorized == []


def test_revocation_cancels_orders_waiting_for_confirmation():
    w = World(level=1)
    m = w.mandate()
    order = w.engine.consider(offer())[0]
    w.engine.revoke(m.id)
    assert w.engine.orders()[0]["state"] == "CANCELLED"
    with pytest.raises(ValueError):
        w.engine.confirm(order["id"])  # plus rien à confirmer
    assert w.engine.consider(offer(url="https://ldlc.fr/autre")) == []


def test_live_quote_is_checked_again_before_paying():
    w = World(level=2, quotes={"ldlc": Quote("LDLC", offer()["titre"], 1999.0, 15.0, True, "new", 24, 14, 0.97)})
    w.mandate(level=2)
    order = w.engine.consider(offer())[0]
    assert order["state"] == "BLOCKED" and ("prix" in order["reason"] or "budget" in order["reason"])
    assert w.payment.authorized == []


def test_merchant_failure_voids_the_payment():
    w = World(level=2, fail_place=True)
    w.mandate(level=2)
    order = w.engine.consider(offer())[0]
    assert order["state"] == "FAILED" and w.payment.voided and w.engine.mandates()[0].spent == 0


def test_global_stop_blocks_everything_and_cancels_pending_orders():
    w = World(level=1)
    w.mandate()
    w.engine.consider(offer())
    tool = {t.name: t for t in purchase_tools(w.engine)}["stop_purchases"]
    assert tool.run()["stopped"] and w.state() == ["CANCELLED"]
    assert w.engine.consider(offer(url="https://ldlc.fr/autre")) == []
    w2 = World(level=2)
    w2.mandate(level=2)
    w2.engine.set_stopped(True, "utilisateur")
    assert w2.engine.consider(offer()) == []


# --- Sécurité des données et du paiement ----------------------------------------------------------------------

def test_card_data_is_never_accepted():
    assert not contains_card_data("référence 1234 5678 9012 3456")  # 16 chiffres, Luhn invalide : pas une carte
    assert contains_card_data("carte 4111 1111 1111 1111")  # numéro de test Visa (Luhn valide)
    assert contains_card_data({"note": "cvv 123"}) and contains_card_data("expiration 12/29")
    assert not contains_card_data("commande 1899 € le 02/11")
    w = World()
    with pytest.raises(MandateError, match="carte"):
        w.mandate(note="payer avec 4111111111111111")
    with pytest.raises(MandateError, match="carte"):
        w.engine.create_mandate({**MANDATE, "card_number": "4111 1111 1111 1111"})


def test_no_real_payment_can_be_plugged_in():
    with pytest.raises(PurchaseError, match="bac à sable"):
        PurchaseEngine(Store(), SandboxPayment(sandbox=False), SandboxMerchant())
    with pytest.raises(PurchaseError):
        PurchaseEngine(Store(), SandboxPayment(), SandboxMerchant(), settings=PurchaseSettings(sandbox=False))


def test_mandate_validation():
    w = World()
    for bad in ({"product": "iPhone 16"}, {"sellers": []}, {"total_budget": 100}, {"max_purchases": 9},
                {"expires_at": NOW - 1}, {"level": 4}):
        with pytest.raises(MandateError):
            w.mandate(**bad)


# --- WATCH -> PURCHASE --------------------------------------------------------------------------------------

def test_watch_findings_are_considered_but_only_offers_and_only_with_a_mandate():
    w = World(level=1)
    found = {"watch_id": "w1", "kind": "price", "title": offer()["titre"], "url": offer()["url"],
             **{k: v for k, v in offer().items() if k not in ("titre", "url")}}
    w.events.publish(Event(WATCH_FOUND, "watch", found))
    assert w.state() == [] and w.engine.opportunities() == []  # aucun mandat : surveiller n'est pas acheter
    w.mandate()
    w.events.publish(Event(WATCH_FOUND, "watch", {**found, "kind": "event"}))
    assert w.state() == []
    w.events.publish(Event(WATCH_FOUND, "watch", found))
    assert w.state() == ["AWAITING_CONFIRMATION"]


def test_admin_routes():
    w = World()
    status, mandate = w.engine.admin("POST", ["mandates"], {}, MANDATE)
    assert status == 201 and mandate["product"] == "rtx 5090"
    w.engine.consider(offer())
    order_id = w.engine.orders()[0]["id"]
    assert w.engine.admin("GET", [], {}, None)[1]["en_attente"] == 1
    assert w.engine.admin("POST", ["orders", order_id, "cancel"], {}, None)[1]["state"] == "CANCELLED"
    assert w.engine.admin("POST", ["stop"], {}, None)[1] == {"arrêt_global": True}
    assert w.engine.admin("POST", ["resume"], {}, None)[1] == {"arrêt_global": False}
    assert w.engine.admin("POST", ["mandates", mandate["id"], "revoke"], {}, None)[1]["state"] == "REVOKED"
    assert w.engine.admin("POST", ["mandates"], {}, {**MANDATE, "note": "cvv 999"})[0] == 400
    assert w.engine.admin("GET", ["history"], {}, None)[1]
