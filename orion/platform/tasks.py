"""Tâches persistantes (CORE FOUNDATION F2) : programmées par vous ou initiées par un module, gardées en base,
exécutées sous le contrôle du moteur central.

**Qui programme quoi.**
- Tâche **programmée par vous** : principal ``delegated:<type>:<tâche>``. Créée seulement depuis la conversation, la
  voix ou ORION Control (``schedule_for_user``).
- **Initiative** d'un module : principal ``module:<nom>``. Créée seulement par ce module, à travers son ``TaskGate``
  (principal fixé, types de tâche qui lui appartiennent).
- La **capacité** d'une tâche est déclarée par son type, enregistré par la plateforme (``register``), jamais par
  celui qui la crée : aucun module ne s'accorde une capacité en choisissant un type.

**Exécution** (``run_due``, appelée chaque seconde par un fil de fond, directement par les tests) :
1. arrêt d'urgence ou état illisible : rien ne part, la file entière attend ; vérifié **avant chaque tâche**, pas
   seulement en début de tour ;
2. les tâches dont l'échéance de validité est passée deviennent ``expirée`` ;
3. chaque tâche due est **réservée** par une seule mise à jour conditionnelle (``pending`` -> ``running``, bail
   daté) : deux fils ou deux processus ne peuvent pas l'exécuter tous les deux ;
4. décision du moteur central **au moment de l'exécution** (autorisations relues : une autorisation expirée ou
   révoquée depuis la création ne sert jamais). Une tâche liée à une autorisation précise (``grant_id``) échoue si
   celle-ci n'est plus valide, même si une autre la couvrirait ;
   - permis : exécution sous le principal de la tâche ;
   - à confirmer : une demande de confirmation liée (identifiant, action, paramètres, échéance, usage unique) est
     créée et la tâche passe ``held`` ; ``confirm`` l'accorde, la prochaine exécution la consomme ;
   - retenue par l'état d'autonomie (suspension pour une initiative) : la tâche reste en attente, sans perdre
     d'essai, et repart à la reprise ;
   - refusée pour une autre raison : ``failed`` ;
5. résultat : ``done`` (ou prochaine occurrence pour une tâche périodique, sans rattrapage en rafale), ou échec
   (nouvel essai plus tard, attente doublée, jusqu'à ``max_attempts``, puis ``failed``).

**Interruption brutale** (``recover``, au démarrage puis à chaque tour) : une tâche ``running`` dont le bail est échu
a été interrompue. Type **idempotent** : elle repart (l'essai compte). Sinon : ``interrupted``, jamais relancée
d'elle-même (l'action a pu avoir lieu) ; vous la relancez (``retry``) ou l'annulez.

**Traçabilité** : création, réservation, décision, reprise, échec, expiration, annulation et résultat sont inscrits
au registre d'actions (sans valeur de paramètre) ; chaque exécution a sa ligne dans ``task_runs``.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from orion.platform.autonomy import CONTROL, VOICE
from orion.platform.permissions import Capability, Decision, PermissionEngine
from orion.platform.running import running_as
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            principal TEXT NOT NULL,
            origin TEXT NOT NULL,
            capability TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL,
            run_at REAL NOT NULL,
            interval REAL,
            expires_at REAL,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            attempts INTEGER NOT NULL DEFAULT 0,
            runs INTEGER NOT NULL DEFAULT 0,
            grant_id TEXT NOT NULL DEFAULT '',
            confirmation TEXT NOT NULL DEFAULT '',
            dedup_key TEXT,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            lease_owner TEXT NOT NULL DEFAULT '',
            lease_until REAL,
            last_error TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX tasks_due ON tasks (state, run_at);
        CREATE UNIQUE INDEX tasks_dedup ON tasks (dedup_key) WHERE dedup_key IS NOT NULL;
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            run INTEGER NOT NULL,
            attempt INTEGER NOT NULL,
            owner TEXT NOT NULL,
            started_at REAL NOT NULL,
            finished_at REAL,
            outcome TEXT NOT NULL DEFAULT 'en cours',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX task_runs_task ON task_runs (task_id, id)
    """),
)

