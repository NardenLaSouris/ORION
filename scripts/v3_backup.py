"""Sauvegarde, vérification et restauration des données de l'environnement V3 (jamais de la production).

    python scripts/v3_backup.py backup  [--data /opt/orion-v3/data] [--out ~/sauvegardes/orion-v3] [--keep 14]
    python scripts/v3_backup.py verify  <dossier de sauvegarde>
    python scripts/v3_backup.py restore <dossier de sauvegarde> [--data /opt/orion-v3/data]

- **Bases SQLite** copiées à chaud par l'API de sauvegarde de SQLite (copie cohérente même service en marche, journal
  WAL compris), puis contrôlées (``PRAGMA integrity_check``) ; autres fichiers (JSON, JSONL) copiés tels quels.
- **Manifeste** : résumé SHA-256 et taille de chaque fichier ; ``verify`` les recontrôle.
- **Rotation** : seules les ``--keep`` dernières sauvegardes restent.
- **Restauration** : refusée si le service ``orion-v3`` tourne ou si la sauvegarde ne se vérifie pas ; les fichiers
  actuels sont mis de côté (``avant-restauration-<date>``), jamais effacés.
- **Production** : tout chemin sous ``/opt/orion`` (hors ``/opt/orion-v3``) est refusé.
La base personnelle (F4) est sauvegardée telle quelle, chiffrée ; aucune clé n'est jamais copiée.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DATA = Path("/opt/orion-v3/data")
DEFAULT_OUT = Path.home() / "sauvegardes" / "orion-v3"
DATABASES = (".db", ".sqlite")
FILES = (".json", ".jsonl")
SKIP = ("-wal", "-shm", ".tmp")
SERVICE = "orion-v3"


def _guard(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    text = str(resolved)
    if (text == "/opt/orion" or text.startswith("/opt/orion/")) or text.startswith("/opt/jarvis"):
        raise SystemExit(f"Refusé : {resolved} appartient à la production.")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA integrity_check").fetchone()[0]


def backup(data: Path, out: Path, keep: int) -> Path:
    data, out = _guard(data), _guard(out)
    if not data.is_dir():
        raise SystemExit(f"Dossier de données absent : {data}")
    target = out / time.strftime("%Y-%m-%dT%H%M%S")
    target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)
    manifest = {"format": 1, "source": str(data), "created_at": time.time(), "files": {}}
    for item in sorted(data.iterdir()):
        if not item.is_file() or item.name.endswith(SKIP):
            continue
        copy = target / item.name
        if item.suffix in DATABASES:
            with sqlite3.connect(f"file:{item}?mode=ro", uri=True) as source, sqlite3.connect(copy) as dest:
                source.backup(dest)
                dest.execute("PRAGMA journal_mode=DELETE")  # copie en un seul fichier, sans -wal ni -shm
            state = _integrity(copy)
            if state != "ok":
                raise SystemExit(f"Copie de {item.name} incohérente : {state}")
        elif item.suffix in FILES:
            shutil.copy2(item, copy)
        else:
            continue
        copy.chmod(0o600)
        manifest["files"][item.name] = {"sha256": _sha256(copy), "bytes": copy.stat().st_size}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    old = sorted(p for p in out.iterdir() if p.is_dir() and (p / "manifest.json").exists())
    for stale in old[:-keep] if keep > 0 else []:
        shutil.rmtree(stale)
    return target


def verify(folder: Path) -> list[str]:
    """Problèmes trouvés (liste vide : sauvegarde intacte)."""
    folder = _guard(folder)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["manifeste absent ou illisible"]
    problems = []
    for name, entry in manifest.get("files", {}).items():
        path = folder / name
        if not path.is_file():
            problems.append(f"{name} : absent")
        elif _sha256(path) != entry["sha256"]:
            problems.append(f"{name} : résumé différent")
        elif path.suffix in DATABASES and _integrity(path) != "ok":
            problems.append(f"{name} : base incohérente")
    return problems


def _service_active() -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
    except FileNotFoundError:
        return False


def restore(folder: Path, data: Path, *, active=_service_active) -> Path:
    folder, data = _guard(folder), _guard(data)
    if active():
        raise SystemExit(f"Arrêtez d'abord {SERVICE} : sudo systemctl stop {SERVICE}")
    problems = verify(folder)
    if problems:
        raise SystemExit("Sauvegarde refusée : " + "; ".join(problems))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    aside = data / time.strftime("avant-restauration-%Y-%m-%dT%H%M%S")
    aside.mkdir()
    for name in manifest["files"]:
        for suffix in ("", "-wal", "-shm"):
            current = data / f"{name}{suffix}"
            if current.exists():
                current.rename(aside / current.name)
        restored = data / name
        shutil.copy2(folder / name, restored)
        restored.chmod(0o660)  # groupe orion-v3 : le service doit pouvoir écrire
    return aside


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--data", type=Path, default=DEFAULT_DATA)
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--keep", type=int, default=14)
    v = sub.add_parser("verify")
    v.add_argument("folder", type=Path)
    r = sub.add_parser("restore")
    r.add_argument("folder", type=Path)
    r.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args(argv)
    if args.command == "backup":
        target = backup(args.data, args.out, args.keep)
        problems = verify(target)
        print(f"Sauvegarde {target} : " + ("vérifiée" if not problems else "; ".join(problems)))
        return 1 if problems else 0
    if args.command == "verify":
        problems = verify(args.folder)
        print("Intacte." if not problems else "\n".join(problems))
        return 1 if problems else 0
    aside = restore(args.folder, args.data)
    print(f"Restaurée ; anciens fichiers mis de côté dans {aside}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
