"""CORE FOUNDATION F1 assemblé : base opérationnelle, registre d'actions, interrupteur d'autonomie, moteur de
permissions, et leurs raccordements (Core des outils, outils vocaux, API d'ORION Control).

Inactif tant que ``[platform] enabled = false`` (comportement V2 inchangé). Activé, il ne remplace rien : le Core des
outils garde ses permissions (profils, confirmations, Cedar) et le moteur central s'y ajoute ; la décision la plus
stricte l'emporte.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from orion.platform.autonomy import CONTROL, VOICE, AutonomyError, AutonomyState, AutonomySwitch
from orion.platform.ledger import ActionLedger
from orion.platform.permissions import Capability, Decision, PermissionEngine
from orion.platform.store import Store

log = logging.getLogger(__name__)

# Origines du Core des outils -> principal du moteur central.
TOOL_PRINCIPALS = {"user": "user", "admin": "admin", "routine": "delegated:routine"}


def tool_capability(tool) -> Capability:
    """Capacité d'un outil : déclarée (``tool.capability``), sinon déduite prudemment de son risque."""
    declared = getattr(tool, "capability", None)
    if declared is not None:
        return Capability(declared)
    from orion.tools.base import Risk

    return Capability.WRITE_DATA if tool.risk is Risk.SAFE else Capability.DESTRUCTIVE


class FoundationGate:
    """Même interface que ``PermissionManager.decide`` : la décision existante, puis le moteur central."""

    def __init__(self, inner, engine: PermissionEngine):
        self.inner, self.engine = inner, engine

    def __getattr__(self, name: str) -> Any:  # mode, cedar… du PolicyGate enveloppé
        return getattr(self.inner, name)

    def decide(self, user: str, tool, parameters: dict, confirmed: bool = False, origin: str = "user"):
        from orion.tools.permissions import Decision as ToolDecision, PermissionDecision

        first = self.inner.decide(user, tool, parameters, confirmed=confirmed, origin=origin)
        if first.decision is ToolDecision.DENY:
            return first
        principal = TOOL_PRINCIPALS.get(origin, f"untrusted:{origin}")
        verdict = self.engine.decide(principal, tool_capability(tool), {"parameters": sorted(parameters)},
                                     action=f"tool:{tool.name}", confirmed=confirmed)
        if verdict.decision is Decision.DENY:
            return PermissionDecision(ToolDecision.DENY, verdict.reason)
        if verdict.decision is Decision.ASK and first.decision is ToolDecision.ALLOW and not confirmed:
            return PermissionDecision(ToolDecision.REQUIRES_CONFIRMATION, verdict.reason)
        return first


class Foundation:
    def __init__(self, store: Store):
        self.store = store
        self.ledger = ActionLedger(store)
        self.autonomy = AutonomySwitch(store, self.ledger)
        self.permissions = PermissionEngine(store, ledger=self.ledger, autonomy=self.autonomy)

    def wrap(self, gate):
        return FoundationGate(gate, self.permissions)

    # --- Voix : suspendre ou arrêter, jamais réactiver ----------------------------------------------------------

    def tools(self) -> list:
        from orion.tools.base import Param, Risk, Tool

        def suspend(reason: str | None = None) -> dict:
            return {"state": self.autonomy.suspend(VOICE, reason or "demandé à la voix").value}

        def stop(reason: str | None = None) -> dict:
            return {"state": self.autonomy.emergency_stop(VOICE, reason or "demandé à la voix").value}

        def say(result: dict) -> str:
            if result["state"] == AutonomyState.EMERGENCY_STOP.value:
                return ("Arrêt d'urgence enclenché, monsieur : plus aucune action autonome. Je reste à votre écoute ; "
                        "la reprise se fait depuis ORION Control.")
            return ("C'est fait, monsieur : mon autonomie est suspendue. Vos tâches programmées continuent ; "
                    "la reprise se fait depuis ORION Control.")

        reason = {"reason": Param(str, "raison, si elle a été dite", required=False, max_length=200)}
        return [
            Tool("suspend_autonomy", "Suspend les initiatives autonomes d'ORION (« suspends ton autonomie », « mets "
                 "ton autonomie en pause ») ; la conversation et les tâches programmées continuent.", reason,
                 {"state": "état"}, Risk.SAFE, suspend, say=say),
            Tool("emergency_stop", "Arrêt d'urgence : plus aucune action autonome, tâches programmées comprises "
                 "(« arrêt d'urgence », « arrête tout ce que tu fais seule »).", reason,
                 {"state": "état"}, Risk.SAFE, stop, say=say),
        ]

    # --- API d'administration (ORION Control) -----------------------------------------------------------------

    def admin(self, method: str, route: list[str], query: dict, data: Any, session: str) -> tuple[int, Any]:
        """``session`` : client authentifié par l'API (IP autorisée et jeton vérifiés avant d'arriver ici)."""
        if route == ["autonomy"] and method == "GET":
            return 200, self.autonomy.snapshot()
        if route == ["autonomy"] and method == "POST":
            data = data if isinstance(data, dict) else {}
            target = data.get("state")
            try:
                if target == AutonomyState.EMERGENCY_STOP.value:
                    self.autonomy.emergency_stop(CONTROL, str(data.get("reason") or "ORION Control"))
                elif target in (s.value for s in AutonomyState):
                    self.autonomy.change(target, CONTROL, str(data.get("reason") or "ORION Control"),
                                         confirmation=str(data.get("confirmation") or ""), session=session)
                else:
                    return 400, {"status": "error", "error": "bad_request", "message": "état inconnu"}
            except AutonomyError as exc:
                return 403, {"status": "error", "error": "autonomy_refused", "message": str(exc)}
            return 200, self.autonomy.snapshot()
        if route == ["ledger"] and method == "GET":
            first = lambda name: (query.get(name) or [None])[0]  # noqa: E731
            return 200, {"entries": self.ledger.list(int(first("limit") or 100), first("principal"),
                                                     int(first("before") or 0) or None),
                         "head": self.ledger.head()}
        if route == ["ledger", "verify"] and method == "GET":
            return 200, self.ledger.verify().as_dict()
        if route == ["grants"] and method == "GET":
            return 200, [g.as_dict() for g in self.permissions.grants.list(include_revoked=True)]
        if len(route) == 2 and route[0] == "grants" and method == "DELETE":
            ok = self.permissions.grants.revoke(route[1], by=CONTROL)
            return (200, {"status": "ok"}) if ok else (404, {"status": "error", "error": "not_found"})
        return 404, {"status": "error", "error": "not_found"}


def build_foundation(cfg) -> Foundation | None:
    p = getattr(cfg, "platform", None)
    if p is None or not p.enabled:
        return None
    foundation = Foundation(Store(Path(p.database)))
    log.info("CORE FOUNDATION : autonomie %s, registre %d lignes", foundation.autonomy.snapshot()["label"],
             foundation.ledger.head()["count"])
    return foundation