PENDING, RUNNING, HELD = "pending", "running", "held"
DONE, FAILED, EXPIRED, CANCELLED, INTERRUPTED = "done", "failed", "expired", "cancelled", "interrupted"
FINAL = frozenset({DONE, FAILED, EXPIRED, CANCELLED, INTERRUPTED})
USER, MODULE = "utilisateur", "module"
USER_CREATORS = frozenset({"user", VOICE, CONTROL})
KIND = re.compile(r"^[a-z][a-z0-9_]{0,30}\.[a-z][a-z0-9_]{0,40}$")
MAX_BACKOFF = 6 * 3600.0
MAX_RESULT = 2000


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class TaskType:
    kind: str  # « <module>.<action> » : le module propriétaire est le préfixe
    handler: Callable[["TaskRun"], Any]
    capability: Capability
    idempotent: bool = False  # relançable sans risque après une interruption
    timeout: float = 300.0  # durée du bail : au-delà, la tâche est considérée interrompue
    user: bool = True  # programmable par vous
    module: bool = False  # programmable par le module propriétaire

    @property
    def owner(self) -> str:
        return self.kind.split(".", 1)[0]


@dataclass(frozen=True)
class TaskRun:
    """Ce que reçoit le code d'une tâche : ses paramètres (copie) et une clé unique par exécution, à reprendre pour
    rendre idempotente une action extérieure (identifiant de commande, d'envoi…)."""

    task_id: str
    kind: str
    principal: str
    params: dict
    run: int
    attempt: int

    @property
    def run_key(self) -> str:
        return f"{self.task_id}:{self.run}"


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    principal: str
    origin: str
    capability: str
    params: dict
    state: str
    run_at: float
    interval: float | None
    expires_at: float | None
    max_attempts: int
    attempts: int
    runs: int
    grant_id: str
    confirmation: str
    created_by: str
    created_at: float
    updated_at: float
    lease_owner: str
    lease_until: float | None
    last_error: str
    result: Any

    @classmethod
    def from_row(cls, row: dict) -> "Task":
        return cls(row["id"], row["kind"], row["principal"], row["origin"], row["capability"],
                   loads(row["params"], {}), row["state"], row["run_at"], row["interval"], row["expires_at"],
                   row["max_attempts"], row["attempts"], row["runs"], row["grant_id"], row["confirmation"],
                   row["created_by"], row["created_at"], row["updated_at"], row["lease_owner"], row["lease_until"],
                   row["last_error"], loads(row["result"], {}))

    def as_dict(self) -> dict:
        """Vue d'ORION Control : sans valeur de paramètre."""
        return {"id": self.id, "kind": self.kind, "principal": self.principal, "origin": self.origin,
                "capability": self.capability, "parameters": sorted(self.params), "state": self.state,
                "run_at": self.run_at, "interval": self.interval, "expires_at": self.expires_at,
                "attempts": self.attempts, "max_attempts": self.max_attempts, "runs": self.runs,
                "grant_id": self.grant_id, "awaiting_confirmation": self.confirmation if self.state == HELD else "",
                "created_by": self.created_by, "created_at": self.created_at, "last_error": self.last_error}


def _short(value: Any) -> Any:
    from orion.tools.core import loggable

    text = dumps(loggable(value if isinstance(value, dict) else {"résultat": value}))
    return loads(text, {}) if len(text) <= MAX_RESULT else {"tronqué": True}


