"""Historique commun des modules : qui a fait quoi, sur quel élément, quand (affiché dans ORION Control).

Une ligne par fait (création, vérification, déclenchement, refus, erreur…) ; jamais modifiée ni réécrite. Le détail
est un petit dictionnaire JSON ; les textes privés n'y vont pas (les modules n'y mettent que des résumés).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from orion.platform.store import Migration, Store, dumps, loads

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE module_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            item TEXT NOT NULL DEFAULT '',
            at REAL NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX module_history_item ON module_history (module, item, at)
    """),
)
MAX_DETAIL = 4000


class History:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self._store = store
        self._clock = clock
        store.migrate("history", MIGRATIONS)

    def record(self, module: str, item: str, kind: str, detail: dict[str, Any] | None = None) -> int:
        text = dumps(detail or {})
        if len(text) > MAX_DETAIL:
            text = dumps({"tronqué": True, "début": text[:MAX_DETAIL - 100]})
        return self._store.write("INSERT INTO module_history (module, item, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
                                   (module, item or "", self._clock(), kind, text))

    def list(self, module: str | None = None, item: str | None = None, limit: int = 100) -> list[dict]:
        where, params = [], []
        if module:
            where.append("module = ?")
            params.append(module)
        if item:
            where.append("item = ?")
            params.append(item)
        sql = "SELECT * FROM module_history" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._store.query(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 1000))))
        return [{**row, "detail": loads(row["detail"], {})} for row in rows]
