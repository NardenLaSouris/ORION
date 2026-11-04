#!/usr/bin/env python3
"""Contrôle indépendant d'un changement d'EVOLVE autonome avant déploiement (compte user, hors d'ORION).

    python3 v3_evolve_gate.py --repo ~/orion-dev --base <commit déployé> --commit <commit d'EVOLVE> --log <journal>

Refuse (code 1, raison sur la sortie d'erreur) si : le changement ne part pas exactement du code déployé ; plus de
3 commits ; un fichier protégé ou sensible est touché ; plus de 8 fichiers ou 400 lignes ; un test est supprimé ou
affaibli ; aucun test ajouté ; une ligne dangereuse ; une migration de base (un retour arrière ne pourrait plus
repartir) ; déjà 3 déploiements automatiques en 24 heures. Ce fichier est dans deploy/ : EVOLVE ne peut pas le
modifier (orion/evolve/policy.py, PROTECTED), et c'est la copie déployée qui juge.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import time
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
EDITABLE_ROOTS = ("orion/", "tests/", "docs/", "scripts/")
EDITABLE_SUFFIXES = (".py", ".md", ".toml", ".txt", ".json")
FORBIDDEN = (
    # protégés d'EVOLVE
    "orion/evolve/*", "orion/evolve/**", "orion/purchase/*", "orion/purchase/**", "tests/test_evolve*",
    "tests/test_purchase*", ".env*", "*.env", "*.local.toml", "data/**", "models/**", "deploy/**", ".github/**",
    ".git/**", "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "*.pem", "*.key", "*.p12",
    "*secret*", "*token*", "searxng/**", "jarvis/**",
    # sensibles : jamais sans le propriétaire
    "orion/platform/**", "orion/foundation/**", "orion/control/**", "orion/call/**", "orion/mail/**",
    "orion/winagent/**", "orion/knowledge/**", "orion/tools/core.py", "orion/tools/base.py", "orion/tools/request.py",
    "orion/tools/confirmation.py", "orion/llm/guard.py", "orion/config.py", "orion/api*.py", "*security*",
    "*permission*", "config.toml", "policies/**", "scripts/v3_*",
)
DANGEROUS = (r"shell\s*=\s*True", r"\beval\s*\(", r"\bexec\s*\(", r"os\.system", r"os\.popen", r"pickle\.loads?",
             r"marshal\.loads", r"__import__\s*\(", r"\bsubprocess\b", r"rm\s+-rf", r"ctypes", r"socket\.socket",
             r"\bMigration\s*\(", r"DROP\s+TABLE", r"DELETE\s+FROM", r"chmod|chown", r"setuid|sudo")
MAX_COMMITS, MAX_FILES, MAX_LINES, MAX_PER_DAY = 3, 8, 400, 3


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


def problems(repo: str, base: str, commit: str, log: Path) -> list[str]:
    found = []
    if not SHA.match(base) or not SHA.match(commit):
        return ["identifiants de commit invalides"]
    if subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", base, commit]).returncode != 0:
        return ["le changement ne part pas du code déployé"]
    commits = git(repo, "rev-list", f"{base}..{commit}").split()
    if not 1 <= len(commits) <= MAX_COMMITS:
        found.append(f"{len(commits)} commits")
    status = [line.split("\t") for line in git(repo, "diff", "--name-status", "--no-renames", base, commit).splitlines()]
    files = [(parts[0], parts[-1]) for parts in status if parts and parts[0]]
    if not files:
        found.append("aucun fichier changé")
    for state, path in files:
        low = path.lower()
        if any(fnmatch.fnmatch(low, p) for p in FORBIDDEN) or ".." in path.split("/"):
            found.append(f"fichier interdit : {path}")
        elif not path.startswith(EDITABLE_ROOTS) or not path.endswith(EDITABLE_SUFFIXES):
            found.append(f"fichier hors des dossiers permis : {path}")
        if path.startswith("tests/") and state == "D":
            found.append(f"test supprimé : {path}")
        if path.startswith("tests/") and state == "M":
            removed = [line for line in git(repo, "diff", base, commit, "--", path).splitlines()
                       if line.startswith("-") and not line.startswith("---") and line[1:].strip()]
            if removed:
                found.append(f"test existant affaibli : {path}")
    if len(files) > MAX_FILES:
        found.append(f"{len(files)} fichiers")
    lines = sum(int(n) for row in git(repo, "diff", "--numstat", base, commit).splitlines()
                for n in row.split()[:2] if n.isdigit())
    if lines > MAX_LINES:
        found.append(f"{lines} lignes")
    if not any(path.startswith("tests/") and state in ("A", "M") for state, path in files):
        found.append("aucun test ajouté")
    added = [line[1:] for line in git(repo, "diff", base, commit).splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    for pattern in DANGEROUS:
        if any(re.search(pattern, line, re.IGNORECASE) for line in added):
            found.append(f"ligne refusée ({pattern})")
    if log.exists():
        now = time.time()
        recent = 0
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp, _, rest = line.partition(" ")
            if rest.startswith("déployé") and stamp.isdigit() and now - int(stamp) < 86_400:
                recent += 1
        if recent >= MAX_PER_DAY:
            found.append(f"déjà {recent} déploiements automatiques en 24 heures")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    found = problems(args.repo, args.base, args.commit, Path(args.log))
    if found:
        print("; ".join(found)[:600], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
