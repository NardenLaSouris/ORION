"""Coût de production d'ORION, côté IA : tokens consommés par les sessions de développement (Claude Code).

Lit les transcriptions locales des projets ORION (``~/.claude/projects/*jarvis*``, ``*orion*``, sous-agents compris)
et n'en garde que des nombres : tokens par modèle et par catégorie, période couverte, temps de travail actif. Aucun
texte des conversations n'est copié. Le résultat est un petit JSON lu par le fond d'écran
(``data/production_stats.json``) :

  python scripts/production_stats.py --out production_stats.json

Une réponse peut apparaître plusieurs fois dans une transcription (diffusion par morceaux) : elle est comptée une fois,
avec sa consommation la plus élevée. Le temps actif additionne les écarts de moins de 30 minutes entre deux messages.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

GAP = 30 * 60  # au-delà, pause : le temps n'est pas compté
FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_5m_tokens", "cache_1h_tokens")


def transcripts(root: Path) -> list[Path]:
    found = []
    for project in root.iterdir() if root.is_dir() else []:
        name = project.name.lower()
        if project.is_dir() and ("jarvis" in name or "orion" in name):
            found += sorted(project.rglob("*.jsonl"))
    return found


def _stamp(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def collect(files: list[Path]) -> dict:
    usage: dict[str, dict] = {}  # identifiant de réponse -> (modèle, consommation)
    stamps: list[float] = []
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                at = _stamp(entry.get("timestamp", ""))
                if at is not None:
                    stamps.append(at)
                message = entry.get("message") if entry.get("type") == "assistant" else None
                if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                    continue
                u = message["usage"]
                split = u.get("cache_creation") or {}
                counts = {"input_tokens": u.get("input_tokens") or 0, "output_tokens": u.get("output_tokens") or 0,
                          "cache_read_input_tokens": u.get("cache_read_input_tokens") or 0,
                          "cache_5m_tokens": split.get("ephemeral_5m_input_tokens", u.get(
                              "cache_creation_input_tokens") or 0) if split else u.get(
                              "cache_creation_input_tokens") or 0,
                          "cache_1h_tokens": split.get("ephemeral_1h_input_tokens") or 0}
                key = message.get("id") or entry.get("requestId") or entry.get("uuid")
                previous = usage.get(key)
                if previous is None or sum(counts.values()) > sum(previous["counts"].values()):
                    usage[key] = {"model": message.get("model") or "inconnu", "counts": counts}
    models: dict[str, dict] = {}
    for item in usage.values():
        total = models.setdefault(item["model"], dict.fromkeys(FIELDS, 0) | {"responses": 0})
        total["responses"] += 1
        for field in FIELDS:
            total[field] += item["counts"][field]
    stamps.sort()
    active = sum(b - a for a, b in zip(stamps, stamps[1:]) if b - a < GAP)
    return {"format": 1, "generated_at": time.time(), "first": stamps[0] if stamps else None,
            "last": stamps[-1] if stamps else None, "active_hours": round(active / 3600, 1),
            "transcripts": len(files), "models": models}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path(os.path.expanduser("~/.claude/projects")))
    parser.add_argument("--out", type=Path, default=Path("production_stats.json"))
    args = parser.parse_args(argv)
    files = transcripts(args.root)
    if not files:
        print(f"Aucune transcription ORION dans {args.root}", file=sys.stderr)
        return 1
    stats = collect(files)
    args.out.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    tokens = sum(sum(m[f] for f in FIELDS) for m in stats["models"].values())
    print(f"{len(files)} transcription(s), {tokens:,} tokens, {stats['active_hours']} h actives -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
