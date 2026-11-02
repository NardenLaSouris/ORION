"""Permissions d'ORION EVOLVE, imposées par le code (jamais par les consignes données au LLM).

Niveaux :
1. OBSERVATEUR : détecter, analyser, diagnostiquer, rédiger des rapports et des propositions ;
2. DÉVELOPPEUR : en plus, dans l'espace de travail isolé seulement : branche « evolve/… », modifications, tests,
   commit (si toutes les vérifications passent), Pull Request préparée (poussée seulement si un dépôt distant est
   configuré, jamais en force, jamais sur une branche existante) ;
3. MAINTENEUR : intégration et déploiement. **Refusé dans cette version**, quel que soit le réglage : aucune méthode ne
   fusionne ni ne déploie.

Interdits permanents (``PolicyError``) : modifier ses propres permissions ou contrôles (orion/evolve), les achats
(orion/purchase), les secrets (.env, clés), la configuration locale, les données et sauvegardes (data/), le déploiement
(deploy/), les dépendances (requirements) ; écrire hors de l'espace de travail ; ajouter du code dangereux ou un secret.
Le niveau ne peut être changé que par l'utilisateur (ORION Control), jamais par EVOLVE ni par le LLM.
"""

from __future__ import annotations

import fnmatch
import re
from enum import IntEnum
from pathlib import Path, PurePosixPath


class PolicyError(PermissionError):
    pass


class Level(IntEnum):
    OBSERVER = 1
    DEVELOPER = 2
    MAINTAINER = 3

    @property
    def label(self) -> str:
        return {1: "observateur", 2: "développeur", 3: "mainteneur"}[int(self)]


MAX_LEVEL = Level.DEVELOPER  # niveau 3 : désactivé dans cette version

OBSERVE, DIAGNOSE, PROPOSE = "observer", "diagnostiquer", "proposer"
BRANCH, EDIT, TEST, COMMIT, PULL_REQUEST = "branche", "modifier", "tester", "commit", "pull_request"
MERGE, DEPLOY = "fusionner", "déployer"
REQUIRED = {OBSERVE: Level.OBSERVER, DIAGNOSE: Level.OBSERVER, PROPOSE: Level.OBSERVER,
            BRANCH: Level.DEVELOPER, EDIT: Level.DEVELOPER, TEST: Level.DEVELOPER, COMMIT: Level.DEVELOPER,
            PULL_REQUEST: Level.DEVELOPER, MERGE: Level.MAINTAINER, DEPLOY: Level.MAINTAINER}

PROTECTED = (
    "orion/evolve/*", "orion/evolve/**", "orion/purchase/*", "orion/purchase/**", "tests/test_evolve*",
    "tests/test_purchase*", ".env", ".env.*", "*.env", "config.local.toml", "*.local.toml", "data/*", "data/**",
    "models/*", "models/**", "deploy/*", "deploy/**", ".github/*", ".github/**", ".git/*", ".git/**",
    "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "*.pem", "*.key", "*.p12", "*secret*",
    "*token*", "searxng/config/*", "jarvis/*", "jarvis/**",
)
EDITABLE_ROOTS = ("orion/", "tests/", "docs/", "scripts/")
EDITABLE_SUFFIXES = (".py", ".md", ".toml", ".txt", ".json")
# Lignes ajoutées refusées : exécution libre, shell, désérialisation dangereuse, secrets.
DANGEROUS = (
    (re.compile(r"shell\s*=\s*True"), "subprocess avec shell=True"),
    (re.compile(r"\bos\.system\s*\("), "os.system"),
    (re.compile(r"\bos\.popen\s*\("), "os.popen"),
    (re.compile(r"(?<![\w.])eval\s*\("), "eval"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec"),
    (re.compile(r"\bpickle\.loads?\s*\("), "pickle"),
    (re.compile(r"\bmarshal\.loads?\s*\("), "marshal"),
    (re.compile(r"__import__\s*\("), "__import__"),
    (re.compile(r"\bctypes\b"), "ctypes"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clé privée"),
    (re.compile(r"(?i)\b(?:token|password|passwd|secret|api_key|apikey)\s*=\s*['\"][^'\"]{8,}['\"]"), "secret en clair"),
    (re.compile(r"\b(?:ghp|gho|github_pat|sk|xox[bap])_[A-Za-z0-9_]{16,}"), "jeton en clair"),
    (re.compile(r"(?i)\bpush\b.*(?:--force|-f\b)"), "push forcé"),
    (re.compile(r"(?i)/opt/orion"), "chemin de production"),
)
SAFE_BRANCH = re.compile(r"^evolve/[a-z0-9][a-z0-9-]{2,60}$")


def clamp(level: int) -> tuple[Level, str]:
    """Niveau effectif et remarque ; tout ce qui dépasse le niveau 2 est ramené à 2 (niveau 3 refusé)."""
    try:
        value = Level(int(level))
    except (TypeError, ValueError):
        return Level.OBSERVER, f"niveau {level!r} inconnu : observateur"
    if value > MAX_LEVEL:
        return MAX_LEVEL, "niveau 3 (mainteneur) refusé dans cette version : développeur"
    return value, ""


def require(level: Level, action: str) -> None:
    needed = REQUIRED.get(action)
    if needed is None:
        raise PolicyError(f"action inconnue : {action}")
    if needed > MAX_LEVEL:
        raise PolicyError(f"« {action} » est réservé au niveau 3, désactivé dans cette version")
    if level < needed:
        raise PolicyError(f"« {action} » demande le niveau {needed.label} (niveau actuel : {level.label})")


def check_path(relative: str) -> str:
    """Chemin relatif permis dans l'espace de travail, normalisé ; PolicyError sinon."""
    text = str(relative or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or text.startswith("~") or ":" in text:
        raise PolicyError(f"chemin refusé : {relative!r}")
    normalized = path.as_posix()
    lowered = normalized.lower()
    for pattern in PROTECTED:
        if fnmatch.fnmatch(lowered, pattern) or fnmatch.fnmatch(PurePosixPath(lowered).name, pattern):
            raise PolicyError(f"fichier protégé : {normalized}")
    if not normalized.startswith(EDITABLE_ROOTS) or not normalized.endswith(EDITABLE_SUFFIXES):
        raise PolicyError(f"hors des dossiers modifiables : {normalized}")
    return normalized


def inside(root: Path, relative: str) -> Path:
    """Chemin réel sous ``root`` (liens symboliques résolus) ; PolicyError s'il en sort."""
    target = (root / check_path(relative)).resolve()
    if root.resolve() not in target.parents:
        raise PolicyError(f"le chemin sort de l'espace de travail : {relative}")
    return target


def dangerous_lines(added: list[str]) -> list[str]:
    found = []
    for line in added:
        for pattern, label in DANGEROUS:
            if pattern.search(line):
                found.append(f"{label} : {line.strip()[:120]}")
    return found


def check_workspace(workspace: Path, protected_roots: list[Path]) -> Path:
    """L'espace de travail n'est ni l'installation active, ni un de ses dossiers de code, ni un parent de celle-ci."""
    ws = workspace.resolve()
    for root in protected_roots:
        r = root.resolve()
        if ws == r or r in ws.parents and not _under_data(ws, r) or ws in r.parents:
            raise PolicyError(f"espace de travail refusé : {ws} touche l'installation active {r}")
    return ws


def _under_data(ws: Path, root: Path) -> bool:
    """Seul le dossier de données de l'installation peut contenir l'espace de travail (data/evolve/…)."""
    try:
        return ws.relative_to(root).parts[0] == "data"
    except (ValueError, IndexError):
        return False
