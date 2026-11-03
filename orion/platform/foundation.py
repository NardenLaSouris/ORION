"""CORE FOUNDATION assemblé : base opérationnelle, registre d'actions, interrupteur d'autonomie, moteur de
permissions (F1), tâches persistantes et confirmations liées à une demande (F2), et leurs raccordements (Core des
outils, outils vocaux, API d'ORION Control).

Inactif tant que ``[platform] enabled = false`` (comportement V2 inchangé). Activé, il ne remplace rien : le Core des
outils garde ses permissions (profils, confirmations, Cedar) et le moteur central s'y ajoute ; la décision la plus
stricte l'emporte.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from orion.platform.autonomy import CONTROL, VOICE, AutonomyError, AutonomyState, AutonomySwitch
from orion.platform.eventhub import Event, EventHub, InterruptionPolicy
from orion.platform.ledger import ActionLedger
from orion.platform.permissions import Capability, Decision, PermissionEngine
from orion.platform.store import OPERATIONAL, PERSONAL, Store
from orion.platform.tasks import TaskService

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
        self.tasks = TaskService(store, self.permissions)
        self.events = EventHub(store, permit=self.permissions.decide)
        self.configure("22:30", "08:00")  # valeurs de [modules] posées ensuite par la fabrique
        self._stopped_at: float | None = None
        self.autonomy.on_change(self._after_change)
        # Arrêt d'urgence : les tâches en cours reçoivent l'ordre de s'arrêter à leur prochain point de contrôle.
        self.autonomy.on_change(lambda before, after: self.tasks.request_stop_all("arrêt d'urgence")
                                if after is AutonomyState.EMERGENCY_STOP else None)
        self.personal = None  # base personnelle chiffrée (lot F4), branchée par attach_personal
        from orion.platform.resources import ResourceService

        self.resources = ResourceService()  # lot F5 : sondes posées par la fabrique
        self.tasks.busy = self.resources.busy

    def attach_personal(self, personal, keyring) -> None:
        """Lot F4 : les textes personnels des bases opérationnelles sont scellés avec la clé de la base personnelle,
        ou retenus en mémoire si elle est verrouillée ; jamais écrits en clair."""
        from orion.platform.vault import Sealer

        sealer = Sealer(None if personal.locked else keyring, configured=True)
        self.personal = personal
        self.events.sealer = self.tasks.sealer = self.permissions.confirmations.sealer = sealer

    def configure(self, quiet_start: str, quiet_end: str, critical_ignores_quiet: bool = False) -> None:
        """Heures calmes de la règle d'interruption (celles des modules, [modules])."""
        from orion.notifications.manager import QUIET_EXEMPT

        self.events.policy = InterruptionPolicy(quiet_start, quiet_end, critical_ignores_quiet, QUIET_EXEMPT)

    def _after_change(self, before, after) -> None:
        """Arrêt d'urgence : on note quand. Reprise : récapitulatif des notifications manquées (jamais rejouées)."""
        if after is AutonomyState.EMERGENCY_STOP:
            self._stopped_at = self.events._clock()  # noqa: SLF001
            return
        if before is AutonomyState.EMERGENCY_STOP:
            text = self.events.recap(since=self._stopped_at)
            self._stopped_at = None
            if text:
                self.events.publish(Event("system.recap", "recap", "delegated:system", "Notifications manquées",
                                          text + " Dites « mes notifications manquées » pour les entendre.",
                                          expires_at=self.events._clock() + 2 * 3600))  # noqa: SLF001

    def start(self) -> None:
        """Tâches persistantes (reprise des interrompues), puis remises d'événements en attente."""
        self.tasks.start()
        self.events.start()

    def stop(self) -> None:
        self.tasks.stop()
        self.events.stop()

    def wrap(self, gate):
        return FoundationGate(gate, self.permissions)

    # --- Voix : suspendre ou arrêter, jamais réactiver ----------------------------------------------------------

    def tools(self) -> list:
        from orion.tools.base import Param, Risk, Tool

        def suspend(reason: str | None = None) -> dict:
            return {"state": self.autonomy.suspend(VOICE, reason or "demandé à la voix").value}

        def stop(reason: str | None = None) -> dict:
            state = self.autonomy.emergency_stop(VOICE, reason or "demandé à la voix").value
            return {"state": state, "still_running": len(self.tasks.running())}

        def say(result: dict) -> str:
            if result["state"] == AutonomyState.EMERGENCY_STOP.value:
                still = result.get("still_running", 0)
                if still:  # jamais dire « tout est arrêté » quand une opération continue réellement
                    return (f"Arrêt d'urgence enclenché, monsieur : plus aucune nouvelle action autonome. Attention, "
                            f"{still} tâche{'s' if still > 1 else ''} en cours n'{'ont' if still > 1 else 'a'} pas "
                            f"encore pu s'interrompre ; {'elles s' if still > 1 else 'elle s'}'arrêter"
                            f"{'ont' if still > 1 else 'a'} au prochain point de contrôle. Le détail est dans ORION "
                            "Control.")
                return ("Arrêt d'urgence enclenché, monsieur : plus aucune action autonome. Je reste à votre écoute ; "
                        "la reprise se fait depuis ORION Control.")
            return ("C'est fait, monsieur : mon autonomie est suspendue. Vos tâches programmées continuent ; "
                    "la reprise se fait depuis ORION Control.")

        def missed() -> dict:
            items = self.events.missed(include_obsolete=True)
            return {"items": [{"title": i["title"], "source": i["source"], "obsolete": i["obsolete"],
                               "at": i["created_at"]} for i in items][:10], "count": len(items)}

        def say_missed(result: dict) -> str:
            if not result["count"]:
                return "Vous n'avez manqué aucune notification, monsieur."
            from datetime import datetime

            lines = [f"{n}, {i['title']} à {datetime.fromtimestamp(i['at']):%H h %M}"
                     + (" (obsolète)" if i["obsolete"] else "") for n, i in enumerate(result["items"], 1)]
            return (f"{result['count']} notification{'s' if result['count'] > 1 else ''} manquée"
                    f"{'s' if result['count'] > 1 else ''} : " + " ; ".join(lines) + ". Je peux en relancer une.")

        def relaunch(position: int) -> dict:
            items = self.events.missed(include_obsolete=True)
            if not 1 <= position <= len(items):
                return {"status": "unknown"}
            return {"status": self.events.relaunch(items[position - 1]["id"], by=VOICE),
                    "title": items[position - 1]["title"]}

        def say_relaunch(result: dict) -> str:
            if result["status"] == "relaunched":
                return f"Je vous la redonne : {result['title']}."
            if result["status"] == "obsolete":
                return f"« {result['title']} » est devenue sans objet : je ne la rejoue pas."
            return "Je ne trouve pas cette notification manquée."

        reason = {"reason": Param(str, "raison, si elle a été dite", required=False, max_length=200)}
        return [
            Tool("suspend_autonomy", "Suspend les initiatives autonomes d'ORION (« suspends ton autonomie », « mets "
                 "ton autonomie en pause ») ; la conversation et les tâches programmées continuent.", reason,
                 {"state": "état"}, Risk.SAFE, suspend, say=say),
            Tool("emergency_stop", "Arrêt d'urgence : plus aucune action autonome, tâches programmées comprises "
                 "(« arrêt d'urgence », « arrête tout ce que tu fais seule »).", reason,
                 {"state": "état"}, Risk.SAFE, stop, say=say),
            Tool("missed_notifications", "Notifications manquées pendant un arrêt d'urgence (« mes notifications "
                 "manquées », « qu'est-ce que j'ai raté ? »).", {}, {"count": "nombre"}, Risk.SAFE, missed,
                 say=say_missed, capability="READ"),
            Tool("relaunch_missed", "Redonne une notification manquée, par son numéro dans la liste (« relance la "
                 "deuxième »). Jamais une notification devenue obsolète.",
                 {"position": Param(int, "numéro dans la liste des notifications manquées", minimum=1, maximum=50)},
                 {"status": "résultat"}, Risk.SAFE, relaunch, say=say_relaunch, capability="NOTIFY"),
        ]

    # --- API d'administration (ORION Control) -----------------------------------------------------------------

    def admin(self, method: str, route: list[str], query: dict, data: Any, session: str) -> tuple[int, Any]:
        """``session`` : client authentifié par l'API (IP autorisée et jeton vérifiés avant d'arriver ici)."""
        if route and route[0] == "personal":
            return self._personal_admin(method, route[1:])
        if route == ["resources"] and method == "GET":
            return 200, self.resources.status()
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
        if route == ["tasks"] and method == "GET":
            states = tuple(s for s in ((query.get("state") or [""])[0] or "").split(",") if s)
            return 200, {"tasks": [t.as_dict() for t in self.tasks.list(states or None)], **self.tasks.status(),
                         "running": self.tasks.running()}
        if len(route) == 2 and route[0] == "tasks" and method == "GET":
            task = self.tasks.get(route[1])
            if task is None:
                return 404, {"status": "error", "error": "not_found"}
            return 200, {**task.as_dict(), "runs": self.tasks.runs(task.id)}
        if len(route) == 3 and route[0] == "tasks" and method == "POST" and route[2] in ("cancel", "retry", "confirm"):
            ok = getattr(self.tasks, route[2])(route[1], by=CONTROL)
            return (200, {"status": "ok"}) if ok else (409, {"status": "error", "error": "not_applicable"})
        if route == ["events"] and method == "GET":
            return 200, {"events": self.events.recent(int((query.get("limit") or [100])[0])),
                         "pending": self.events.pending()}
        if route == ["events", "missed"] and method == "GET":
            return 200, self.events.missed(include_obsolete=True)
        if len(route) == 3 and route[0] == "events" and method == "POST" and route[2] in ("relaunch", "dismiss"):
            if route[2] == "relaunch":
                status = self.events.relaunch(route[1], by=CONTROL)
                code = {"relaunched": 200, "obsolete": 409}.get(status, 404)
                return code, {"status": status}
            ok = self.events.dismiss(route[1], by=CONTROL)
            return (200, {"status": "ok"}) if ok else (404, {"status": "error", "error": "not_found"})
        if route == ["confirmations"] and method == "GET":
            return 200, self.permissions.confirmations.pending()
        if len(route) == 3 and route[0] == "confirmations" and method == "POST" and route[2] in ("approve", "refuse"):
            ok = getattr(self.permissions.confirmations, route[2])(route[1], by=CONTROL)
            return (200, {"status": "ok"}) if ok else (409, {"status": "error", "error": "not_applicable"})
        return 404, {"status": "error", "error": "not_found"}

    def _personal_admin(self, method: str, route: list[str]) -> tuple[int, Any]:
        """État de la base personnelle (jamais de contenu ni de clé), rotation progressive, vérification."""
        from orion.platform.vault import VaultError

        if self.personal is None:
            return 200, {"state": "non configurée", "reason": "[platform] personal = false"}
        try:
            if route == [] and method == "GET":
                return 200, self.personal.status()
            if route == ["rotate"] and method == "POST":
                done = self.personal.rotate()
                self.ledger.append(CONTROL, "PERSONAL", "personal.rotate", "allow", f"{done} rechiffré(s)")
                return 200, {"rotated": done, **self.personal.status()}
            if route == ["verify"] and method == "POST":
                return 200, self.personal.verify()
        except VaultError as exc:
            return 409, {"status": "error", "error": str(exc)}
        return 404, {"status": "error", "error": "not_found"}


def build_foundation(cfg) -> Foundation | None:
    p = getattr(cfg, "platform", None)
    if p is None or not p.enabled:
        return None
    foundation = Foundation(Store(Path(p.database), OPERATIONAL))
    if p.personal:
        from orion.platform.personal import PersonalStore
        from orion.platform.vault import load_keyring

        keyring, reason = load_keyring(p.data_key)
        personal = PersonalStore(Store(Path(p.personal_database), PERSONAL), keyring, reason)
        foundation.attach_personal(personal, keyring)
        state = personal.status()
        log.info("Données personnelles : base %s (%s)", state["state"], state["reason"] or f"clé {state['current']}")
    log.info("CORE FOUNDATION : autonomie %s, registre %d lignes", foundation.autonomy.snapshot()["label"],
             foundation.ledger.head()["count"])
    return foundation
