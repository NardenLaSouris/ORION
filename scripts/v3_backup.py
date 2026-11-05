"""Sauvegarde, vérification et restauration des données de l'environnement V3 (jamais de la production).

    python scripts/v3_backup.py backup  [--data /opt/orion-v3/data] [--out ~/sauvegardes/orion-v3] [--keep 14]
    python scripts/v3_backup.py verify  <dossier de sauvegarde>
    python scripts/v3_backup.py restore <dossier de sauvegarde> [--data /opt/orion-v3/data]
    python scripts/v3_backup.py periodic [--data …] [--out ~/sauvegardes/orion-v3] [--status data/backup_status.json]

- **Bases SQLite** copiées à chaud par l'API de sauvegarde de SQLite (copie cohérente même service en marche, journal
  WAL compris), puis contrôlées (``PRAGMA integrity_check``) ; autres fichiers (JSON, JSONL) copiés tels quels.
- **Manifeste** : résumé SHA-256 et taille de chaque fichier ; ``verify`` les recontrôle.
- **Rotation** : seules les ``--keep`` dernières sauvegardes restent.
- **Restauration** : refusée si le service ``orion-v3`` tourne ou si la sauvegarde ne se vérifie pas ; les fichiers
  actuels sont mis de côté (``avant-restauration-<date>``), jamais effacés.
- **Production** : tout chemin sous ``/opt/orion`` (hors ``/opt/orion-v3``) est refusé.
- **Sauvegarde périodique** (``periodic``, minuterie ``orion-v3-backup.timer``, chaque nuit) : une sauvegarde
  vérifiée dans ``quotidiennes/`` (14 gardées), recopiée dans ``hebdomadaires/`` si la dernière y a 7 jours ou plus
  (8 gardées), puis un état ``backup_status.json`` dans le dossier de données, que SENTINEL lit (alerte écrite si la
  dernière sauvegarde manque, a échoué ou date de plus de 26 heures). Les sauvegardes d'avant déploiement restent à
  part, à la racine de ``--out`` : aucune rotation ne touche l'autre.
La base personnelle (F4) est sauvegardée telle quelle, chiffrée ; aucune clé n'est jamais copiée.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
# Secrets rangés dans le dossier de données (jeton de connexion Spotify, en 600 au compte orion-v3) : jamais
# sauvegardés, comme les clés ; une nouvelle connexion les recrée. Notés comme exclus dans le manifeste.
SECRET_FILES = frozenset({"spotify_token.json"})
SERVICE = "orion-v3"
STATUS_NAME = "backup_status.json"  # état de la sauvegarde périodique : jamais sauvegardé lui-même
DAILY, WEEKLY = "quotidiennes", "hebdomadaires"
WEEK_SECONDS = 7 * 86400 - 3600  # une heure de marge : la minuterie peut glisser de quelques minutes


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
    manifest = {"format": 1, "source": str(data), "created_at": time.time(), "files": {}, "excluded": []}
    for item in sorted(data.iterdir()):
        if not item.is_file() or item.name.endswith(SKIP) or item.name == STATUS_NAME:
            continue
        if item.name in SECRET_FILES:
            manifest["excluded"].append(item.name)
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


def _created(folder: Path) -> float:
    try:
        return float(json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["created_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def _backups(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def _size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


def write_status(path: Path, status: dict) -> None:
    """Écrit l'état à côté puis le renomme : SENTINEL ne lit jamais un fichier à moitié écrit."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.chmod(0o640)  # groupe orion-v3 (dossier en setgid) : le service le lit, personne d'autre
    os.replace(temp, path)


def periodic(data: Path, out: Path, status_path: Path | None, keep: int = 14, weekly_keep: int = 8,
             clock=time.time) -> dict:
    """Sauvegarde de la nuit : quotidienne vérifiée, hebdomadaire si due, état pour SENTINEL. Ne lève jamais :
    un échec est écrit dans l'état (SENTINEL prévient), et la sortie vaut 1."""
    status = {"format": 1, "at": clock(), "ok": False, "folder": "", "problems": [], "bytes": 0, "files": 0,
              "weekly": "", "weekly_at": 0.0}
    try:
        out = _guard(out)
        for folder in (out / DAILY, out / WEEKLY):
            folder.mkdir(parents=True, exist_ok=True)
            folder.chmod(0o700)
        target = backup(data, out / DAILY, keep)
        problems = verify(target)
        status.update(folder=f"{DAILY}/{target.name}", problems=problems, ok=not problems, bytes=_size(target),
                      files=len(json.loads((target / "manifest.json").read_text(encoding="utf-8"))["files"]))
        weeklies = _backups(out / WEEKLY)
        if not problems and (not weeklies or clock() - _created(weeklies[-1]) >= WEEK_SECONDS):
            shutil.copytree(target, out / WEEKLY / target.name, copy_function=shutil.copy2)
            weeklies = _backups(out / WEEKLY)
            for stale in weeklies[:-weekly_keep] if weekly_keep > 0 else []:
                shutil.rmtree(stale)
            weeklies = _backups(out / WEEKLY)
        if weeklies:
            status.update(weekly=f"{WEEKLY}/{weeklies[-1].name}", weekly_at=_created(weeklies[-1]))
    except SystemExit as exc:  # backup() et _guard() refusent par SystemExit, avec un message en clair
        status["problems"] = [str(exc.code)]
    except (OSError, sqlite3.Error) as exc:
        status["problems"] = [f"{type(exc).__name__}: {exc}"[:300]]
    if status_path is not None:
        write_status(status_path, status)
    return status


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
    p = sub.add_parser("periodic")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--keep", type=int, default=14)
    p.add_argument("--weekly-keep", type=int, default=8)
    p.add_argument("--status", type=Path, default=DEFAULT_DATA / STATUS_NAME)
    args = parser.parse_args(argv)
    if args.command == "periodic":
        status = periodic(args.data, args.out, args.status, args.keep, args.weekly_keep)
        print(f"Sauvegarde {status['folder'] or '—'} : " + ("vérifiée" if status["ok"] else "; ".join(status["problems"]))
              + (f" ; hebdomadaire : {status['weekly']}" if status["weekly"] else ""))
        return 0 if status["ok"] else 1
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
