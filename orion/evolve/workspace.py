"""Espace de travail isolé d'EVOLVE : un clone du dépôt, jamais l'installation active.

- Clone local (``git clone --no-hardlinks``) dans le dossier configuré, vérifié par la politique
  (orion/evolve/policy.py).
- Une branche « evolve/<id> » par correctif, partie de la branche suivie ; jamais de travail sur une autre branche.
- Modifications par remplacement exact (le texte cherché doit apparaître une seule fois), chemins vérifiés.
- Vérifications avant tout commit : tests, compilation de tout le code, contrôle de sécurité du diff (lignes
  dangereuses, secrets, fichiers protégés, dépendances). Un échec interdit le commit.
- Commandes : liste blanche (git, l'interpréteur Python), sans shell, environnement débarrassé des secrets, délai.
- Retour arrière : rien n'est fusionné ni déployé ; abandonner un correctif = revenir à la branche suivie et supprimer
  la branche « evolve/<id> » (``discard``), ce qui laisse le dépôt exactement comme avant.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orion.evolve.policy import SAFE_BRANCH, PolicyError, check_path, check_workspace, dangerous_lines, inside

log = logging.getLogger(__name__)

SECRET_ENV = re.compile(r"(?i)token|secret|password|passwd|pass|key|credential|auth|cookie")
ALLOWED = {"git"}
AUTHOR = ("-c", "user.name=ORION EVOLVE", "-c", "user.email=evolve@orion.local", "-c", "commit.gpgsign=false")
MAX_EDITS, MAX_FILE = 20, 200_000


@dataclass
class CheckReport:
    passed: bool
    steps: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env.update({"HOME": str(home), "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1",
                "GIT_CONFIG_NOSYSTEM": "1"})
    return env


class Workspace:
    def __init__(self, root: Path, source: str, protected_roots: list[Path], *, base: str = "",
                 test_command: list[str] | None = None, timeout: float = 900.0,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.root = check_workspace(Path(root), protected_roots)
        self.repo = self.root / "repo"
        self.source, self.base = source, base
        self.test_command = test_command or [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        self._timeout, self._run = timeout, run

    # --- Commandes -------------------------------------------------------------------------------------------

    def _cmd(self, argv: list[str], cwd: Path | None = None, timeout: float | None = None,
             check: bool = True) -> tuple[int, str]:
        program = argv[0]
        if program not in ALLOWED and program != sys.executable:
            raise PolicyError(f"commande refusée : {program}")
        completed = self._run(argv, cwd=str(cwd or self.repo), env=clean_env(self.root), capture_output=True,
                              text=True, timeout=timeout or self._timeout, check=False)
        output = (completed.stdout or "") + (completed.stderr or "")
        if check and completed.returncode != 0:
            raise RuntimeError(f"{' '.join(argv[:3])} : échec ({completed.returncode}) {output[-300:]}")
        return completed.returncode, output

    def _git(self, *args: str, check: bool = True) -> str:
        return self._cmd(["git", *AUTHOR, *args], check=check)[1]

    # --- Dépôt -----------------------------------------------------------------------------------------------

    def ensure(self) -> Path:
        """Clone le dépôt source s'il n'est pas déjà là ; rend son chemin."""
        if not (self.repo / ".git").exists():
            self.root.mkdir(parents=True, exist_ok=True)
            self._trust_source()
            self._cmd(["git", "clone", "--no-hardlinks", "--quiet", self.source, str(self.repo)], cwd=self.root)
            if self.base:
                self._git("checkout", "--quiet", self.base)
        if not self.base:
            self.base = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        return self.repo

    def _trust_source(self) -> None:
        """Le dépôt source local (/opt/orion-v3) appartient au compte qui déploie, pas à celui d'ORION : git refuse
        alors de le cloner (« dubious ownership »). Il est déclaré sûr dans la configuration de l'espace de travail
        seulement (HOME = ce dossier), pour ce seul chemin."""
        source = Path(self.source)
        if "://" in self.source or not source.is_dir():
            return
        config = self.root / ".gitconfig"
        resolved = source.resolve()
        wanted = [f"\tdirectory = {path.as_posix()}\n" for path in (resolved, resolved / ".git")]
        text = config.read_text(encoding="utf-8") if config.exists() else ""
        missing = [line for line in wanted if line not in text]
        if missing:
            config.write_text(text + ("[safe]\n" if "[safe]" not in text else "") + "".join(missing), encoding="utf-8")

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def start_branch(self, name: str) -> str:
        if not SAFE_BRANCH.match(name):
            raise PolicyError(f"nom de branche refusé : {name!r} (evolve/…)")
        self.ensure()
        self._git("checkout", "--quiet", "--force", self.base)
        self._git("clean", "--quiet", "-fd")
        # à jour avec la source, même revenue en arrière (retour arrière d'un déploiement) : le clone la suit
        self._git("fetch", "--quiet", "origin", check=False)
        if self._git("rev-parse", "--verify", "--quiet", f"origin/{self.base}", check=False).strip():
            self._git("reset", "--quiet", "--hard", f"origin/{self.base}")
        existing = self._git("branch", "--list", name).strip()
        if existing:
            raise PolicyError(f"la branche {name} existe déjà")
        self._git("checkout", "--quiet", "-b", name)
        return name

    def apply(self, edits: list[dict]) -> list[str]:
        """``edits`` : [{"path", "search", "replace"}] (remplacement exact, une occurrence) ou [{"path", "content"}]
        (nouveau fichier de test seulement). Rend les chemins touchés ; PolicyError ou ValueError sinon."""
        if not edits or len(edits) > MAX_EDITS:
            raise ValueError(f"de 1 à {MAX_EDITS} modifications")
        if not self.current_branch().startswith("evolve/"):
            raise PolicyError("modifications permises seulement sur une branche evolve/…")
        touched = []
        for edit in edits:
            path = check_path(edit.get("path", ""))
            target = inside(self.repo, path)
            if "content" in edit:
                if target.exists() or not path.startswith("tests/"):
                    raise PolicyError(f"création permise seulement pour un nouveau fichier de test : {path}")
                content = str(edit["content"])
            else:
                if not target.is_file():
                    raise ValueError(f"fichier introuvable : {path}")
                text = target.read_text(encoding="utf-8")
                search, replace = str(edit.get("search", "")), str(edit.get("replace", ""))
                if not search or text.count(search) != 1:
                    raise ValueError(f"{path} : le texte à remplacer doit apparaître exactement une fois")
                content = text.replace(search, replace)
            if len(content) > MAX_FILE:
                raise ValueError(f"{path} : fichier trop gros")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            touched.append(path)
        return touched

    def changed_files(self) -> list[str]:
        """Fichiers modifiés, ajoutés ou supprimés par rapport à la branche suivie (travail en cours compris)."""
        self._git("add", "--intent-to-add", "--all")
        return [line for line in self._git("diff", "--name-only", self.base).splitlines() if line.strip()]

    def deleted_files(self) -> list[str]:
        return [line for line in self._git("diff", "--name-only", "--diff-filter=D", self.base).splitlines()
                if line.strip()]

    def changed_lines(self) -> int:
        total = 0
        for line in self._git("diff", "--numstat", self.base).splitlines():
            counts = line.split()[:2]  # « ajoutées  supprimées  chemin » ; « - - » pour un fichier binaire
            total += sum(int(n) for n in counts if n.isdigit())
        return total

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def diff(self) -> str:
        self._git("add", "--intent-to-add", "--all")
        return self._git("diff", self.base)

    def check(self) -> CheckReport:
        """Tests, compilation, sécurité du diff. ``passed`` faux au moindre problème."""
        report = CheckReport(True)
        diff = self.diff()
        changed = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
        added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
        for path in changed:
            try:
                check_path(path)
            except PolicyError as exc:
                report.problems.append(str(exc))
        report.problems += [f"ligne refusée ({p})" for p in dangerous_lines(added)]
        if not changed:
            report.problems.append("aucune modification")
        for name, argv in (("compilation", [sys.executable, "-m", "compileall", "-q", "orion", "tests"]),
                           ("tests", list(self.test_command))):
            code, output = self._cmd(argv, check=False)
            report.steps.append({"étape": name, "ok": code == 0, "sortie": output[-1500:]})
            if code != 0:
                report.problems.append(f"{name} en échec")
        report.passed = not report.problems
        report.steps.append({"étape": "sécurité", "ok": not report.problems, "fichiers": changed})
        return report

    def commit(self, message: str, report: CheckReport) -> str:
        if not report.passed:
            raise PolicyError("vérifications en échec : commit refusé")
        branch = self.current_branch()
        if not branch.startswith("evolve/"):
            raise PolicyError("commit permis seulement sur une branche evolve/…")
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", message[:2000])
        return self._git("rev-parse", "HEAD").strip()

    def push(self, remote: str, branch: str) -> None:
        if not remote:
            raise PolicyError("aucun dépôt distant configuré : la Pull Request reste préparée localement")
        if not SAFE_BRANCH.match(branch):
            raise PolicyError(f"branche refusée : {branch}")
        self._git("push", "--quiet", remote, f"{branch}:refs/heads/{branch}")  # jamais --force

    def discard(self, branch: str) -> None:
        """Retour arrière complet : branche suivie, branche evolve supprimée, rien d'autre ne change."""
        if not SAFE_BRANCH.match(branch):
            raise PolicyError(f"branche refusée : {branch}")
        self._git("checkout", "--quiet", "--force", self.base)
        self._git("clean", "--quiet", "-fd")
        self._git("branch", "--quiet", "-D", branch, check=False)

    def read(self, relative: str, line: int, around: int = 6) -> str:
        """Extrait du code (diagnostic) ; jamais un fichier protégé ni hors du dépôt."""
        try:
            target = inside(self.repo, relative)
        except PolicyError:
            return ""
        if not target.is_file():
            return ""
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line - around - 1)
        return "\n".join(f"{n}: {text}" for n, text in enumerate(lines[start:line + around], start + 1))
