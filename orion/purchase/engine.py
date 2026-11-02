"""ORION PURCHASE : achats conditionnels sous mandat, en bac à sable dans cette version.

Surveiller, proposer, acheter sont trois choses distinctes :
- WATCH signale une offre (événement ``watch.found``) : ce n'est jamais une autorisation ;
- sans mandat actif qui lui correspond, PURCHASE ne fait rien (l'offre est seulement notée) ;
- avec un mandat, l'offre est vérifiée critère par critère (orion/purchase/evaluate.py) ; un critère faux ou inconnu
  bloque ; si tout est vérifié :
  - niveau 1 (par défaut) : commande préparée, l'utilisateur confirme dans ORION Control ;
  - niveau 2 : commande automatique, seulement si le réglage ``[purchase] level`` ET le mandat le permettent, que
    l'arrêt global n'est pas actif, et que le prestataire et le marchand sont ceux du bac à sable (aucun réel
    n'existe) ;
  - niveau 3 : comme le niveau 2, dans une enveloppe : plafond par achat et par période (``allow_level3``).
- Avant chaque étape (devis, paiement, commande) : arrêt global, révocation et expiration revérifiés ; le devis du
  moment est revérifié contre le mandat ; le budget est réservé sous verrou et libéré si l'achat n'aboutit pas.
- Authentification forte demandée : commande suspendue (AWAITING_AUTH), l'utilisateur s'authentifie lui-même.
- La même offre n'est jamais commandée deux fois (clé mandat + vendeur + adresse + total).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from orion.events import Event, EventBus
from orion.platform.alerts import Alert, AlertRouter, Level
from orion.platform.history import History
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.store import Migration, Store, dumps, loads
from orion.purchase import providers as pv
from orion.purchase.evaluate import evaluate
from orion.purchase.models import (
    ACTIVE, AWAITING_AUTH, AWAITING_CONFIRMATION, BLOCKED, CANCELLED, CONFIRMED, EXHAUSTED, EXPIRED, FAILED,
    MANDATE_STATES, OPEN_ORDERS, ORDER_STATES, PAYING, PLACED, REFUSED, REVOKED, Mandate, MandateError, Order,
    contains_card_data,
)
from orion.watch.engine import WATCH_FOUND

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE purchase_mandates (id TEXT PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL,
                                        updated_at REAL NOT NULL);
        CREATE TABLE purchase_orders (id TEXT PRIMARY KEY, mandate_id TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
                                      state TEXT NOT NULL, total REAL NOT NULL, at REAL NOT NULL, data TEXT NOT NULL);
        CREATE TABLE purchase_opportunities (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                                             mandate_id TEXT NOT NULL, title TEXT NOT NULL, seller TEXT NOT NULL,
                                             total REAL, allowed INTEGER NOT NULL, checks TEXT NOT NULL);
        CREATE TABLE purchase_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)
    """),
)


class PurchaseError(PermissionError):
    pass


@dataclass(frozen=True)
class PurchaseSettings:
    level: int = 1
    sandbox: bool = True
    allow_level3: bool = False
    per_purchase_max: float = 0.0
    period_max: float = 0.0
    period_days: int = 30


