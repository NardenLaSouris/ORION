"""Registre d'actions : une ligne par décision de permission et par action qui touche le monde (CORE FOUNDATION F1).

- **Ajout seul.** La classe n'offre ni modification ni suppression ; la base refuse en plus ``UPDATE`` et ``DELETE``
  sur la table (déclencheurs SQLite).
- **Chaînage.** Chaque ligne porte le résumé SHA-256 de la précédente et le sien, calculé sur son contenu canonique :
  ``verify`` relit toute la chaîne et donne la première ligne altérée (modifiée, insérée ou supprimée au milieu).
  Une troncature de la fin ne se voit pas dans la chaîne seule : ``head`` (nombre de lignes et dernier résumé) est
  affiché dans ORION Control et peut être noté ailleurs pour comparaison.
- **Aucun texte privé.** Seuls des noms (principal, capacité, outil), des décisions, des raisons courtes et un petit
  détail sans valeur de paramètre ; tout passe par ``loggable`` (mêmes règles que le journal des outils).
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from orion.platform.store import Migration, Store, dumps, loads

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE action_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at REAL NOT NULL,
            principal TEXT NOT NULL,
            capability TEXT NOT NULL,
            action TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}',
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        );
        CREATE INDEX action_ledger_principal ON action_ledger (principal, capability, at);
        CREATE TRIGGER action_ledger_no_update BEFORE UPDATE ON action_ledger
            BEGIN SELECT RAISE(ABORT, 'registre en ajout seul'); END;
        CREATE TRIGGER action_ledger_no_delete BEFORE DELETE ON action_ledger
            BEGIN SELECT RAISE(ABORT, 'registre en ajout seul'); END
    """),
)
GENESIS = "0" * 64
FIELDS = ("at", "principal", "capability", "action", "decision", "reason", "request_id", "detail")
MAX_REASON, MAX_DETAIL = 200, 2000


@dataclass(frozen=True)
class Integrity:
    ok: bool
    count: int
    head: str
    broken_at: int | None = None
    problem: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "count": self.count, "head": self.head, "broken_at": self.broken_at,
                "problem": self.problem}


def entry_hash(prev_hash: str, row: dict) -> str:
    canonical = dumps([prev_hash, *[row[name] for name in FIELDS]])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean(detail: dict[str, Any] | None) -> str:
    from orion.tools.core import loggable

    text = dumps(loggable(detail or {}))
    return text if len(text) <= MAX_DETAIL else dumps({"tronqué": True})


class ActionLedger:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self._store, self._clock = store, clock
        self._lock = threading.Lock()
        store.migrate("ledger", MIGRATIONS)

    def append(self, principal: str, capability: str, action: str, decision: str, reason: str = "",
               request_id: str = "", detail: dict[str, Any] | None = None) -> int:
        """Ajoute une ligne chaînée à la précédente ; rend son identifiant."""
        row = {"at": round(self._clock(), 6), "principal": str(principal)[:80], "capability": str(capability)[:40],
               "action": str(action)[:80], "decision": str(decision)[:20], "reason": str(reason)[:MAX_REASON],
               "request_id": str(request_id or "")[:40], "detail": _clean(detail)}
        with self._lock, self._store.transaction():
            last = self._store.one("SELECT hash FROM action_ledger ORDER BY id DESC LIMIT 1")
            prev = last["hash"] if last else GENESIS
            return self._store.write(
                "INSERT INTO action_ledger (at, principal, capability, action, decision, reason, request_id, detail, "
                "prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*[row[name] for name in FIELDS], prev, entry_hash(prev, row)))

    def verify(self) -> Integrity:
        """Relit toute la chaîne : identifiants contigus, chaque ligne liée à la précédente et intacte."""
        prev, count, expected_id = GENESIS, 0, None
        for row in self._store.query("SELECT * FROM action_ledger ORDER BY id"):
            if expected_id is not None and row["id"] != expected_id:
                return Integrity(False, count, prev, row["id"], "ligne supprimée avant celle-ci")
            if row["prev_hash"] != prev:
                return Integrity(False, count, prev, row["id"], "chaînage rompu")
            if entry_hash(prev, row) != row["hash"]:
                return Integrity(False, count, prev, row["id"], "contenu modifié")
            prev, count, expected_id = row["hash"], count + 1, row["id"] + 1
        return Integrity(True, count, prev)

    def head(self) -> dict:
        row = self._store.one("SELECT COUNT(*) AS n, MAX(id) AS last FROM action_ledger")
        last = self._store.one("SELECT hash FROM action_ledger ORDER BY id DESC LIMIT 1")
        return {"count": int(row["n"]), "last_id": row["last"], "hash": last["hash"] if last else GENESIS}

    def list(self, limit: int = 100, principal: str | None = None, before: int | None = None) -> list[dict]:
        where, params = [], []
        if principal:
            where.append("principal = ?")
            params.append(principal)
        if before:
            where.append("id < ?")
            params.append(int(before))
        sql = "SELECT * FROM action_ledger" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._store.query(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 500))))
        return [{**row, "detail": loads(row["detail"], {})} for row in rows]

    def count_since(self, principal: str, capability: str, since: float, decision: str = "allow",
                    exclude_action: str = "") -> int:
        """Décisions permises depuis ``since``, hors ``exclude_action`` : l'action en cours de décision (un achat
        vérifié à la préparation puis au paiement ne compte pas contre lui-même)."""
        row = self._store.one("SELECT COUNT(*) AS n FROM action_ledger WHERE principal = ? AND "
                              "capability = ? AND decision = ? AND at >= ? AND action != ?",
                              (principal, capability, decision, since, exclude_action))
        return int(row["n"]) if row else 0
