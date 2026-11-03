"""Persistance SQLite des modules V3 : une base, des migrations versionnées par module, des écritures sérialisées.

- Chaque module déclare ses migrations (version 1, 2, 3… contiguës) ; ``migrate`` applique celles qui manquent, chacune
  dans sa transaction : une migration en échec ne laisse rien à moitié appliqué.
- Une base plus récente que le code (retour à une version précédente d'ORION) est refusée plutôt que modifiée.
- Une seule connexion, protégée par un verrou : les fils des modules (planificateur, API) écrivent l'un après l'autre.
- ``backup`` copie la base à chaud (API de sauvegarde de SQLite) avant une opération risquée.

Les secrets n'y entrent jamais (aucune table n'en prévoit) ; les modules sensibles (achats) le vérifient en plus.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger(__name__)

MODULE_NAME = r"^[a-z][a-z0-9_]{0,30}$"

# Lot F4 : trois bases, chacune n'accueille que sa classe de données. Opérationnelle (orion_ops.db) : fonctionnement
# d'ORION, textes personnels scellés (orion.platform.vault.Sealer). Publique (orion_v3.db) : sources publiques et état
# des modules. Personnelle (orion_personal.db) : chiffrée enregistrement par enregistrement.
OPERATIONAL, PUBLIC, PERSONAL = "operational", "public", "personal"
DATA_CLASSES = {
    "ledger": OPERATIONAL, "autonomy": OPERATIONAL, "grants": OPERATIONAL, "confirmations": OPERATIONAL,
    "tasks": OPERATIONAL, "events": OPERATIONAL,
    "history": PUBLIC, "alerts": PUBLIC, "news": PUBLIC, "price_history": PUBLIC, "event_calendar": PUBLIC,
    "watch": PUBLIC, "evolve": PUBLIC, "purchase": PUBLIC, "sentinel": PUBLIC,
    "fragfactory": PUBLIC,  # identifiants de clips et métadonnées courtes ; vidéos et analyses restent chez Frag Factory
    "personal": PERSONAL,
}


class StoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class Store:
    def __init__(self, path: Path | str = ":memory:", data_class: str | None = None):
        self.path = str(path)
        self.data_class = data_class  # None : aucune vérification (tests, outils)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS schema_versions (module TEXT PRIMARY KEY, "
                         "version INTEGER NOT NULL, applied_at REAL NOT NULL)")
        self._depth = 0

    # --- Migrations ----------------------------------------------------------------------------------------

    def version(self, module: str) -> int:
        row = self.one("SELECT version FROM schema_versions WHERE module = ?", (module,))
        return int(row["version"]) if row else 0

    def migrate(self, module: str, migrations: Sequence[Migration]) -> int:
        """Applique les migrations manquantes de ``module`` ; rend sa version finale."""
        import re

        if not re.match(MODULE_NAME, module):
            raise StoreError(f"Nom de module invalide : {module!r}")
        if self.data_class is not None and DATA_CLASSES.get(module) != self.data_class:
            raise StoreError(f"{module} : classe de données {DATA_CLASSES.get(module, 'inconnue')}, refusée dans la base "
                             f"{self.data_class}")
        versions = [m.version for m in migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise StoreError(f"{module} : migrations attendues 1, 2, 3… sans trou (reçu {versions})")
        current = self.version(module)
        if current > len(migrations):
            raise StoreError(f"{module} : base en version {current}, plus récente que ce code ({len(migrations)}) ; "
                             "rien n'est modifié")
        for migration in migrations[current:]:
            with self.transaction():  # pas executescript : il validerait chaque instruction à part
                for statement in _statements(migration.sql):
                    self._db.execute(statement)
                self._db.execute("INSERT INTO schema_versions (module, version, applied_at) VALUES (?, ?, ?) "
                                 "ON CONFLICT(module) DO UPDATE SET version = excluded.version, "
                                 "applied_at = excluded.applied_at", (module, migration.version, time.time()))
            log.info("Base %s : %s en version %d", Path(self.path).name, module, migration.version)
        return len(migrations)

    # --- Accès ---------------------------------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Transaction (imbriquable : seule la plus externe valide ou annule)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._db.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._db.execute("COMMIT")

    def write(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Écriture ; rend le dernier identifiant inséré (ou le nombre de lignes touchées si rien n'est inséré)."""
        with self._lock:
            cursor = self._db.execute(sql, tuple(params))
            return cursor.lastrowid if cursor.lastrowid and sql.lstrip().upper().startswith("INSERT") \
                else cursor.rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        with self._lock:
            row = self._db.execute(sql, tuple(params)).fetchone()
            return dict(row) if row is not None else None

    def backup(self, target: Path | str) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(target)) as copy:
            self._db.backup(copy)
        return target

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _statements(sql: str) -> list[str]:
    """Découpe un script de migration en instructions complètes : un déclencheur (« BEGIN … ; END ») reste entier."""
    statements, buffer = [], ""
    for part in sql.split(";"):
        buffer += part + ";"
        if sqlite3.complete_statement(buffer):
            if buffer.strip().rstrip(";").strip():
                statements.append(buffer.strip().rstrip(";").strip())
            buffer = ""
    if buffer.strip().rstrip(";").strip():
        statements.append(buffer.strip().rstrip(";").strip())
    return statements
