"""Module MISSIONS (lot M1, docs/v3/missions.md) : vos missions gardées en base, suivies d'après leurs tâches F2.

- **Stockage** : base opérationnelle (comme les tâches F2), plan entier **scellé** (lot F4 : objectif, titres, notes et
  paramètres peuvent être personnels) ; en clair seulement l'état, la révision, les compteurs et les dates. Base
  personnelle verrouillée (clé absente) : rien n'est écrit, le module se dit verrouillé (423 dans ORION Control).
- **Création et décisions : vous seul** (conversation, voix ou ORION Control). Une mission naît en brouillon : son plan
  ne vaut rien tant que vous ne l'avez pas validé.
- **Lancement : sur votre action seulement** (« lancer les étapes prêtes »). Chaque étape prête devient une tâche F2
  programmée par vous (``schedule_for_user``), donc décidée par le moteur central au moment d'agir, sous votre
  délégation ; le module n'a aucun droit propre et ne lance jamais rien de lui-même (autonomie suspendue ou non).
- **Suivi** (tâche de fond ``missions:refresh``) : états des étapes recalculés d'après leurs tâches, blocages repérés ;
  un blocage qui vous attend est annoncé une fois (``missions.blocked``, texte privé), la fin aussi
  (``missions.finished``). Le suivi ne crée, ne relance et n'annule aucune tâche.
- **Annulation** : la mission et ses tâches encore actives (annulation coopérative F2).
- **Registre** : chaque décision (création, validation, lancement, étape validée ou faite, annulation), sans texte.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Any, Callable

from orion.missions.plan import FINAL, Plan, PlanError, Step
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

DRAFT, ACTIVE, FINISHED, CANCELLED = "draft", "active", "finished", "cancelled"
REFRESH_JOB = "missions:refresh"
USER_ACTORS = frozenset({"user", "voix", "control"})  # les créateurs de tâches « programmées par vous » (F2)
MAX_OBJECTIVE, MAX_TITLE = 300, 200
STEP_FIELDS = {"id", "title", "kind", "after", "task_kind", "params", "sensitive"}

MIGRATIONS = [Migration(1, """
CREATE TABLE missions (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    done INTEGER NOT NULL,
    plan TEXT NOT NULL,           -- plan entier, scellé (lot F4)
    notified TEXT NOT NULL,       -- blocages déjà annoncés (identifiants d'étapes et raisons, sans texte libre)
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX missions_state ON missions (state);
""")]

LOCKED = (423, {"status": "error", "error": "locked",
                "message": "Base personnelle verrouillée : les missions ne sont pas accessibles (clé absente)."})


class MissionError(ValueError):
    pass


class MissionsLocked(RuntimeError):
    pass


class MissionsModule(BaseModule):
    name = "missions"
    title = "MISSIONS"

    def __init__(self, store: Store, tasks, *, events=None, ledger=None, scheduler=None,
                 clock: Callable[[], float] = time.time, refresh_seconds: float = 60.0):
        self._store, self._tasks, self._ledger, self._scheduler = store, tasks, ledger, scheduler
        self._clock, self.refresh_seconds = clock, max(10.0, float(refresh_seconds))
        self._gate = events.gate("missions", "delegated:missions:suivi") if events is not None else None
        self._lock = threading.RLock()
        store.migrate("missions", MIGRATIONS)

    # --- Cycle de vie ---------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._scheduler is not None:
            self._scheduler.add(REFRESH_JOB, self.refresh_seconds, self.refresh)

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.remove(REFRESH_JOB)

    @property
    def locked(self) -> bool:
        from orion.platform.vault import WITHHELD

        return self._tasks.sealer.mode == WITHHELD

    def status(self) -> dict:
        rows = self._store.query("SELECT state, COUNT(*) AS n FROM missions GROUP BY state")
        return {"locked": self.locked, "missions": {r["state"]: r["n"] for r in rows}}

    # --- Stockage (plan scellé) -----------------------------------------------------------------------------

    def _seal(self, mid: str, plan: Plan) -> str:
        text = dumps(plan.as_dict())
        sealed = self._tasks.sealer.seal(text, f"missions:{mid}:plan")
        if not sealed:
            raise MissionsLocked("base personnelle verrouillée")
        return sealed

    def _load(self, mid: str) -> tuple[dict, Plan]:
        row = self._store.one("SELECT * FROM missions WHERE id = ?", (mid,))
        if row is None:
            raise KeyError(mid)
        text = self._tasks.sealer.open(row["plan"], f"missions:{mid}:plan")
        if text is None:
            raise MissionsLocked("base personnelle verrouillée")
        return dict(row), Plan.from_dict(loads(text, {}))

    def _save(self, mid: str, plan: Plan, state: str, notified: list[str] | None = None) -> None:
        summary = plan.summary()
        params: list[Any] = [state, plan.revision, summary["steps"], summary["done"], self._seal(mid, plan),
                             self._clock()]
        extra = ""
        if notified is not None:
            extra, params = ", notified = ?", [*params, dumps(sorted(notified))]
        self._store.write(f"UPDATE missions SET state = ?, revision = ?, steps = ?, done = ?, plan = ?, updated_at = ?"
                          f"{extra} WHERE id = ?", [*params, mid])

    def _record(self, by: str, action: str, mid: str, allowed: bool, reason: str) -> None:
        if self._ledger is not None:
            self._ledger.append(by, "WRITE_DATA", f"missions.{action}:{mid}", "allow" if allowed else "deny", reason)

    def _actor(self, by: str, action: str, mid: str = "-") -> None:
        if by not in USER_ACTORS:
            self._record(str(by)[:80], action, mid, False, "décision réservée à l'utilisateur")
            raise PermissionError("une mission ne se crée et ne se décide qu'à la voix, en conversation ou dans "
                                  "ORION Control")
        if self.locked:
            raise MissionsLocked("base personnelle verrouillée")

    # --- Vos décisions --------------------------------------------------------------------------------------

    def create(self, objective: str, steps: list[dict], *, by: str) -> dict:
        """Mission en brouillon : plan vérifié (graphe, natures, types de tâche existants), à valider par vous."""
        self._actor(by, "create")
        objective = str(objective or "").strip()[:MAX_OBJECTIVE]
        plan = Plan(objective, [self._step(raw) for raw in (steps or [])])
        plan.validate()
        known = set(self._tasks.types())
        unknown = sorted({s.task_kind for s in plan.steps if s.task_kind and s.task_kind not in known})
        if unknown:
            raise PlanError("type de tâche inconnu : " + ", ".join(unknown))
        mid = secrets.token_hex(6)
        now = self._clock()
        with self._lock:
            summary = plan.summary()
            self._store.write("INSERT INTO missions (id, state, revision, steps, done, plan, notified, created_by, "
                              "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (mid, DRAFT, plan.revision, summary["steps"], summary["done"], self._seal(mid, plan),
                               "[]", by, now, now))
        self._record(by, "create", mid, True, f"brouillon de {len(plan.steps)} étape(s)")
        return self.view(mid)

    @staticmethod
    def _step(raw: dict) -> Step:
        if not isinstance(raw, dict) or set(raw) - STEP_FIELDS:
            raise PlanError("étape : champs attendus " + ", ".join(sorted(STEP_FIELDS)))
        after = raw.get("after", [])
        params = raw.get("params", {})
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after) or not isinstance(params, dict):
            raise PlanError("étape : « after » est une liste d'identifiants, « params » un objet")
        return Step(str(raw.get("id", "")).strip()[:40], str(raw.get("title", "")).strip()[:MAX_TITLE],
                    str(raw.get("kind", "action")), list(after), str(raw.get("task_kind", "")), dict(params),
                    bool(raw.get("sensitive", False)))

    def approve_plan(self, mid: str, *, by: str) -> dict:
        self._actor(by, "approve", mid)
        with self._lock:
            row, plan = self._load(mid)
            if row["state"] != DRAFT:
                raise MissionError("ce plan n'attend pas de validation")
            plan.refresh({}, self._clock())
            self._save(mid, plan, ACTIVE)
        self._record(by, "approve", mid, True, "plan validé")
        return self.view(mid)

    def launch(self, mid: str, *, by: str) -> dict:
        """Les étapes prêtes deviennent des tâches F2 programmées par vous ; rien d'autre ne lance une étape."""
        self._actor(by, "launch", mid)
        from orion.platform.tasks import TaskError

        launched, refused = [], []
        with self._lock:
            row, plan = self._load(mid)
            if row["state"] != ACTIVE:
                raise MissionError("mission non active : validez d'abord son plan")
            now = self._clock()
            for step in plan.refresh(self._task_states(plan), now):
                try:
                    task = self._tasks.schedule_for_user(step.task_kind, step.params, by=by,
                                                         key=f"missions:{mid}:{step.id}:r{plan.revision}")
                except (TaskError, PermissionError) as exc:
                    step.state, step.note, step.changed_at = "failed", f"tâche refusée : {exc}"[:200], now
                    refused.append(step.id)
                    continue
                step.task_id, step.state, step.changed_at = task.id, "running", now
                launched.append(step.id)
            self._save(mid, plan, ACTIVE)
        self._record(by, "launch", mid, True, f"{len(launched)} étape(s) lancée(s), {len(refused)} refusée(s)")
        return {**self.view(mid), "launched": launched, "refused": refused}

    def approve_step(self, mid: str, step_id: str, *, by: str) -> dict:
        return self._step_decision(mid, step_id, by, "approve_step", lambda plan: plan.approve(step_id))

    def complete_human(self, mid: str, step_id: str, note: str = "", *, by: str) -> dict:
        return self._step_decision(mid, step_id, by, "complete",
                                   lambda plan: plan.complete_human(step_id, str(note or "")))

    def _step_decision(self, mid: str, step_id: str, by: str, action: str, apply: Callable[[Plan], Step]) -> dict:
        self._actor(by, action, mid)
        with self._lock:
            row, plan = self._load(mid)
            if row["state"] != ACTIVE:
                raise MissionError("mission non active")
            plan.refresh(self._task_states(plan), self._clock())
            apply(plan)
            plan.refresh(self._task_states(plan), self._clock())
            self._save(mid, plan, FINISHED if plan.finished() else ACTIVE)
        self._record(by, action, mid, True, f"étape {step_id}")
        return self.view(mid)

    def cancel(self, mid: str, *, by: str) -> dict:
        self._actor(by, "cancel", mid)
        with self._lock:
            row, plan = self._load(mid)
            if row["state"] in (FINISHED, CANCELLED):
                raise MissionError("mission déjà terminée")
            now = self._clock()
            for step in plan.steps:
                if step.task_id and step.state not in FINAL:
                    self._tasks.cancel(step.task_id, by=by)  # annulation coopérative : jamais dite faite avant
                if step.state not in FINAL:
                    step.state, step.changed_at = "cancelled", now
            self._save(mid, plan, CANCELLED)
        self._record(by, "cancel", mid, True, "mission annulée")
        return self.view(mid)

    # --- Suivi (tâche de fond) ------------------------------------------------------------------------------

    def _task_states(self, plan: Plan) -> dict[str, str]:
        states = {}
        for step in plan.steps:
            if step.task_id:
                task = self._tasks.get(step.task_id)
                if task is not None:
                    states[step.task_id] = task.state
                    if task.last_error and task.state in ("failed", "expired"):
                        step.note = str(task.last_error)[:200]
        return states

    def refresh(self, now: float | None = None) -> dict:
        """États d'après les tâches ; blocages qui vous attendent annoncés une fois ; fin annoncée. Ne lance rien."""
        if self.locked:
            return {"locked": True}
        now = self._clock() if now is None else now
        announced = finished = 0
        for row in self._store.query("SELECT id FROM missions WHERE state = ?", (ACTIVE,)):
            mid = row["id"]
            with self._lock:
                try:
                    meta, plan = self._load(mid)
                except (KeyError, MissionsLocked, PlanError):
                    continue
                before_plan = dumps(plan.as_dict())
                plan.refresh(self._task_states(plan), now)
                blockers = [b for b in plan.blockers(now) if b.needs_you]
                keys = [f"{b.step}:{b.reason}" for b in blockers]
                before = set(loads(meta["notified"], []))
                for blocker, key in zip(blockers, keys, strict=True):
                    if key not in before:
                        announced += self._announce("missions.blocked", "Mission en attente de vous",
                                                    f"{plan.objective} : {plan.step(blocker.step).title} "
                                                    f"({blocker.reason}).", f"missions:{mid}:{key}")
                state = ACTIVE
                if plan.finished():
                    state, finished = FINISHED, finished + 1
                    self._announce("missions.finished", "Mission terminée", plan.report(), f"missions:{mid}:fin")
                if state != ACTIVE or set(keys) != before or dumps(plan.as_dict()) != before_plan:
                    self._save(mid, plan, state, keys)  # rien ne change : rien n'est réécrit
        return {"announced": announced, "finished": finished}

    def _announce(self, kind: str, title: str, body: str, key: str) -> int:
        if self._gate is None:
            return 0
        from orion.platform.eventhub import VOICE, WRITTEN

        try:
            self._gate.publish(kind, title, body, dedup_key=key, private=True, channels=(VOICE, WRITTEN))
        except Exception:  # noqa: BLE001 - le suivi continue ; l'annonce manquée sera refaite au prochain blocage
            log.exception("MISSIONS : annonce impossible")
            return 0
        return 1

    # --- Lecture -----------------------------------------------------------------------------------------------

    def view(self, mid: str) -> dict:
        row, plan = self._load(mid)
        return {"id": mid, "state": row["state"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                **plan.as_dict(), "summary": plan.summary(), "report": plan.report(),
                "blockers": [{"step": b.step, "reason": b.reason, "needs_you": b.needs_you}
                             for b in plan.blockers(self._clock())]}

    def missions(self) -> list[dict]:
        """Liste sans texte personnel (état, avancement, dates) ; l'objectif seulement s'il est lisible."""
        out = []
        for row in self._store.query("SELECT * FROM missions ORDER BY updated_at DESC LIMIT 100"):
            text = self._tasks.sealer.open(row["plan"], f"missions:{row['id']}:plan")
            objective = loads(text, {}).get("objective", "") if text else ""
            out.append({"id": row["id"], "state": row["state"], "objective": objective, "steps": row["steps"],
                        "done": row["done"], "revision": row["revision"], "updated_at": row["updated_at"]})
        return out

    # --- ORION Control -----------------------------------------------------------------------------------------

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        body = data if isinstance(data, dict) else {}
        try:
            if method == "GET" and not parts:
                return 200, {"locked": self.locked, "missions": [] if self.locked else self.missions()}
            if self.locked:
                return LOCKED
            if method == "POST" and not parts:
                return 200, self.create(body.get("objective", ""), body.get("steps", []), by="control")
            if not parts:
                return NOT_FOUND
            mid = parts[0]
            if method == "GET" and len(parts) == 1:
                return 200, self.view(mid)
            if method == "POST" and parts[1:] == ["approve"]:
                return 200, self.approve_plan(mid, by="control")
            if method == "POST" and parts[1:] == ["launch"]:
                return 200, self.launch(mid, by="control")
            if method == "POST" and parts[1:] == ["cancel"]:
                return 200, self.cancel(mid, by="control")
            if method == "POST" and len(parts) == 4 and parts[1] == "steps" and parts[3] == "approve":
                return 200, self.approve_step(mid, parts[2], by="control")
            if method == "POST" and len(parts) == 4 and parts[1] == "steps" and parts[3] == "complete":
                return 200, self.complete_human(mid, parts[2], str(body.get("note", "")), by="control")
        except KeyError:
            return 404, {"status": "error", "error": "not_found", "message": "Mission inconnue."}
        except MissionsLocked:
            return LOCKED
        except (PlanError, MissionError) as exc:
            return 400, {"status": "error", "error": "invalid", "message": str(exc)}
        return NOT_FOUND
