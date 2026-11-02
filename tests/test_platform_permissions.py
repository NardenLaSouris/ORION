"""CORE FOUNDATION F1 : moteur central des permissions, interrupteur d'autonomie (actif, suspendu, arrêt d'urgence),
registre d'actions en ajout seul et chaîné, raccordements (Core des outils, alertes, planificateur, achats, EVOLVE,
appels, voix, ORION Control). Données factices uniquement."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform import Store  # noqa: E402
from orion.platform.alerts import Alert, AlertRouter, Level  # noqa: E402
from orion.platform.autonomy import (  # noqa: E402
    CONFIRMATION_PHRASE, CONTROL, VOICE, AutonomyError, AutonomyState, AutonomySwitch,
)
from orion.platform.foundation import Foundation, build_foundation  # noqa: E402
from orion.platform.ledger import ActionLedger  # noqa: E402
from orion.platform.permissions import Capability, Decision, PermissionDenied, PermissionEngine  # noqa: E402
from orion.platform.scheduler import Scheduler  # noqa: E402

NOW = 1_800_000_000.0
DAY = 86400.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def engine(clock):
    return PermissionEngine(Store(), clock=clock)


def activate(engine):
    engine.autonomy.change(AutonomyState.ACTIVE, CONTROL, confirmation=CONFIRMATION_PHRASE, session="control@test")


# --- Interrupteur d'autonomie --------------------------------------------------------------------------------

def test_autonomy_is_suspended_by_default_and_survives_a_restart(tmp_path):
    path = tmp_path / "ops.db"
    first = PermissionEngine(Store(path))
    assert first.autonomy.state() is AutonomyState.SUSPENDED
    activate(first)
    first.autonomy.emergency_stop(VOICE, "essai")
    again = PermissionEngine(Store(path))
    assert again.autonomy.state() is AutonomyState.EMERGENCY_STOP


def test_voice_can_suspend_or_stop_but_never_reactivate(engine):
    activate(engine)
    assert engine.autonomy.suspend(VOICE) is AutonomyState.SUSPENDED
    with pytest.raises(AutonomyError, match="seul ORION Control"):
        engine.autonomy.change(AutonomyState.ACTIVE, VOICE, confirmation=CONFIRMATION_PHRASE, session="x")
    engine.autonomy.emergency_stop(VOICE)
    assert engine.autonomy.suspend(VOICE) is AutonomyState.EMERGENCY_STOP  # suspendre ne lève pas un arrêt d'urgence
    with pytest.raises(AutonomyError):
        engine.autonomy.change(AutonomyState.SUSPENDED, VOICE)
    assert engine.autonomy.state() is AutonomyState.EMERGENCY_STOP


@pytest.mark.parametrize("confirmation, session, why", [
    ("", "control@test", "confirmation explicite"),
    ("oui", "control@test", "confirmation explicite"),
    (CONFIRMATION_PHRASE, "", "non authentifiée"),
])
def test_reactivation_needs_an_authenticated_control_session_and_the_exact_phrase(engine, confirmation, session, why):
    with pytest.raises(AutonomyError, match=why):
        engine.autonomy.change(AutonomyState.ACTIVE, CONTROL, confirmation=confirmation, session=session)
    assert engine.autonomy.state() is AutonomyState.SUSPENDED
    refused = engine.ledger.list(5)[0]
    assert refused["decision"] == "deny" and refused["action"] == "autonomy:active"


def test_unknown_origin_cannot_change_the_switch(engine):
    with pytest.raises(AutonomyError):
        engine.autonomy.emergency_stop("module:watch")


# --- Décisions du moteur central ----------------------------------------------------------------------------

def test_conversation_and_control_are_never_blocked_by_the_switch(engine):
    engine.autonomy.emergency_stop(VOICE)
    assert engine.decide("user", Capability.CONTROL_DEVICE).allowed
    assert engine.decide("admin", Capability.WRITE_DATA).allowed


def test_suspended_autonomy_denies_module_initiatives_but_not_programmed_tasks(engine):
    assert engine.decide("module:news", Capability.NOTIFY).decision is Decision.DENY
    assert engine.decide("module:news", Capability.READ).allowed
    assert engine.decide("delegated:watch:abc", Capability.NOTIFY).allowed
    activate(engine)
    assert engine.decide("module:news", Capability.NOTIFY).allowed  # phase 1 : notification
    assert engine.decide("module:news", Capability.CONTROL_DEVICE).decision is Decision.DENY  # hors phase 1


def test_emergency_stop_denies_every_autonomous_action_including_programmed_tasks(engine):
    activate(engine)
    engine.autonomy.emergency_stop(CONTROL)
    for principal in ("module:watch", "delegated:routine", "delegated:watch:abc"):
        for capability in Capability:
            assert engine.decide(principal, capability).decision is Decision.DENY


def test_unreadable_autonomy_state_fails_safe(engine):
    activate(engine)
    engine.autonomy._store.write("UPDATE autonomy_state SET state = 'peut-être' WHERE id = 1")
    assert engine.autonomy.state() is None
    verdict = engine.decide("delegated:routine", Capability.NOTIFY)
    assert verdict.decision is Decision.DENY and "indéterminé" in verdict.reason
    assert engine.suspended_for("delegated:scheduler") is True
    assert engine.decide("user", Capability.NOTIFY).allowed


@pytest.mark.parametrize("capability", [Capability.SPEND, Capability.DESTRUCTIVE, Capability.EXTERNAL_MESSAGE])
def test_sensitive_actions_always_ask_an_autonomous_origin_without_a_bounded_grant(engine, capability):
    activate(engine)
    for principal in ("module:purchase", "delegated:purchase:m1"):
        assert engine.decide(principal, capability).decision is Decision.ASK
    assert engine.decide("delegated:routine", capability, confirmed=True).allowed  # votre confirmation


def test_code_change_only_for_evolve_in_its_sandbox(engine):
    activate(engine)
    assert engine.decide("module:watch", Capability.CODE_CHANGE, {"sandbox": True}).decision is Decision.DENY
    assert engine.decide("module:evolve", Capability.CODE_CHANGE, {"sandbox": False}).decision is Decision.DENY
    assert engine.decide("module:evolve", Capability.CODE_CHANGE, {"sandbox": True}).decision is Decision.ASK
    assert engine.decide("module:evolve", Capability.CODE_CHANGE, {"sandbox": True}, confirmed=True).allowed


@pytest.mark.parametrize("principal", ["", "root", "module:", "module:Watch", "untrusted:mail", "user:x", None])
def test_unknown_principal_is_denied(engine, principal):
    activate(engine)
    assert engine.decide(principal, Capability.READ).decision is Decision.DENY


def test_engine_error_fails_safe(engine, monkeypatch):
    activate(engine)
    monkeypatch.setattr(engine.grants, "active_for", lambda *a: 1 / 0)
    verdict = engine.decide("module:watch", Capability.NOTIFY)
    assert verdict.decision is Decision.DENY and "erreur" in verdict.reason


def test_an_autonomous_action_that_cannot_be_recorded_does_not_happen(engine, monkeypatch):
    activate(engine)
    monkeypatch.setattr(engine.ledger, "append", lambda *a, **k: 1 / 0)
    assert engine.decide("module:watch", Capability.NOTIFY).decision is Decision.DENY
    assert engine.decide("user", Capability.NOTIFY).allowed  # la conversation continue


# --- Autorisations ------------------------------------------------------------------------------------------

def test_a_module_cannot_grant_itself_a_capability(engine):
    with pytest.raises(PermissionError):
        engine.grants.grant("module:watch", Capability.CONTROL_DEVICE, granted_by="module:watch")
    with pytest.raises(PermissionError):
        engine.grants.grant("module:watch", Capability.CONTROL_DEVICE, granted_by=VOICE)
    gate = engine.gate("module:watch")
    assert not hasattr(gate, "grants") and not hasattr(gate, "_grants")
    with pytest.raises(AttributeError):
        gate.principal_other = "user"  # principal fixe (__slots__)
    with pytest.raises(ValueError):
        engine.gate("user")  # un module ne reçoit jamais l'identité de la conversation
    assert engine.grants.list() == []


def test_sensitive_grants_must_be_bounded(engine, clock):
    with pytest.raises(ValueError, match="bornée"):
        engine.grants.grant("delegated:purchase:*", Capability.SPEND, {"max_amount": 50}, granted_by=CONTROL)
    with pytest.raises(ValueError, match="bornée"):
        engine.grants.grant("delegated:purchase:*", Capability.SPEND, {}, clock() + DAY, granted_by=CONTROL)
    with pytest.raises(ValueError, match="EVOLVE"):
        engine.grants.grant("module:watch", Capability.CODE_CHANGE, {"per_day": 1}, clock() + DAY, granted_by=CONTROL)


def test_grant_limits_expiry_and_revocation(engine, clock):
    activate(engine)
    grant = engine.grants.grant("delegated:purchase:*", Capability.SPEND, {"max_amount": 50, "per_day": 2},
                                clock() + DAY, granted_by=CONTROL)
    assert engine.decide("delegated:purchase:m1", Capability.SPEND, {"amount": 40}).allowed
    assert engine.decide("delegated:purchase:m1", Capability.SPEND, {"amount": 60}).decision is Decision.ASK
    assert engine.decide("delegated:purchase:m1", Capability.SPEND).decision is Decision.ASK  # montant absent
    assert engine.decide("delegated:purchase:m1", Capability.SPEND, {"amount": 10}).allowed
    assert engine.decide("delegated:purchase:m1", Capability.SPEND, {"amount": 10}).decision is Decision.ASK  # 2/jour
    clock.t += DAY + 1
    assert engine.decide("delegated:purchase:m1", Capability.SPEND, {"amount": 10}).decision is Decision.ASK  # expirée
    assert engine.grants.revoke(grant.id, by=VOICE) is True
    assert engine.grants.list() == []


def test_emergency_stop_overrides_grants(engine, clock):
    activate(engine)
    engine.grants.grant("module:lights", Capability.CONTROL_DEVICE, {"devices": ["salon"]}, clock() + DAY,
                        granted_by=CONTROL)
    assert engine.decide("module:lights", Capability.CONTROL_DEVICE, {"device": "salon"}).allowed
    assert engine.decide("module:lights", Capability.CONTROL_DEVICE, {"device": "cuisine"}).decision is Decision.DENY
    engine.autonomy.emergency_stop(VOICE)
    assert engine.decide("module:lights", Capability.CONTROL_DEVICE, {"device": "salon"}).decision is Decision.DENY


# --- Registre d'actions ---------------------------------------------------------------------------------------

def test_every_decision_is_recorded_and_the_chain_verifies(engine):
    activate(engine)
    engine.decide("module:watch", Capability.NOTIFY, action="alert:watch")
    engine.decide("module:watch", Capability.SPEND)
    entries = engine.ledger.list(10)
    assert [e["decision"] for e in entries[:2]] == ["ask", "allow"]
    integrity = engine.ledger.verify()
    assert integrity.ok and integrity.count == len(engine.ledger.list(100))


def test_ledger_is_append_only_in_the_database(engine):
    engine.decide("module:watch", Capability.NOTIFY)
    with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
        engine.ledger._store.write("UPDATE action_ledger SET decision = 'allow'")
    with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
        engine.ledger._store.write("DELETE FROM action_ledger")
    assert not hasattr(engine.ledger, "delete") and not hasattr(engine.ledger, "update")


@pytest.mark.parametrize("tamper, problem", [
    ("UPDATE action_ledger SET decision = 'allow' WHERE id = 2", "contenu modifié"),
    ("DELETE FROM action_ledger WHERE id = 2", "ligne supprimée"),
    ("UPDATE action_ledger SET prev_hash = hash WHERE id = 3", "chaînage rompu"),
])
def test_tampering_is_detected(tmp_path, tamper, problem):
    path = tmp_path / "ops.db"
    ledger = ActionLedger(Store(path))
    for decision in ("allow", "deny", "ask", "allow"):
        ledger.append("module:watch", "NOTIFY", "alert", decision, "essai")
    raw = sqlite3.connect(path)  # altération hors d'ORION : déclencheurs retirés d'abord
    raw.executescript("DROP TRIGGER action_ledger_no_update; DROP TRIGGER action_ledger_no_delete;"
                      f"{tamper};")
    raw.commit()
    raw.close()
    integrity = ActionLedger(Store(path)).verify()
    assert not integrity.ok and problem in integrity.problem and integrity.broken_at in (2, 3)


def test_ledger_never_keeps_parameter_values_or_secrets(engine):
    activate(engine)
    engine.decide("module:watch", Capability.NOTIFY, {"parameters": ["message"], "password": "hunter2",
                                                      "amount": 12.5}, action="tool:send")
    text = str(engine.ledger.list(1)[0])
    assert "hunter2" not in text and "password" not in text and "12.5" in text


# --- Planificateur : l'arrêt d'urgence passe avant la file -------------------------------------------------

def test_emergency_stop_halts_the_scheduler_between_two_due_jobs(engine, clock):
    activate(engine)
    ran = []
    scheduler = Scheduler(clock=clock, halted=lambda: engine.suspended_for("delegated:scheduler"))
    scheduler.add("module:a", 60, lambda: (ran.append("a"), engine.autonomy.emergency_stop(VOICE)), clock())
    scheduler.add("module:b", 60, lambda: ran.append("b"), clock() + 1)
    clock.t += 2
    assert scheduler.tick() == ["module:a"] and ran == ["a"]
    assert scheduler.tick() == [] and ran == ["a"]  # b reste due, ne part pas
    engine.autonomy.change(AutonomyState.ACTIVE, CONTROL, confirmation=CONFIRMATION_PHRASE, session="control@test")
    assert scheduler.tick() == ["module:b"]


def test_suspension_keeps_programmed_scheduler_tasks(engine, clock):
    scheduler = Scheduler(clock=clock, halted=lambda: engine.suspended_for("delegated:scheduler"))
    scheduler.add("watch:x", 60, lambda: None, clock())
    assert engine.autonomy.state() is AutonomyState.SUSPENDED and scheduler.tick() == ["watch:x"]


def test_unreadable_halt_state_stops_the_scheduler(clock):
    scheduler = Scheduler(clock=clock, halted=lambda: 1 / 0)
    scheduler.add("a", 60, lambda: pytest.fail("ne doit pas tourner"), clock())
    assert scheduler.tick() == []


# --- Alertes ------------------------------------------------------------------------------------------------

def router(engine, said):
    return AlertRouter(Store(), presence=lambda: True, speak=lambda text: said.append(text) or True,
                       permit=engine.decide, now=lambda: __import__("datetime").datetime(2026, 11, 2, 12, 0))


def test_alerts_go_through_the_engine(engine):
    said = []
    alerts = router(engine, said)
    blocked = alerts.deliver(Alert("news", "Bulletin", "Trois titres."))
    assert not blocked.delivered and "autonomie suspendue" in blocked.skipped["tout"]
    assert alerts.history(1)[0]["status"] == "bloquée"
    assert alerts.deliver(Alert("watch", "RTX 5090", "Prix atteint.", origin="delegated:watch:w1")).delivered
    engine.autonomy.emergency_stop(VOICE)
    assert not alerts.deliver(Alert("watch", "RTX 5090", "Encore.", key="k2", origin="delegated:watch:w1")).delivered
    assert said == ["Prix atteint."]


def test_watch_alerts_are_programmed_tasks():
    source = (ROOT / "orion" / "watch" / "engine.py").read_text(encoding="utf-8")
    assert 'origin=f"delegated:watch:{watch.id}"' in source


# --- Core des outils (routines, conversation) ---------------------------------------------------------------

def tool_core(foundation):
    from orion.tools import ConfirmationManager, PermissionManager, Risk, Tool, ToolCore, ToolRegistry

    registry = ToolRegistry()
    registry.register(Tool("lights_on", "Allume.", {}, {}, Risk.SAFE, lambda: {"ok": True}))
    registry.register(Tool("delete_all", "Supprime.", {}, {}, Risk.CONFIRMATION_REQUIRED, lambda: {"ok": True}))
    gate = PermissionManager()
    return ToolCore(registry, foundation.wrap(gate) if foundation else gate,
                    ConfirmationManager(("oui",), ("non",)), timeout=2.0)


def call(tool):
    return {"type": "tool_call", "tool": tool, "parameters": {}}


def test_routines_stop_on_emergency_while_conversation_continues():
    from orion.tools import CONFIRM, DONE, REJECTED

    foundation = Foundation(Store())
    core = tool_core(foundation)
    assert core.submit(call("lights_on"), origin="routine").status == DONE  # suspendue : vos routines continuent
    assert core.submit(call("delete_all"), origin="routine").status == CONFIRM
    foundation.autonomy.emergency_stop(VOICE)
    assert core.submit(call("lights_on"), origin="routine").status == REJECTED
    assert core.submit(call("lights_on")).status == DONE  # conversation
    actions = [e["action"] for e in foundation.ledger.list(10)]
    assert "tool:lights_on" in actions


def test_without_foundation_tool_decisions_are_unchanged():
    from orion.tools import CONFIRM, DONE

    core = tool_core(None)
    assert core.submit(call("lights_on"), origin="routine").status == DONE
    assert core.submit(call("delete_all"), origin="routine").status == CONFIRM


# --- Voix -------------------------------------------------------------------------------------------------

def test_voice_tools_suspend_and_stop_and_none_reactivates():
    foundation = Foundation(Store())
    tools = {t.name: t for t in foundation.tools()}
    assert set(tools) == {"suspend_autonomy", "emergency_stop"}
    assert all("réactiv" not in t.description.lower() for t in tools.values())
    result = tools["emergency_stop"].run()
    assert result["state"] == "emergency_stop" and "ORION Control" in tools["emergency_stop"].say(result)


# --- ORION Control (API) ---------------------------------------------------------------------------------

def test_control_api_reactivation_requires_the_phrase():
    foundation = Foundation(Store())
    status, body = foundation.admin("POST", ["autonomy"], {}, {"state": "active"}, session="control@1.2.3.4")
    assert status == 403 and foundation.autonomy.state() is AutonomyState.SUSPENDED
    status, body = foundation.admin("POST", ["autonomy"], {}, {"state": "active", "confirmation": CONFIRMATION_PHRASE},
                                    session="control@1.2.3.4")
    assert status == 200 and body["state"] == "active"
    status, body = foundation.admin("POST", ["autonomy"], {}, {"state": "emergency_stop"}, session="control@1.2.3.4")
    assert body["state"] == "emergency_stop"
    status, body = foundation.admin("GET", ["ledger", "verify"], {}, None, session="control@1.2.3.4")
    assert body["ok"] and body["count"] >= 3


def test_platform_is_disabled_by_default():
    from orion.config import load_config

    cfg = load_config(ROOT / "config.toml")
    assert cfg.platform.enabled is False and build_foundation(cfg) is None


# --- Achats, EVOLVE, appels ----------------------------------------------------------------------------------

def test_automatic_purchase_without_grant_waits_for_confirmation_and_emergency_blocks_payment():
    from orion.purchase import PurchaseEngine, PurchaseSettings, SandboxMerchant, SandboxPayment

    from tests.test_purchase import MANDATE, offer

    foundation = Foundation(Store())
    activate(foundation.permissions)
    merchant = SandboxMerchant()
    engine = PurchaseEngine(Store(), SandboxPayment(), merchant, settings=PurchaseSettings(level=2),
                            clock=lambda: NOW, gate=foundation.permissions.gate("delegated:purchase"))
    engine.create_mandate({**MANDATE, "level": 2})
    order = engine.consider(offer())[0]
    assert order["state"] == "AWAITING_CONFIRMATION" and not merchant.placed  # pas d'autorisation bornée
    foundation.autonomy.emergency_stop(VOICE)
    assert engine.confirm(order["id"])["state"] == "BLOCKED" and not merchant.placed


def test_evolve_patch_needs_the_engine(tmp_path):
    from orion.evolve import EvolveEngine, EvolveSettings, PolicyError

    foundation = Foundation(Store())
    foundation.autonomy.emergency_stop(VOICE)
    evolve = EvolveEngine(Store(), workspace=None, settings=EvolveSettings(level=2),
                          gate=foundation.permissions.gate("module:evolve"), logs=lambda: [])
    with pytest.raises(PolicyError, match="arrêt d'urgence"):
        evolve.patch("x", [], "essai", confirmed=True)


def test_programmed_calls_are_refused_on_emergency_stop():
    from orion.call import CallRefused
    from orion.call.manager import CallManager, CallSettings

    foundation = Foundation(Store())
    manager = CallManager(CallSettings(device="sip:test"), None, None, lambda call: (None, None))
    manager.permit = foundation.permissions.decide
    foundation.autonomy.emergency_stop(VOICE)
    with pytest.raises(CallRefused, match="arrêt d'urgence"):
        manager.request("Règle d'appel", "routine")


def test_gate_require_raises_with_the_verdict(engine):
    gate = engine.gate("module:watch")
    with pytest.raises(PermissionDenied) as raised:
        gate.require(Capability.NOTIFY)
    assert raised.value.verdict.decision is Decision.DENY


def test_news_module_is_untouched_by_the_foundation():
    """NEWS reste isolé : aucun de ses fichiers ne référence le socle (coordination préalable exigée)."""
    for path in (ROOT / "orion" / "news").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "permissions" not in text and "autonomy" not in text and "foundation" not in text, path.name


def test_build_v3_connects_modules_to_the_foundation(tmp_path):
    from dataclasses import replace

    from orion.config import load_config
    from orion.platform.build import build_v3

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, platform=replace(cfg.platform, enabled=True, database=tmp_path / "ops.db"),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"),
                  watch=replace(cfg.watch, enabled=True), purchase=replace(cfg.purchase, enabled=True))
    foundation = build_foundation(cfg)
    modules = build_v3(cfg, foundation=foundation)
    try:
        assert modules.alerts._permit == foundation.permissions.decide
        assert modules.scheduler.halted() is False  # suspendue par défaut : les tâches programmées tournent
        foundation.autonomy.emergency_stop(VOICE)
        assert modules.scheduler.halted() is True
        assert modules.registry.get("purchase")._gate.principal == "delegated:purchase"
    finally:
        modules.stop()


def test_a_bounded_grant_lets_one_automatic_purchase_through_and_counts_it_once():
    from orion.purchase import PurchaseEngine, PurchaseSettings, SandboxMerchant, SandboxPayment

    from tests.test_purchase import MANDATE, offer

    foundation = Foundation(Store())
    activate(foundation.permissions)
    foundation.permissions.grants.grant("delegated:purchase:*", Capability.SPEND, {"max_amount": 2000, "per_day": 1},
                                        NOW + DAY, granted_by=CONTROL)
    alerts = []
    merchant = SandboxMerchant()
    engine = PurchaseEngine(Store(), SandboxPayment(), merchant, settings=PurchaseSettings(level=2),
                            clock=lambda: NOW, gate=foundation.permissions.gate("delegated:purchase"),
                            alerts=type("A", (), {"deliver": lambda _, a: alerts.append(a)})())
    mandate = engine.create_mandate({**MANDATE, "level": 2, "max_purchases": 2, "total_budget": 4100})
    assert engine.consider(offer())[0]["state"] == "PLACED" and merchant.placed  # vérifié deux fois, compté une
    assert engine.consider(offer(vendeur="Materiel", url="https://materiel.example/2"))[0]["state"] \
        == "AWAITING_CONFIRMATION"  # plafond d'un achat par jour atteint : votre confirmation
    assert alerts and all(a.origin == f"delegated:purchase:{mandate.id}" for a in alerts)
