"""Interrupteur d'autonomie centralisé (CORE FOUNDATION F1).

Trois états, du plus permissif au plus strict :
- ``active`` : les actions autonomes autorisées s'exécutent (phase 1 : lecture, analyse, notification) ;
- ``suspended`` : les initiatives des modules (``module:*``) sont refusées ; les tâches que vous avez programmées
  (``delegated:*`` : routine, surveillance, mandat) continuent avec leurs droits ;
- ``emergency_stop`` : arrêt immédiat de toute action autonome, tâches programmées comprises ; le planificateur des
  modules ne lance plus rien.

La conversation (``user``) et ORION Control (``admin``) ne sont jamais concernés.

Règles (codées ici, vérifiées par les tests) :
- état par défaut : ``suspended`` (première mise en service) ;
- état illisible ou inconnu : ``None`` (indéterminé) ; le moteur de permissions refuse alors toute action autonome ;
- rendre l'état **plus strict** : la voix ou ORION Control ;
- rendre l'état **moins strict** (réactiver) : ORION Control seul, session authentifiée et phrase de confirmation
  explicite ; la voix ne réactive jamais ;
- chaque changement est inscrit au registre d'actions ; l'état est relu en base à chaque décision (aucun cache).
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable

from orion.platform.store import Migration, Store

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE autonomy_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL,
            since REAL NOT NULL,
            changed_by TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        )
    """),
)

VOICE, CONTROL, SYSTEM = "voix", "control", "système"
CONFIRMATION_PHRASE = "réactiver l'autonomie"


class AutonomyState(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EMERGENCY_STOP = "emergency_stop"

    @property
    def strictness(self) -> int:
        return {"active": 0, "suspended": 1, "emergency_stop": 2}[self.value]

    @property
    def label(self) -> str:
        return {"active": "active", "suspended": "suspendue", "emergency_stop": "arrêt d'urgence"}[self.value]


DEFAULT = AutonomyState.SUSPENDED


class AutonomyError(PermissionError):
    pass


class AutonomySwitch:
    def __init__(self, store: Store, ledger=None, clock: Callable[[], float] = time.time):
        self._store, self._ledger, self._clock = store, ledger, clock
        self._lock = threading.Lock()
        self._listeners: list[Callable[[AutonomyState | None, AutonomyState], None]] = []
        store.migrate("autonomy", MIGRATIONS)

    def on_change(self, listener: Callable[[AutonomyState | None, AutonomyState], None]) -> None:
        self._listeners.append(listener)

    def state(self) -> AutonomyState | None:
        """État courant relu en base ; ``DEFAULT`` s'il n'a jamais été réglé ; None s'il est illisible."""
        try:
            row = self._store.one("SELECT state FROM autonomy_state WHERE id = 1")
            return DEFAULT if row is None else AutonomyState(row["state"])
        except Exception:  # noqa: BLE001 - base en panne ou valeur inconnue : indéterminé, donc refus
            log.exception("Autonomie : état illisible")
            return None

    def snapshot(self) -> dict:
        state = self.state()
        row = None
        try:
            row = self._store.one("SELECT since, changed_by, reason FROM autonomy_state WHERE id = 1")
        except Exception:  # noqa: BLE001
            pass
        return {"state": state.value if state else "indéterminé", "label": state.label if state else "indéterminé",
                "since": row["since"] if row else None, "changed_by": row["changed_by"] if row else "",
                "reason": row["reason"] if row else "", "default": DEFAULT.value}

    # --- Changements -----------------------------------------------------------------------------------------

    def suspend(self, by: str, reason: str = "") -> AutonomyState:
        """Suspendre les initiatives autonomes (voix ou ORION Control). Sans effet sur un arrêt d'urgence."""
        current = self.state()
        if current is AutonomyState.EMERGENCY_STOP:
            return current
        return self._set(AutonomyState.SUSPENDED, by, reason)

    def emergency_stop(self, by: str, reason: str = "") -> AutonomyState:
        """Arrêt d'urgence (voix ou ORION Control) : prioritaire sur tout, effet immédiat."""
        return self._set(AutonomyState.EMERGENCY_STOP, by, reason)

    def change(self, target: AutonomyState | str, by: str, reason: str = "", *, confirmation: str = "",
               session: str = "") -> AutonomyState:
        """Changement quelconque. Vers un état moins strict : ORION Control seul, session authentifiée (``session``
        fournie par l'API après contrôle du jeton) et phrase de confirmation exacte."""
        target = AutonomyState(target)
        current = self.state()
        loosening = current is None or target.strictness < current.strictness
        if loosening:
            if by != CONTROL:
                self._refused(target, by, "seul ORION Control réactive l'autonomie")
            if not session:
                self._refused(target, by, "session ORION Control non authentifiée")
            if confirmation.strip().lower() != CONFIRMATION_PHRASE:
                self._refused(target, by, "confirmation explicite absente")
        return self._set(target, by, reason)

    def _refused(self, target: AutonomyState, by: str, why: str) -> None:
        if self._ledger is not None:
            self._ledger.append(by, "AUTONOMY", f"autonomy:{target.value}", "deny", why)
        raise AutonomyError(why)

    def _set(self, target: AutonomyState, by: str, reason: str) -> AutonomyState:
        if by not in (VOICE, CONTROL, SYSTEM):
            raise AutonomyError(f"origine inconnue : {by!r}")
        with self._lock:
            previous = self.state()
            self._store.write("INSERT INTO autonomy_state (id, state, since, changed_by, reason) VALUES (1, ?, ?, ?, ?) "
                              "ON CONFLICT(id) DO UPDATE SET state = excluded.state, since = excluded.since, "
                              "changed_by = excluded.changed_by, reason = excluded.reason",
                              (target.value, self._clock(), by, str(reason or "")[:200]))
            if self._ledger is not None:
                self._ledger.append(by, "AUTONOMY", f"autonomy:{target.value}", "allow",
                                    f"{previous.value if previous else 'indéterminé'} -> {target.value}")
        log.warning("Autonomie : %s -> %s (%s)", previous.value if previous else "indéterminé", target.value, by)
        for listener in list(self._listeners):
            try:
                listener(previous, target)
            except Exception:  # noqa: BLE001 - un abonné en panne n'empêche jamais l'arrêt
                log.exception("Autonomie : abonné en échec")
        return target
