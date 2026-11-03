"""Base personnelle chiffrée (CORE FOUNDATION, lot F4 ; docs/v3/foundation.md § « Lot F4 »).

Chaque enregistrement est chiffré à part (AES-256-GCM, ``orion.platform.vault``), lié à sa place par les données
associées (collection et identifiant). En clair, il ne reste que la collection, l'identifiant (opaque), la clé utilisée
et les dates. Sans la clé, la base est illisible ; ORION continue sans elle (état « verrouillée »).

- **Contrôle de clé** : un témoin chiffré par clé. Une clé fausse est détectée à l'ouverture, jamais après coup.
- **Rotation** : nouvelle clé courante dans le trousseau, rechiffrement progressif (``rotate``), puis ``retire`` de
  l'ancienne seulement quand plus rien n'en dépend.
- **Sauvegarde** : copie à chaud de la base (chiffrés seulement) et manifeste (empreinte SHA-256, clés utilisées).
  Refusée dans un dossier qui contient une clé. Indépendante du NAS : un dossier quelconque.
- **Restauration** : vérification du manifeste, puis déchiffrement de chaque enregistrement avant installation ;
  l'éventuelle base en place est gardée à côté, jamais écrasée.
- **Migration JSON** : import idempotent (même identifiant, même résultat), source jamais modifiée ; export inverse
  octet pour octet identique à la source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable

from orion.platform.store import Migration, Store, StoreError
from orion.platform.vault import DataKeyring, Locked, VaultError

log = logging.getLogger(__name__)

MODULE = "personal"
WITNESS = b"orion-personal-v1"
KEY_FILES = ("data_key", "orion.data_key", "orion-v3.data_key")  # noms qu'une sauvegarde ne côtoie jamais

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE personal_records (
            collection TEXT NOT NULL,
            id TEXT NOT NULL,
            key_id TEXT NOT NULL,
            blob BLOB NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (collection, id)
        );
        CREATE INDEX personal_records_key ON personal_records (key_id);
        CREATE TABLE personal_keycheck (
            key_id TEXT PRIMARY KEY,
            blob BLOB NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE personal_imports (
            collection TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            count INTEGER NOT NULL,
            at REAL NOT NULL,
            PRIMARY KEY (collection, sha256)
        )
    """),
)


