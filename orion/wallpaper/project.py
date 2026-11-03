"""Statistiques du projet ORION pour le fond d'écran : poids sur disque et estimation du coût de production.

- **Poids** : tout ce qu'occupe l'installation (code, environnement Python, modèles, données), parcouru au plus une
  fois par heure.
- **Coût de production**, deux parts, toutes deux des estimations :
  - IA : tokens des sessions de développement (``scripts/production_stats.py``, fichier ``production_stats.json``)
    au tarif public par million de tokens ;
  - travail : lignes de code et de tests du dépôt, au rythme d'un développeur qui les écrit, les teste et les
    documente (``dev_lines_per_day``), payé ``dev_net_monthly`` euros nets par mois (21 jours travaillés).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

# Tarif public en dollars par million de tokens (entrée, sortie, lecture du cache) ; écriture du cache : 1,25 fois
# l'entrée pour 5 minutes, 2 fois pour 1 heure. Modèle inconnu : tarif d'Opus 5.5.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-opus-5": (5.0, 25.0, 0.50), "claude-opus-4-8": (5.0, 25.0, 0.50),
          "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-sonnet-5": (2.0, 10.0, 0.20),
          "claude-haiku-5-5": (0.10, 0.50, 0.01), "claude-fable-5-1": (10.0, 50.0, 0.25)}
CODE = {".py", ".js", ".html", ".css"}
SKIP = {".git", ".venv", "venv", "models", "data", "node_modules", "__pycache__", ".pytest_cache"}
WORKING_DAYS = 21


def footprint(root: Path) -> dict:
    """Octets par partie de l'installation (liens symboliques non suivis, fichiers illisibles ignorés)."""
    parts: dict[str, int] = {}
    for entry in os.scandir(root):
        name = entry.name
        part = {".venv": "python", "models": "models", "data": "data", ".git": "history"}.get(name, "code")
        parts[part] = parts.get(part, 0) + _size(entry)
    return parts


def _size(entry: os.DirEntry) -> int:
    try:
        if entry.is_symlink():
            return 0
        if entry.is_file(follow_symlinks=False):
            return entry.stat(follow_symlinks=False).st_size
        if entry.is_dir(follow_symlinks=False):
            return sum(_size(child) for child in os.scandir(entry.path))
    except OSError:
        pass
    return 0


def code_lines(root: Path) -> int:
    total = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if Path(name).suffix in CODE:
                try:
                    with open(Path(folder) / name, "rb") as handle:
                        total += sum(1 for line in handle if line.strip())
                except OSError:
                    pass
    return total


def ai_cost(stats: dict) -> float:
    """Coût en dollars des tokens relevés par ``production_stats.py``."""
    total = 0.0
    for model, t in (stats.get("models") or {}).items():
        if model.startswith("<"):  # réponses synthétiques (aucun appel facturé)
            continue
        inp, out, read = PRICES.get(model, PRICES["claude-opus-5-5"])
        total += (t.get("input_tokens", 0) * inp + t.get("output_tokens", 0) * out
                  + t.get("cache_read_input_tokens", 0) * read + t.get("cache_5m_tokens", 0) * inp * 1.25
                  + t.get("cache_1h_tokens", 0) * inp * 2) / 1e6
    return total


def project_source(root: Path, stats_path: Path, net_monthly: float, lines_per_day: float,
                   usd_eur: float) -> Callable[[], dict]:
    def read() -> dict:
        parts = footprint(root)
        lines = code_lines(root)
        days = lines / max(1.0, lines_per_day)
        labour = days * net_monthly / WORKING_DAYS
        try:
            stats = json.loads(Path(stats_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stats = {}
        ai_usd = ai_cost(stats)
        tokens = sum(sum(v for k, v in t.items() if k != "responses") for t in (stats.get("models") or {}).values())
        return {"size": sum(parts.values()), "parts": parts, "lines": lines, "dev_days": round(days),
                "labour_eur": round(labour), "ai_usd": round(ai_usd), "tokens": tokens,
                "active_hours": stats.get("active_hours"), "since": stats.get("first"),
                "total_eur": round(labour + ai_usd * usd_eur), "net_monthly": net_monthly}
    return read