class TaskService:
    def __init__(self, store: Store, permissions: PermissionEngine, clock: Callable[[], float] = time.time,
                 instance: str | None = None, max_backoff: float = MAX_BACKOFF, batch: int = 20):
        self._store, self._permissions, self._clock = store, permissions, clock
        self._ledger = permissions.ledger
        self.instance = instance or secrets.token_hex(6)  # propriétaire des bails de ce processus
        self._types: dict[str, TaskType] = {}
        self._max_backoff, self._batch = max_backoff, batch
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        store.migrate("tasks", MIGRATIONS)

    # --- Types de tâche (code de la plateforme, jamais des données) ------------------------------------------

    def register(self, kind: str, handler: Callable[[TaskRun], Any], capability: Capability | str, *,
                 idempotent: bool = False, timeout: float = 300.0, user: bool = True, module: bool = False) -> None:
        if not KIND.match(kind or ""):
            raise TaskError(f"type de tâche invalide : {kind!r} (« module.action » attendu)")
        if kind in self._types:
            raise TaskError(f"type de tâche déjà enregistré : {kind}")
        self._types[kind] = TaskType(kind, handler, Capability(capability), idempotent, max(1.0, float(timeout)),
                                     user, module)

    def types(self) -> list[str]:
        return sorted(self._types)

    def _type(self, kind: str) -> TaskType:
        if kind not in self._types:
            raise TaskError(f"type de tâche inconnu : {kind!r}")
        return self._types[kind]

    # --- Création ----------------------------------------------------------------------------------------------

    def schedule_for_user(self, kind: str, params: dict | None = None, *, by: str, run_at: float | None = None,
                          interval: float | None = None, expires_at: float | None = None, max_attempts: int = 3,
                          grant_id: str = "", key: str | None = None) -> Task:
        """Tâche que vous programmez (conversation, voix ou ORION Control) : principal ``delegated:<type>:<id>``."""
        if by not in USER_CREATORS:
            self._ledger.append(str(by)[:80], "TASK", f"task.create:{kind}", "deny",
                                "tâche « programmée par l'utilisateur » créée hors de la conversation ou d'ORION Control")
            raise PermissionError("une tâche programmée ne se crée qu'à la voix, en conversation ou depuis ORION Control")
        task_type = self._type(kind)
        if not task_type.user:
            raise TaskError(f"{kind} : type non programmable par l'utilisateur")
        return self._create(task_type, params, None, USER, by, run_at, interval, expires_at, max_attempts, grant_id,
                            key)

    def _schedule_for_module(self, module: str, kind: str, params: dict | None, run_at: float | None,
                             interval: float | None, expires_at: float | None, max_attempts: int,
                             key: str | None) -> Task:
        task_type = self._type(kind)
        if not task_type.module or task_type.owner != module:
            self._ledger.append(f"module:{module}", "TASK", f"task.create:{kind}", "deny",
                                "type de tâche d'un autre module ou non programmable par un module")
            raise PermissionError(f"{kind} : le module {module} ne peut pas créer ce type de tâche")
        return self._create(task_type, params, f"module:{module}", MODULE, f"module:{module}", run_at, interval,
                            expires_at, max_attempts, "", key)

    def _create(self, task_type: TaskType, params: dict | None, principal: str | None, origin: str, by: str,
                run_at: float | None, interval: float | None, expires_at: float | None, max_attempts: int,
                grant_id: str, key: str | None) -> Task:
        params = dict(params or {})
        dumps(params)  # paramètres sérialisables, sinon erreur ici plutôt qu'à l'exécution
        now = self._clock()
        run_at = now if run_at is None else float(run_at)
        if interval is not None and float(interval) <= 0:
            raise TaskError("intervalle strictement positif attendu")
        if expires_at is not None and float(expires_at) <= now:
            raise TaskError("échéance de validité déjà passée")
        if grant_id and not self._permissions.grants.is_active(grant_id, now):
            raise TaskError("autorisation inconnue, révoquée ou expirée")
        task_id = secrets.token_hex(6)
        principal = principal or f"delegated:{task_type.owner}:{task_id}"
        with self._lock, self._store.transaction():
            if key:
                existing = self._store.one("SELECT * FROM tasks WHERE dedup_key = ?", (key,))
                if existing is not None:  # même demande répétée (double clic, nouvel essai réseau) : une seule tâche
                    return Task.from_row(existing)
            self._store.write(
                "INSERT INTO tasks (id, kind, principal, origin, capability, params, state, run_at, interval, "
                "expires_at, max_attempts, grant_id, dedup_key, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, task_type.kind, principal, origin, task_type.capability.value, dumps(params), PENDING,
                 run_at, float(interval) if interval else None, float(expires_at) if expires_at else None,
                 max(1, int(max_attempts)), grant_id, key or None, by, now, now))
            self._ledger.append(by, "TASK", f"task.create:{task_id}", "allow",
                                f"{task_type.kind} ({origin}) sous {principal}",
                                detail={"paramètres": sorted(params), "autorisation": grant_id})
        return self.get(task_id)

    # --- Exécution ---------------------------------------------------------------------------------------------

    def run_due(self, now: float | None = None) -> list[tuple[str, str]]:
        """Exécute les tâches dues ; rend [(identifiant, résultat)] dans l'ordre."""
        now = self._clock() if now is None else now
        if self._halted():
            return []
        self.recover(now)
        self._expire(now)
        due = self._store.query("SELECT * FROM tasks WHERE state = ? AND run_at <= ? ORDER BY run_at, created_at "
                                "LIMIT ?", (PENDING, now, self._batch))
        outcomes = []
        for row in due:
            if self._halted():  # arrêt d'urgence pendant la file : le reste attend
                log.warning("Tâches : arrêt d'urgence, file interrompue")
                break
            outcome = self._run(Task.from_row(row), now)
            if outcome:
                outcomes.append((row["id"], outcome))
        return outcomes

    def _halted(self) -> bool:
        """Arrêt d'urgence ou état illisible : vrai (une tâche programmée elle-même ne passe plus)."""
        try:
            return bool(self._permissions.blocked("delegated:tasks", Capability.READ))
        except Exception:  # noqa: BLE001
            log.exception("Tâches : état d'autonomie illisible")
            return True

    def _run(self, task: Task, now: float) -> str:
        task_type = self._types.get(task.kind)
        if task_type is None:
            return ""  # type pas (encore) enregistré : la tâche attend, l'échéance de validité s'applique
        if why := self._permissions.blocked(task.principal, task.capability):
            if task.last_error != f"retenue : {why}":  # une ligne au registre par changement, pas par tour
                self._update(task.id, last_error=f"retenue : {why}")
                self._ledger.append(task.principal, "TASK", f"task.hold:{task.id}", "deny", why)
            return ""
        if not self._claim(task, now, task_type):
            return ""  # déjà prise par un autre fil ou un autre processus
        run, attempt = task.runs + 1, task.attempts + 1
        run_id = self._store.write("INSERT INTO task_runs (task_id, run, attempt, owner, started_at) "
                                   "VALUES (?, ?, ?, ?, ?)", (task.id, run, attempt, self.instance, now))
        verdict = self._decide(task, task_type)
        if verdict.decision is Decision.ASK:
            return self._hold(task, run_id, verdict.reason)
        if not verdict.allowed:
            if why := self._permissions.blocked(task.principal, task.capability):  # changé entre-temps : on attend
                self._release(task, run_id, f"retenue : {why}")
                return ""
            return self._finish(task, run_id, FAILED, f"refusée : {verdict.reason}")
        try:
            with running_as(task.principal):
                result = task_type.handler(TaskRun(task.id, task.kind, task.principal, dict(task.params), run,
                                                   attempt))
        except Exception as exc:  # noqa: BLE001 - une tâche en échec ne bloque jamais les autres
            log.exception("Tâche %s (%s) en échec", task.id, task.kind)
            return self._failed(task, run_id, attempt, f"{type(exc).__name__}: {exc}"[:300])
        return self._succeeded(task, run_id, result)

    def _decide(self, task: Task, task_type: TaskType):
        from orion.platform.permissions import Verdict

        now = self._clock()
        if task.grant_id and not self._permissions.grants.is_active(task.grant_id, now):
            self._ledger.append(task.principal, task.capability, f"task:{task.kind}", "deny",
                                "autorisation liée révoquée ou expirée", task.id)
            return Verdict(Decision.DENY, "autorisation liée révoquée ou expirée")
        return self._permissions.decide(task.principal, task_type.capability, self._context(task),
                                        action=f"task:{task.kind}",
                                        request_id=task.id, confirmation=task.confirmation)

    def _claim(self, task: Task, now: float, task_type: TaskType) -> bool:
        with self._lock:
            changed = self._store.write(
                "UPDATE tasks SET state = ?, lease_owner = ?, lease_until = ?, attempts = attempts + 1, "
                "last_error = '', updated_at = ? WHERE id = ? AND state = ? AND run_at <= ? AND attempts = ?",
                (RUNNING, self.instance, now + task_type.timeout, now, task.id, PENDING, now, task.attempts))
        return changed == 1

    def _hold(self, task: Task, run_id: int, reason: str) -> str:
        confirmation = self._permissions.confirmations.request(
            task.principal, f"task:{task.kind}", self._context(task), summary=f"{task.kind} ({task.id})",
            ttl=max(60.0, (task.expires_at - self._clock()) if task.expires_at else 86400.0))
        self._end_run(run_id, "en attente de confirmation", reason)
        self._update(task.id, state=HELD, lease_owner="", lease_until=None, attempts=task.attempts,
                     confirmation=confirmation.id, last_error=reason, guard=RUNNING)
        self._ledger.append(task.principal, "TASK", f"task.hold:{task.id}", "ask", reason,
                            detail={"demande": confirmation.id})
        return HELD

    def _release(self, task: Task, run_id: int, why: str) -> None:
        """Retour en attente sans perdre d'essai (l'état d'autonomie a changé pendant la réservation)."""
        self._end_run(run_id, "retenue", why)
        self._update(task.id, state=PENDING, lease_owner="", lease_until=None, attempts=task.attempts,
                     last_error=why, guard=RUNNING)

    def _succeeded(self, task: Task, run_id: int, result: Any) -> str:
        now = self._clock()
        short = _short(result)
        self._end_run(run_id, "réussie", "")
        if task.interval:
            next_at = task.run_at + task.interval
            while next_at <= now:  # occurrences manquées (arrêt, panne) : une seule exécution, pas de rafale
                next_at += task.interval
            if task.expires_at is not None and next_at >= task.expires_at:
                return self._finish(task, run_id, DONE, "", result=short, run_done=True)
            if not self._update(task.id, state=PENDING, run_at=next_at, attempts=0, runs=task.runs + 1,
                                lease_owner="", lease_until=None, last_error="", result=dumps(short),
                                confirmation="", guard=RUNNING):
                return self.get(task.id).state  # annulée pendant l'exécution
            self._ledger.append(task.principal, "TASK", f"task.result:{task.id}", "allow", "réussie ; suivante prévue",
                                detail={"résultat": sorted(short) if isinstance(short, dict) else []})
            return DONE
        return self._finish(task, run_id, DONE, "", result=short, run_done=True)

    def _failed(self, task: Task, run_id: int, attempt: int, error: str) -> str:
        if attempt >= task.max_attempts:
            return self._finish(task, run_id, FAILED, error)
        now = self._clock()
        self._end_run(run_id, "échec", error)
        retry_at = now + min(30.0 * 2 ** (attempt - 1), self._max_backoff)
        self._update(task.id, state=PENDING, run_at=retry_at, lease_owner="", lease_until=None, last_error=error,
                     confirmation="", guard=RUNNING)
        self._ledger.append(task.principal, "TASK", f"task.fail:{task.id}", "allow",
                            f"échec {attempt}/{task.max_attempts} ; nouvel essai prévu")
        return "retry"

    def _finish(self, task: Task, run_id: int, state: str, error: str, *, result: Any = None,
                run_done: bool = False) -> str:
        self._end_run(run_id, {DONE: "réussie", FAILED: "échec"}.get(state, state), error)
        if not self._update(task.id, state=state, lease_owner="", lease_until=None, last_error=error,
                            confirmation="", runs=task.runs + (1 if run_done else 0), result=dumps(result or {}),
                            guard=RUNNING):
            self._ledger.append(task.principal, "TASK", f"task.result:{task.id}", "deny",
                                "résultat ignoré : tâche annulée ou reprise entre-temps")
            return self.get(task.id).state
        self._ledger.append(task.principal, "TASK", f"task.{'result' if state == DONE else 'fail'}:{task.id}",
                            "allow" if state == DONE else "deny", (error or "réussie")[:200])
        return state

    def _end_run(self, run_id: int, outcome: str, error: str) -> None:
        self._store.write("UPDATE task_runs SET finished_at = ?, outcome = ?, error = ? WHERE id = ?",
                          (self._clock(), outcome, error[:300], run_id))

    def _update(self, task_id: str, guard: str | None = None, **fields: Any) -> bool:
        """Mise à jour ; avec ``guard``, seulement si la tâche est encore dans cet état et réservée par ce processus
        (une annulation pendant l'exécution l'emporte sur le résultat)."""
        fields["updated_at"] = self._clock()
        sql = "UPDATE tasks SET " + ", ".join(f"{name} = ?" for name in fields) + " WHERE id = ?"
        params = [*fields.values(), task_id]
        if guard:
            sql += " AND state = ? AND lease_owner = ?"
            params += [guard, self.instance]
        with self._lock:
            return self._store.write(sql, params) == 1

    @staticmethod
    def _context(task: Task) -> dict:
        return {"task": task.id, "params": task.params,
                **{k: task.params[k] for k in ("amount", "device") if k in task.params}}

    # --- Reprise, expiration ---------------------------------------------------------------------------------

    def recover(self, now: float | None = None) -> list[str]:
        """Tâches interrompues (bail échu) : idempotentes relancées, les autres ``interrupted`` ; rend leurs ids."""
        now = self._clock() if now is None else now
        found = []
        for row in self._store.query("SELECT * FROM tasks WHERE state = ? AND (lease_until IS NULL OR "
                                     "lease_until < ?)", (RUNNING, now)):
            task = Task.from_row(row)
            task_type = self._types.get(task.kind)
            self._store.write("UPDATE task_runs SET finished_at = ?, outcome = 'interrompue' WHERE task_id = ? "
                              "AND finished_at IS NULL", (now, task.id))
            if task_type is not None and task_type.idempotent and task.attempts < task.max_attempts:
                changed = self._store.write("UPDATE tasks SET state = ?, lease_owner = '', lease_until = NULL, "
                                            "last_error = ?, updated_at = ? WHERE id = ? AND state = ? AND "
                                            "lease_owner = ?", (PENDING, "interrompue ; reprise", now, task.id,
                                                                RUNNING, task.lease_owner))
                verdict = "reprise (type idempotent)"
            else:
                changed = self._store.write("UPDATE tasks SET state = ?, lease_owner = '', lease_until = NULL, "
                                            "last_error = ?, updated_at = ? WHERE id = ? AND state = ? AND "
                                            "lease_owner = ?", (INTERRUPTED, "interrompue pendant l'exécution : "
                                                                "résultat inconnu, non relancée", now, task.id,
                                                                RUNNING, task.lease_owner))
                verdict = "interrompue, non relancée (résultat inconnu)"
            if changed:
                self._ledger.append(task.principal, "TASK", f"task.recover:{task.id}", "allow", verdict)
                found.append(task.id)
        return found

    def _expire(self, now: float) -> None:
        for row in self._store.query("SELECT id, principal FROM tasks WHERE state IN (?, ?) AND expires_at IS NOT "
                                     "NULL AND expires_at <= ?", (PENDING, HELD, now)):
            if self._store.write("UPDATE tasks SET state = ?, updated_at = ?, last_error = 'échéance dépassée' "
                                 "WHERE id = ? AND state IN (?, ?)", (EXPIRED, now, row["id"], PENDING, HELD)):
                self._ledger.append(row["principal"], "TASK", f"task.expire:{row['id']}", "deny",
                                    "échéance de validité dépassée")

    # --- Vos décisions (voix, conversation, ORION Control) -----------------------------------------------------

    def confirm(self, task_id: str, *, by: str) -> bool:
        """Accorde la demande de confirmation d'une tâche ``held`` ; elle repart et la consomme à l'exécution."""
        task = self.get(task_id)
        if task is None or task.state != HELD or not task.confirmation:
            return False
        if not self._permissions.confirmations.approve(task.confirmation, by=by):
            return False
        now = self._clock()
        with self._lock:
            return self._store.write("UPDATE tasks SET state = ?, run_at = ?, updated_at = ?, last_error = ? "
                                     "WHERE id = ? AND state = ?", (PENDING, min(task.run_at, now), now,
                                                                     f"confirmée ({by}) : exécution au prochain tour",
                                                                     task_id, HELD)) == 1

    def cancel(self, task_id: str, *, by: str) -> bool:
        if by not in USER_CREATORS:
            raise PermissionError("annulation : voix, conversation ou ORION Control")
        return self._cancel(task_id, by)

    def _cancel(self, task_id: str, by: str, principal: str | None = None) -> bool:
        sql = ("UPDATE tasks SET state = ?, updated_at = ?, last_error = ? WHERE id = ? AND state IN (?, ?, ?)" +
               (" AND principal = ?" if principal else ""))
        params = [CANCELLED, self._clock(), f"annulée ({by})", task_id, PENDING, HELD, RUNNING]
        changed = self._store.write(sql, params + ([principal] if principal else []))
        if changed:
            self._ledger.append(by, "TASK", f"task.cancel:{task_id}", "allow", "tâche annulée")
        return bool(changed)

    def retry(self, task_id: str, *, by: str) -> bool:
        """Relance une tâche interrompue ou en échec : vous seul (le résultat d'une interruption est inconnu)."""
        if by not in USER_CREATORS:
            raise PermissionError("relance : voix, conversation ou ORION Control")
        now = self._clock()
        changed = self._store.write("UPDATE tasks SET state = ?, attempts = 0, run_at = ?, updated_at = ?, "
                                    "last_error = '', confirmation = '' WHERE id = ? AND state IN (?, ?) AND "
                                    "(expires_at IS NULL OR expires_at > ?)",
                                    (PENDING, now, now, task_id, INTERRUPTED, FAILED, now))
        if changed:
            self._ledger.append(by, "TASK", f"task.retry:{task_id}", "allow", "relancée")
        return bool(changed)

    # --- Lecture -----------------------------------------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        row = self._store.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return Task.from_row(row) if row else None

    def list(self, states: tuple[str, ...] | None = None, limit: int = 200) -> list[Task]:
        sql, params = "SELECT * FROM tasks", []
        if states:
            sql += f" WHERE state IN ({', '.join('?' for _ in states)})"
            params += list(states)
        rows = self._store.query(sql + " ORDER BY run_at LIMIT ?", (*params, max(1, min(int(limit), 1000))))
        return [Task.from_row(row) for row in rows]

    def runs(self, task_id: str) -> list[dict]:
        return self._store.query("SELECT * FROM task_runs WHERE task_id = ? ORDER BY id", (task_id,))

    def status(self) -> dict:
        counts = {row["state"]: row["n"] for row in
                  self._store.query("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")}
        return {"counts": counts, "types": self.types(), "instance": self.instance}

    def gate(self, module: str) -> "TaskGate":
        if not re.match(r"^[a-z][a-z0-9_]{0,30}$", module or ""):
            raise TaskError(f"module invalide : {module!r}")
        return TaskGate(self, module)

    # --- Fil de fond -------------------------------------------------------------------------------------------

    def start(self, poll: float = 1.0) -> None:
        if self._thread is not None:
            return
        self.recover()
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(poll):
                try:
                    self.run_due()
                except Exception:  # noqa: BLE001 - le fil ne meurt jamais
                    log.exception("Tâches : tour en échec")

        self._thread = threading.Thread(target=loop, name="taches", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)


class TaskGate:
    """Ce qu'un module reçoit : créer et annuler ses propres initiatives (principal ``module:<nom>`` fixé)."""

    __slots__ = ("_service", "module")

    def __init__(self, service: TaskService, module: str):
        self._service, self.module = service, module

    def schedule(self, kind: str, params: dict | None = None, *, run_at: float | None = None,
                 interval: float | None = None, expires_at: float | None = None, max_attempts: int = 3,
                 key: str | None = None) -> Task:
        return self._service._schedule_for_module(self.module, kind, params, run_at, interval,  # noqa: SLF001
                                                  expires_at, max_attempts, key)

    def cancel(self, task_id: str) -> bool:
        return self._service._cancel(task_id, f"module:{self.module}", principal=f"module:{self.module}")  # noqa: SLF001