class PurchaseEngine(BaseModule):
    name, title, requires = "purchase", "PURCHASE", ("watch",)

    def __init__(self, store: Store, payment: pv.PaymentProvider, merchant: pv.Merchant, *,
                 settings: PurchaseSettings | None = None, alerts: AlertRouter | None = None,
                 events: EventBus | None = None, history: History | None = None,
                 clock: Callable[[], float] = time.time):
        self.settings = settings or PurchaseSettings()
        if not self.settings.sandbox or not payment.sandbox or not merchant.sandbox:
            raise PurchaseError("aucun paiement réel dans cette version : bac à sable seulement")
        self._store, self._payment, self._merchant = store, payment, merchant
        self._alerts, self._events, self._clock = alerts, events, clock
        self._history = history or History(store, clock)
        self._lock = threading.RLock()
        self._unsubscribe = None
        store.migrate("purchase", MIGRATIONS)

    # --- Cycle de vie et arrêt global ------------------------------------------------------------------------

    def start(self) -> None:
        if self._events is not None:
            self._unsubscribe = self._events.subscribe(WATCH_FOUND, self.on_found)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def stopped(self) -> bool:
        row = self._store.one("SELECT value FROM purchase_settings WHERE key = 'stopped'")
        return bool(loads(row["value"], False)) if row else False

    def set_stopped(self, stopped: bool, by: str) -> bool:
        """Arrêt global des achats automatiques (et de toute commande en cours non payée)."""
        self._store.write("INSERT INTO purchase_settings (key, value) VALUES ('stopped', ?) ON CONFLICT(key) DO "
                          "UPDATE SET value = excluded.value", (dumps(bool(stopped)),))
        self._history.record("purchase", "", "arrêt global" if stopped else "reprise", {"par": by})
        if stopped:
            for order in self.orders():
                if order["state"] in (AWAITING_CONFIRMATION, CONFIRMED):
                    self.cancel(order["id"], "arrêt global")
        return bool(stopped)

    # --- Mandats ---------------------------------------------------------------------------------------------

    def create_mandate(self, data: dict) -> Mandate:
        if not isinstance(data, dict):
            raise MandateError("mandat attendu")
        if contains_card_data(data):
            raise MandateError("données de carte bancaire refusées : ORION n'en garde jamais")
        allowed = set(Mandate.__dataclass_fields__) - {"id", "created_at", "state", "spent", "reserved", "purchases"}
        mandate = Mandate(**{k: v for k, v in data.items() if k in allowed}, created_at=self._clock())
        self._save_mandate(mandate)
        self._history.record("purchase", mandate.id, "mandat créé", {"produit": mandate.product,
                                                                     "prix_max": mandate.max_price,
                                                                     "budget": mandate.total_budget,
                                                                     "niveau": mandate.level})
        return mandate

    def mandate(self, mandate_id: str) -> Mandate | None:
        row = self._store.one("SELECT data FROM purchase_mandates WHERE id = ?", (mandate_id,))
        return Mandate.from_dict(loads(row["data"], {})) if row else None

    def mandates(self) -> list[Mandate]:
        return [Mandate.from_dict(loads(r["data"], {})) for r in
                self._store.query("SELECT data FROM purchase_mandates ORDER BY updated_at DESC")]

    def _save_mandate(self, mandate: Mandate) -> None:
        self._store.write("INSERT INTO purchase_mandates (id, state, data, updated_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data, "
                          "updated_at = excluded.updated_at", (mandate.id, mandate.state, dumps(mandate.as_dict()),
                                                               self._clock()))

    def revoke(self, mandate_id: str, by: str = "utilisateur") -> Mandate:
        """Révocation immédiate : plus aucun achat ; les commandes non payées sont annulées."""
        with self._lock:
            mandate = self.mandate(mandate_id)
            if mandate is None:
                raise KeyError(mandate_id)
            if mandate.state != REVOKED:
                MANDATE_STATES.check(mandate.state, REVOKED)
                mandate.state = REVOKED
                self._save_mandate(mandate)
                self._history.record("purchase", mandate_id, "mandat révoqué", {"par": by})
            for order in self.orders():
                if order["mandate_id"] == mandate_id and order["state"] in (AWAITING_CONFIRMATION, CONFIRMED):
                    self.cancel(order["id"], "mandat révoqué")
            return mandate

    def _refresh(self, mandate: Mandate) -> Mandate:
        if mandate.state == ACTIVE and self._clock() >= mandate.expires_at:
            mandate.state = EXPIRED
            self._save_mandate(mandate)
            self._history.record("purchase", mandate.id, "mandat expiré", {})
        return mandate

    # --- Opportunités ----------------------------------------------------------------------------------------

    def on_found(self, event: Event) -> None:
        payload = dict(event.payload)
        if payload.get("kind") not in ("price", "availability"):
            return  # un événement ou une annonce ne s'achète pas
        offer = {**payload, "titre": payload.get("title") or payload.get("titre"), "url": payload.get("url", "")}
        self.consider(offer)

    def consider(self, offer: dict, market: float | None = None) -> list[dict]:
        """Une offre signalée : vérifiée contre chaque mandat actif du même produit ; rend les commandes préparées."""
        prepared = []
        for mandate in self.mandates():
            mandate = self._refresh(mandate)
            if mandate.state != ACTIVE:
                continue
            verdict = evaluate(mandate, offer, self._clock(), market)
            total = None if offer.get("prix") is None or offer.get("livraison") is None \
                else round(offer["prix"] + offer["livraison"], 2)
            self._store.write("INSERT INTO purchase_opportunities (at, mandate_id, title, seller, total, allowed, "
                              "checks) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (self._clock(), mandate.id, str(offer.get("titre") or "")[:200],
                               str(offer.get("vendeur") or ""), total, int(verdict.allowed),
                               dumps(verdict.as_list())))
            if not verdict.allowed:
                continue
            if self.stopped:
                self._history.record("purchase", mandate.id, "offre ignorée", {"raison": "arrêt global"})
                continue
            order = self._prepare(mandate, offer, total)
            if order is not None:
                prepared.append(order.as_dict())
        return prepared

    def _prepare(self, mandate: Mandate, offer: dict, total: float) -> Order | None:
        key = f"{mandate.id}|{str(offer.get('vendeur')).lower()}|{offer.get('url', '')}|{total:.2f}"
        with self._lock:
            if self._store.one("SELECT 1 FROM purchase_orders WHERE key = ?", (key,)):
                return None  # déjà préparée : jamais deux commandes pour la même offre
            safe_offer = {k: offer.get(k) for k in ("vendeur", "titre", "prix", "livraison", "disponible", "état",
                                                     "garantie_mois", "retour_jours", "vendeur_note", "url",
                                                     "place_de_marché")}
            order = Order(mandate.id, key, safe_offer, total, created_at=self._clock())
            self._save_order(order, "préparée")
        level = self.effective_level(mandate, total)
        if level == 1:
            self._move(order, AWAITING_CONFIRMATION, "confirmation demandée")
            self._alert(order, "Achat à confirmer", f"{offer.get('titre')} chez {offer.get('vendeur')} : {total:.2f} € "
                        "livraison comprise. Confirmez ou refusez dans ORION Control.", Level.HIGH)
            return order
        self._move(order, CONFIRMED, f"automatique (niveau {level})")
        return self.place_order(order.id)

    def effective_level(self, mandate: Mandate, total: float) -> int:
        """Niveau réellement permis pour cet achat : le plus bas entre le réglage et le mandat ; le niveau 3 exige en
        plus ``allow_level3`` et une enveloppe (plafond par achat et par période) respectée."""
        s = self.settings
        level = min(s.level, mandate.level)
        if level >= 3:
            if not s.allow_level3 or s.per_purchase_max <= 0 or s.period_max <= 0 or total > s.per_purchase_max \
                    or self.spent_in_period() + total > s.period_max:
                level = 2
        if level >= 2 and self.stopped:
            level = 1
        return max(1, level)

    def spent_in_period(self) -> float:
        since = self._clock() - self.settings.period_days * 86400
        row = self._store.one("SELECT COALESCE(SUM(total), 0) AS s FROM purchase_orders WHERE state = ? AND at > ?",
                              (PLACED, since))
        return float(row["s"]) if row else 0.0

    # --- Commande --------------------------------------------------------------------------------------------

    def confirm(self, order_id: str, by: str = "utilisateur") -> dict:
        if by != "utilisateur":
            raise PurchaseError("seul l'utilisateur confirme un achat")
        order = self._order(order_id)
        self._move(order, CONFIRMED, "confirmée par l'utilisateur")
        return self.place_order(order_id).as_dict()

    def cancel(self, order_id: str, reason: str = "annulée par l'utilisateur") -> dict:
        with self._lock:
            order = self._order(order_id)
            if order.state in OPEN_ORDERS:
                if order.state == AWAITING_AUTH and order.payment_ref:
                    self._payment.void(order.payment_ref)
                self._release(order)
                self._move(order, CANCELLED, reason)
            return order.as_dict()

    def _blocked(self, order: Order) -> str:
        """Raison d'arrêter maintenant (arrêt global, mandat révoqué ou expiré), ou ""."""
        if self.stopped:
            return "arrêt global des achats"
        mandate = self.mandate(order.mandate_id)
        if mandate is None:
            return "mandat introuvable"
        mandate = self._refresh(mandate)
        if mandate.state != ACTIVE:
            return {REVOKED: "mandat révoqué", EXPIRED: "mandat expiré", EXHAUSTED: "mandat épuisé"}.get(
                mandate.state, mandate.state)
        return ""

    def place_order(self, order_id: str) -> Order:
        with self._lock:
            order = self._order(order_id)
            if order.state != CONFIRMED:
                raise PurchaseError(f"commande {order.state} : rien à exécuter")
            if reason := self._blocked(order):
                self._move(order, CANCELLED, reason)
                return order
            quote = self._merchant.quote(order.offer)  # prix et disponibilité du moment
            if reason := self._blocked(order):  # révocation pendant le devis
                self._move(order, CANCELLED, reason)
                return order
            mandate = self.mandate(order.mandate_id)
            live = {**quote.as_offer(), "url": order.offer.get("url"), "place_de_marché":
                    order.offer.get("place_de_marché")}
            verdict = evaluate(mandate, live, self._clock())
            total = None if quote.price is None or quote.shipping is None else round(quote.price + quote.shipping, 2)
            if not verdict.allowed or total is None or total > order.total + 0.005:
                why = "; ".join(verdict.blocking) or "prix final plus élevé qu'annoncé"
                self._move(order, BLOCKED, f"devis du moment refusé : {why}")
                self._alert(order, "Achat bloqué", f"Commande non passée : {why}.", Level.NORMAL)
                return order
            mandate.reserved = round(mandate.reserved + total, 2)  # budget réservé avant de payer
            self._save_mandate(mandate)
            self._move(order, PAYING, "paiement demandé")
            result = self._payment.authorize(order.id, int(round(total * 100)), "EUR", mandate.id)
            order.payment_ref = result.reference
            return self._after_payment(order, quote, result)

    def _after_payment(self, order: Order, quote: pv.Quote, result: pv.PaymentResult) -> Order:
        if result.status == pv.REQUIRES_ACTION:
            self._move(order, AWAITING_AUTH, "authentification forte requise")
            self._alert(order, "Authentification bancaire requise", "Le paiement attend votre authentification "
                        "(3-D Secure) auprès de votre banque ; ORION ne peut pas la faire à votre place.", Level.HIGH)
            return order
        if result.status != pv.APPROVED:
            self._release(order)
            self._move(order, REFUSED if result.status == pv.DECLINED else FAILED, result.message or result.status)
            self._alert(order, "Paiement refusé", f"Achat non effectué : {result.message or result.status}.",
                        Level.NORMAL)
            return order
        if reason := self._blocked(order):  # révocation pendant le paiement : rien n'est commandé
            self._payment.void(order.payment_ref)
            self._release(order)
            self._move(order, CANCELLED, f"{reason} (paiement annulé)")
            return order
        placed = self._merchant.place(order.id, quote, order.payment_ref)
        if not placed.ok:
            self._payment.void(order.payment_ref)
            self._release(order)
            self._move(order, FAILED, f"marchand : {placed.message}")
            self._alert(order, "Commande échouée", f"Le marchand a refusé la commande ({placed.message}) ; le "
                        "paiement est annulé.", Level.NORMAL)
            return order
        order.merchant_ref = placed.order_number
        mandate = self.mandate(order.mandate_id)
        mandate.reserved = max(0.0, round(mandate.reserved - order.total, 2))
        mandate.spent = round(mandate.spent + order.total, 2)
        mandate.purchases += 1
        if mandate.purchases >= mandate.max_purchases or mandate.remaining < mandate.max_price:
            mandate.state = EXHAUSTED
        self._save_mandate(mandate)
        self._move(order, PLACED, f"commande {placed.order_number}")
        self._alert(order, "Achat effectué", f"{order.offer.get('titre')} commandée chez {order.offer.get('vendeur')} "
                    f"pour {order.total:.2f} € (commande {placed.order_number}).", Level.HIGH)
        return order

    def complete_auth(self, order_id: str, authenticated: bool, by: str = "utilisateur") -> dict:
        """Retour de l'authentification forte faite par l'utilisateur auprès de sa banque (bac à sable)."""
        if by != "utilisateur":
            raise PurchaseError("seul l'utilisateur s'authentifie auprès de sa banque")
        with self._lock:
            order = self._order(order_id)
            if order.state != AWAITING_AUTH:
                raise PurchaseError("aucune authentification en attente")
            result = self._payment.complete(order.payment_ref, authenticated)
            quote = self._merchant.quote(order.offer)
            return self._after_payment(order, quote, result).as_dict()

    def _release(self, order: Order) -> None:
        if order.state not in (PAYING, AWAITING_AUTH):
            return
        mandate = self.mandate(order.mandate_id)
        if mandate is not None:
            mandate.reserved = max(0.0, round(mandate.reserved - order.total, 2))
            self._save_mandate(mandate)

    # --- Persistance et alertes ------------------------------------------------------------------------------

    def _order(self, order_id: str) -> Order:
        row = self._store.one("SELECT data FROM purchase_orders WHERE id = ?", (order_id,))
        if row is None:
            raise KeyError(order_id)
        return Order(**loads(row["data"], {}))

    def _save_order(self, order: Order, why: str) -> None:
        order.history.append([self._clock(), order.state, why])
        if contains_card_data(order.as_dict()):
            raise PurchaseError("données de carte bancaire détectées : commande refusée")
        self._store.write("INSERT INTO purchase_orders (id, mandate_id, key, state, total, at, data) VALUES "
                          "(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET state = excluded.state, "
                          "data = excluded.data", (order.id, order.mandate_id, order.key, order.state, order.total,
                                                   self._clock(), dumps(order.as_dict())))

    def _move(self, order: Order, state: str, why: str) -> None:
        ORDER_STATES.check(order.state, state)
        order.state, order.reason = state, why
        self._save_order(order, why)
        self._history.record("purchase", order.id, state, {"raison": why, "total": order.total,
                                                          "mandat": order.mandate_id})

    def _alert(self, order: Order, title: str, message: str, level: Level) -> None:
        if self._alerts is not None:
            self._alerts.deliver(Alert("purchase", title, message, level, key=f"{order.id}:{order.state}",
                                       call_allowed=level >= Level.HIGH))

    def orders(self) -> list[dict]:
        return [loads(r["data"], {}) for r in self._store.query("SELECT data FROM purchase_orders ORDER BY at DESC "
                                                                 "LIMIT 200")]

    def opportunities(self) -> list[dict]:
        rows = self._store.query("SELECT * FROM purchase_opportunities ORDER BY at DESC, id DESC LIMIT 200")
        return [{**r, "allowed": bool(r["allowed"]), "checks": loads(r["checks"], [])} for r in rows]

    # --- ORION Control ---------------------------------------------------------------------------------------

    def status(self) -> dict:
        mandates = self.mandates()
        return {"niveau": self.settings.level, "bac_à_sable": True, "arrêt_global": self.stopped,
                "mandats_actifs": sum(m.state == ACTIVE for m in mandates),
                "budget_autorisé": round(sum(m.total_budget for m in mandates if m.state == ACTIVE), 2),
                "dépensé": round(sum(m.spent for m in mandates), 2),
                "en_attente": sum(o["state"] in (AWAITING_CONFIRMATION, AWAITING_AUTH) for o in self.orders())}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        try:
            if not parts and method == "GET":
                return 200, self.status()
            if parts == ["mandates"]:
                if method == "GET":
                    return 200, [m.as_dict() | {"remaining": m.remaining} for m in self.mandates()]
                if method == "POST":
                    return 201, self.create_mandate(data).as_dict()
            if len(parts) == 3 and parts[0] == "mandates" and parts[2] == "revoke" and method == "POST":
                return 200, self.revoke(parts[1]).as_dict()
            if parts == ["orders"] and method == "GET":
                return 200, self.orders()
            if len(parts) == 3 and parts[0] == "orders" and method == "POST":
                if parts[2] == "confirm":
                    return 200, self.confirm(parts[1])
                if parts[2] == "cancel":
                    return 200, self.cancel(parts[1])
                if parts[2] == "auth":  # bac à sable : résultat de l'authentification faite par l'utilisateur
                    return 200, self.complete_auth(parts[1], bool((data or {}).get("authenticated")))
            if parts == ["opportunities"] and method == "GET":
                return 200, self.opportunities()
            if parts == ["stop"] and method == "POST":
                return 200, {"arrêt_global": self.set_stopped(True, "utilisateur")}
            if parts == ["resume"] and method == "POST":
                return 200, {"arrêt_global": self.set_stopped(False, "utilisateur")}
            if parts == ["history"] and method == "GET":
                return 200, self._history.list("purchase", limit=200)
        except KeyError:
            return 404, {"status": "error", "error": "not_found"}
        except PurchaseError as exc:
            return 403, {"status": "error", "error": "forbidden", "message": str(exc)}
        except ValueError as exc:
            return 400, {"status": "error", "error": "invalid", "message": str(exc)}
        return NOT_FOUND