def _aad(collection: str, record_id: str) -> str:
    return f"personal:{collection}:{record_id}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PersonalStore:
    def __init__(self, store: Store, keyring: DataKeyring | None, reason: str = "",
                 clock: Callable[[], float] = time.time):
        self._store, self._keyring, self._clock = store, keyring, clock
        store.migrate(MODULE, MIGRATIONS)
        self.reason = reason or ("" if keyring else "clé absente")
        if keyring is not None:
            self.reason = self._check(keyring)
            if self.reason:
                self._keyring = None
        if self.reason:
            log.warning("Données personnelles verrouillées : %s", self.reason)

    @property
    def locked(self) -> bool:
        return self._keyring is None

    def _check(self, keyring: DataKeyring) -> str:
        """Vérifie chaque témoin dont la clé est présente ; enregistre celui de la clé courante. "" si tout va bien."""
        for row in self._store.query("SELECT key_id, blob FROM personal_keycheck"):
            if not keyring.has(row["key_id"]):
                continue
            try:
                if keyring.decrypt(bytes(row["blob"]), "personal:keycheck") != WITNESS:
                    return f"clé {row['key_id']} incorrecte"
            except VaultError:
                return f"clé {row['key_id']} incorrecte"
        missing = [r["key_id"] for r in self._store.query("SELECT DISTINCT key_id FROM personal_records")
                   if not keyring.has(r["key_id"])]
        if missing:
            return f"clé(s) {', '.join(sorted(missing))} absente(s) du trousseau"
        if self._store.one("SELECT 1 AS x FROM personal_keycheck WHERE key_id = ?", (keyring.current,)) is None:
            self._store.write("INSERT INTO personal_keycheck (key_id, blob, created_at) VALUES (?, ?, ?)",
                              (keyring.current, keyring.encrypt(WITNESS, "personal:keycheck"), self._clock()))
        return ""

    def _ring(self) -> DataKeyring:
        if self._keyring is None:
            raise Locked(f"données personnelles verrouillées : {self.reason}")
        return self._keyring

    # --- Enregistrements ----------------------------------------------------------------------------------

    def put(self, collection: str, record_id: str, data) -> None:
        ring = self._ring()
        # Ordre des clés conservé (export inverse identique à la source), contrairement à ``dumps`` des modules.
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        blob = ring.encrypt(text.encode("utf-8"), _aad(collection, record_id))
        now = self._clock()
        self._store.write("INSERT INTO personal_records (collection, id, key_id, blob, created_at, updated_at) "
                          "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (collection, id) DO UPDATE SET "
                          "key_id = excluded.key_id, blob = excluded.blob, updated_at = excluded.updated_at",
                          (collection, record_id, ring.current, blob, now, now))

    def get(self, collection: str, record_id: str):
        ring = self._ring()
        row = self._store.one("SELECT blob FROM personal_records WHERE collection = ? AND id = ?",
                              (collection, record_id))
        if row is None:
            return None
        return json.loads(ring.decrypt(bytes(row["blob"]), _aad(collection, record_id)))

    def items(self, collection: str) -> list[tuple[str, object]]:
        ring = self._ring()
        rows = self._store.query("SELECT id, blob FROM personal_records WHERE collection = ? ORDER BY id",
                                 (collection,))
        return [(r["id"], json.loads(ring.decrypt(bytes(r["blob"]), _aad(collection, r["id"])))) for r in rows]

    def delete(self, collection: str, record_id: str) -> bool:
        self._ring()
        return self._store.write("DELETE FROM personal_records WHERE collection = ? AND id = ?",
                                 (collection, record_id)) > 0

    def count(self, collection: str | None = None) -> int:
        sql, params = "SELECT COUNT(*) AS n FROM personal_records", ()
        if collection is not None:
            sql, params = sql + " WHERE collection = ?", (collection,)
        return int(self._store.one(sql, params)["n"])

    def usage(self) -> dict[str, int]:
        return {r["key_id"]: int(r["n"]) for r in
                self._store.query("SELECT key_id, COUNT(*) AS n FROM personal_records GROUP BY key_id")}

    def status(self) -> dict:
        ring = self._keyring
        return {"state": "verrouillée" if ring is None else "ouverte", "reason": self.reason,
                "records": self.count(), "usage": self.usage(),
                "current": ring.current if ring else "", "fingerprints": ring.fingerprints() if ring else {},
                "to_rotate": sum(n for k, n in self.usage().items() if ring and k != ring.current)}

    # --- Rotation -----------------------------------------------------------------------------------------

    def rotate(self, limit: int = 500) -> int:
        """Rechiffre avec la clé courante au plus ``limit`` enregistrements chiffrés par une autre clé."""
        ring = self._ring()
        rows = self._store.query("SELECT collection, id, blob FROM personal_records WHERE key_id != ? LIMIT ?",
                                 (ring.current, limit))
        with self._store.transaction():
            for r in rows:
                aad = _aad(r["collection"], r["id"])
                blob = ring.encrypt(ring.decrypt(bytes(r["blob"]), aad), aad)
                self._store.write("UPDATE personal_records SET key_id = ?, blob = ? WHERE collection = ? AND id = ?",
                                  (ring.current, blob, r["collection"], r["id"]))
        return len(rows)

    def retire(self, key_id: str) -> None:
        """Oublie le témoin d'une ancienne clé ; refusé tant qu'un enregistrement en dépend, ou pour la courante."""
        ring = self._ring()
        if key_id == ring.current:
            raise VaultError("la clé courante ne peut pas être retirée")
        if self.usage().get(key_id):
            raise VaultError(f"{self.usage()[key_id]} enregistrement(s) encore chiffré(s) par {key_id} : rotation d'abord")
        self._store.write("DELETE FROM personal_keycheck WHERE key_id = ?", (key_id,))

    # --- Vérification, sauvegarde, restauration -------------------------------------------------------------

    def verify(self) -> dict:
        """Déchiffre tout : {"records": n, "errors": [...]} (identifiants seulement, jamais de contenu)."""
        ring = self._ring()
        return _verify_rows(self._store.query("SELECT collection, id, blob FROM personal_records"), ring)

    def backup(self, directory: Path | str, name: str = "") -> Path:
        """Sauvegarde chiffrée et son manifeste dans ``directory`` (local, clé USB, NAS monté…) ; rend le manifeste."""
        directory = Path(directory)
        if _holds_key(directory):
            raise VaultError("sauvegarde refusée : ce dossier contient une clé (jamais la clé à côté des chiffrés)")
        directory.mkdir(parents=True, exist_ok=True)
        name = name or time.strftime("orion-personal-%Y%m%d-%H%M%S", time.localtime(self._clock()))
        target = directory / f"{name}.db"
        if target.exists():
            raise VaultError(f"{target.name} existe déjà")
        tmp = directory / f".{name}.db.part"
        copy = sqlite3.connect(str(tmp))
        try:
            with self._store._lock:  # noqa: SLF001 - copie à chaud cohérente (API de sauvegarde de SQLite)
                self._store._db.backup(copy)  # noqa: SLF001
        finally:
            copy.close()
        os.replace(tmp, target)
        manifest = {"format": 1, "file": target.name, "sha256": sha256_file(target), "records": self.count(),
                    "keys": sorted(self.usage()), "created_at": self._clock()}
        path = directory / f"{name}.json"
        path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        return path

    # --- Migration JSON (progressive, réversible) ------------------------------------------------------------

    def import_json(self, collection: str, source: Path | str, record_id: Callable[[dict, int], str]) -> int:
        """Importe une liste JSON (source jamais modifiée) ; même entrée, même identifiant : idempotent."""
        source = Path(source)
        raw = source.read_bytes()
        items = json.loads(raw.decode("utf-8"))
        if not isinstance(items, list):
            raise StoreError("liste JSON attendue")
        ids = [record_id(item, i) for i, item in enumerate(items)]
        if len(set(ids)) != len(ids):
            raise StoreError("identifiants en double dans la source")
        with self._store.transaction():
            for i, (rid, item) in enumerate(zip(ids, items, strict=True)):
                self.put(collection, rid, {"pos": i, "item": item})
            self._store.write("INSERT OR REPLACE INTO personal_imports (collection, sha256, count, at) "
                              "VALUES (?, ?, ?, ?)", (collection, hashlib.sha256(raw).hexdigest(), len(items),
                                                      self._clock()))
        return len(items)

    def export_items(self, collection: str) -> list:
        return [entry["item"] for _, entry in sorted(self.items(collection), key=lambda kv: kv[1]["pos"])]

    def export_json(self, collection: str, target: Path | str, indent: int | None = 1) -> Path:
        """Export inverse (même mise en forme que les fichiers JSON d'ORION) : retour arrière d'une migration."""
        target = Path(target)
        text = json.dumps(self.export_items(collection), ensure_ascii=False, indent=indent)
        target.write_text(text, encoding="utf-8")
        return target


