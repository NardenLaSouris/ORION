"""Outils de la migration de Frag Factory vers son installation persistante (deploy/pc_fragfactory_persistant.ps1).

    python -I pc_fragfactory_migration.py backup <base> <copie.sqlite3>         copie cohérente vérifiée + comptes
    python -I pc_fragfactory_migration.py manifest <dossier> <sortie.json>      SHA-256 et taille de chaque fichier
    python -I pc_fragfactory_migration.py compare  <a.json> <b.json>            identiques, sinon code 1 et écarts
    python -I pc_fragfactory_migration.py inventory <base.sqlite3>              intégrité et comptes (JSON)
    python -I pc_fragfactory_migration.py fix <dossier cible> <ancienne racine> <nouvelle racine>

``fix`` ne s'applique qu'à la COPIE : chemins absolus de l'ancienne racine réécrits (pistes audio d'analyse), rendus en
attente mis en pause, rendu automatique coupé (config.toml et data/settings_override.json, sauvegardés à côté).
Aucun contenu de transcription n'est jamais affiché : seuls des comptes sortent de ce programme.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

COUNTED = ("recordings", "audio_tracks", "candidates", "transcripts", "events", "kill_events", "decisions", "renders",
           "publish_packages", "jobs", "orion_outbox", "orion_requests", "style_profiles", "style_references",
           "team_intervals", "trends", "rework_requests", "reports")
SPLITS = {"recordings": "status", "candidates": "tier", "jobs": "status"}


def _long(folder: Path) -> str:
    # L'ancienne installation est profonde (stockage du paquet Claude) : au-delà de 260 caractères, Windows ignore
    # des fichiers sans le préfixe de chemin étendu (5 WAV de référence de style manquaient au premier manifeste).
    text = os.path.abspath(folder)
    return "\\\\?\\" + text if os.name == "nt" and not text.startswith("\\\\") else text


def manifest(folder: Path) -> dict[str, list]:
    out = {}
    root = _long(folder)
    for current, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(current, name)
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            out[os.path.relpath(path, root).replace("\\", "/")] = [os.path.getsize(path), digest.hexdigest()]
    return dict(sorted(out.items()))


def compare(a: dict, b: dict) -> list[str]:
    problems = [f"absent de la copie : {k}" for k in a if k not in b]
    problems += [f"en trop dans la copie : {k}" for k in b if k not in a]
    problems += [f"différent : {k}" for k in a if k in b and a[k] != b[k]]
    return problems


def inventory(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        report = {"integrite": con.execute("PRAGMA integrity_check").fetchone()[0]}
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in COUNTED:
            if table in names:
                report[table] = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table, column in SPLITS.items():
            if table in names:
                report[f"{table}.{column}"] = dict(con.execute(f'SELECT "{column}", count(*) FROM "{table}" GROUP BY 1'))
        if "candidates" in names:
            report["candidates.status"] = dict(con.execute("SELECT status, count(*) FROM candidates GROUP BY 1"))
        if "orion_outbox" in names:
            report["orion_outbox.max_seq"] = con.execute("SELECT max(seq) FROM orion_outbox").fetchone()[0]
        return report
    finally:
        con.close()


def _keep(path: Path, stamp: str) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.avant-migration-{stamp}"))


def fix(target: Path, old_root: str, new_root: str) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    data = target / "data"
    db = data / "fragfactory.sqlite3"
    done = {}
    con = sqlite3.connect(db)
    try:
        old_variants = {old_root.replace("\\", "/"), old_root.replace("/", "\\")}
        new = new_root.replace("\\", "/")
        moved = 0
        with con:
            for old in old_variants:
                sep = "/" if "/" in old else "\\"
                rows = con.execute("SELECT id, wav_path FROM audio_tracks WHERE wav_path LIKE ?", (old + "%",)).fetchall()
                for row_id, value in rows:
                    rest = value[len(old):].lstrip("/\\").replace(sep, "/")
                    con.execute("UPDATE audio_tracks SET wav_path = ? WHERE id = ?", (f"{new}/{rest}", row_id))
                    moved += 1
            done["chemins_reecrits"] = moved
            done["chemins_ancienne_racine_restants"] = sum(
                con.execute(f'SELECT count(*) FROM "{t}" WHERE "{c}" LIKE ?', (o + "%",)).fetchone()[0]
                for t, c in (("audio_tracks", "wav_path"), ("renders", "path"), ("renders", "thumb_path"),
                             ("kill_events", "evidence_image"), ("style_references", "path"))
                for o in old_variants)
            paused = con.execute("UPDATE jobs SET status = 'paused', message = 'En pause : rendu sur validation "
                                 "humaine seulement (migration)' WHERE kind = 'render' AND status = 'queued'").rowcount
            done["rendus_mis_en_pause"] = paused
        done["integrite"] = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()

    override = data / "settings_override.json"
    if override.exists():
        values = json.loads(override.read_text(encoding="utf-8"))
        if values.get("render.auto_render_tiers"):
            _keep(override, stamp)
            values["render.auto_render_tiers"] = []
            override.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
            done["override_rendu_auto"] = "coupé"
    config = target / "config.toml"
    text = config.read_text(encoding="utf-8")
    line = re.compile(r"(?m)^auto_render_tiers\s*=.*$")
    if line.search(text) and not re.search(r"(?m)^auto_render_tiers\s*=\s*\[\s*\]", text):
        _keep(config, stamp)
        text = line.sub("auto_render_tiers = []   # rendu seulement sur validation humaine (règle ORION)", text, count=1)
        config.write_text(text, encoding="utf-8")
        done["config_rendu_auto"] = "coupé"
    return done


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, args = argv[0], argv[1:]
    if command == "backup":
        # Copie cohérente par l'API de sauvegarde de SQLite (instance en marche comprise), puis contrôle d'intégrité.
        src, dst = sqlite3.connect(f"file:{args[0]}?mode=ro", uri=True), sqlite3.connect(args[1])
        src.backup(dst)
        src.close()
        dst.close()
        report = inventory(Path(args[1]))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["integrite"] == "ok" else 1
    if command == "manifest":
        result = manifest(Path(args[0]))
        Path(args[1]).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"{len(result)} fichiers, {sum(v[0] for v in result.values()) / 1e6:.1f} Mo")
        return 0
    if command == "compare":
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args[:2])
        problems = compare(a, b)
        for p in problems[:30]:
            print(p)
        print("identiques" if not problems else f"{len(problems)} écart(s)")
        return 1 if problems else 0
    if command == "inventory":
        print(json.dumps(inventory(Path(args[0])), ensure_ascii=False))
        return 0
    if command == "fix":
        result = fix(Path(args[0]), args[1], args[2])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("integrite") == "ok" else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