def _verify_rows(rows: Iterable[dict], ring: DataKeyring) -> dict:
    count, errors = 0, []
    for r in rows:
        count += 1
        try:
            json.loads(ring.decrypt(bytes(r["blob"]), _aad(r["collection"], r["id"])))
        except (VaultError, ValueError) as exc:
            errors.append(f"{r['collection']}/{r['id'][:12]} : {exc}")
    return {"records": count, "errors": errors}


def _holds_key(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    credentials = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials and Path(credentials).resolve() == directory.resolve():
        return True
    return any((directory / name).exists() for name in KEY_FILES) or any(directory.glob("*.data_key"))


def restore(manifest: Path | str, target: Path | str, keyring: DataKeyring) -> dict:
    """Restaure une sauvegarde vérifiée vers ``target`` ; une base déjà là est renommée à côté, jamais écrasée."""
    manifest = Path(manifest)
    info = json.loads(manifest.read_text(encoding="utf-8"))
    backup = manifest.parent / info["file"]
    if sha256_file(backup) != info["sha256"]:
        raise VaultError("sauvegarde altérée : empreinte SHA-256 différente du manifeste")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".restauration")
    shutil.copyfile(backup, staging)
    copy = Store(staging)
    try:
        rows = copy.query("SELECT collection, id, blob FROM personal_records")
    finally:
        copy.close()
    result = _verify_rows(rows, keyring)
    if result["errors"] or result["records"] != info["records"]:
        staging.unlink()
        raise VaultError(f"restauration refusée : {len(result['errors'])} enregistrement(s) illisible(s)")
    kept = ""
    if target.exists():
        kept = f"{target.name}.avant-restauration-{time.strftime('%Y%m%d-%H%M%S')}"
        for suffix in ("", "-wal", "-shm"):
            if Path(str(target) + suffix).exists():
                os.replace(str(target) + suffix, str(target.with_name(kept)) + suffix)
    os.replace(staging, target)
    return {"records": result["records"], "kept": kept}
