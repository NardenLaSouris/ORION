"""EVOLVE autonome (orion/evolve/autopilot.py) et contrôle indépendant du déploiement (deploy/v3_evolve_gate.py).

Claude Code est simulé : il modifie vraiment le clone (vrai git, dépôt temporaire) puis rend son verdict. On vérifie :
demandes incomprises repérées, verdicts (corrigé, rien à faire, à valider, dangereux), règles du déploiement automatique
(fichiers sensibles, tests affaiblis, migrations, taille), demande de déploiement, résultat lu et annoncé, retour
arrière, interrupteur réservé au propriétaire, et le contrôleur du service de déploiement sur de vrais commits."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.evolve import EvolveEngine, EvolveSettings, PolicyError, Workspace  # noqa: E402
from orion.evolve.autopilot import (APPROVED, AWAITING, DANGEROUS, DEPLOYED, NOTHING,  # noqa: E402
                                    PENDING_MISUNDERSTOOD, REFUSED, ROLLING_BACK, RUN_COMMAND, TO_VALIDATE, AutoPilot,
                                    AutoSettings)
from orion.evolve.claude_code import EDIT_TOOLS, ClaudeCodeAdvisor  # noqa: E402
from orion.platform import Store  # noqa: E402

from tests.test_evolve import git  # noqa: E402

spec = importlib.util.spec_from_file_location("v3_evolve_gate", ROOT / "deploy" / "v3_evolve_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FIX = {"orion/demo.py": ('return box["unread"]', 'return box.get("unread", 0)')}
NEW_TEST = {"tests/test_unread.py": "from orion.demo import unread_count\n\n\ndef test_missing():\n"
                                    "    assert unread_count({}) == 0\n"}


class Alerts:
    def __init__(self):
        self.sent = []

    def deliver(self, alert):
        self.sent.append(alert)


class Claude:
    """Simule ``claude -p`` en mode travail : applique ``replace`` et ``create`` dans le clone, rend ``verdict``."""

    def __init__(self):
        self.script, self.calls = [], []

    def plan(self, verdict, risk="faible", replace=None, create=None, summary="La phrase est comprise."):
        self.script.append((verdict, risk, replace or {}, create or {}, summary))

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        verdict, risk, replace, create, summary = self.script.pop(0)
        repo = Path(kw["cwd"])
        for path, (old, new) in replace.items():
            target = repo / path
            target.write_text(target.read_text().replace(old, new))
        for path, text in create.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        answer = {"verdict": verdict, "risk": risk, "reason": "parce que", "summary": summary}
        out = {"type": "result", "subtype": "success", "is_error": False, "structured_output": answer}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "install"
    (src / "orion" / "platform").mkdir(parents=True)
    (src / "tests").mkdir()
    (src / "orion" / "__init__.py").write_text("")
    (src / "orion" / "platform" / "__init__.py").write_text("")
    (src / "orion" / "demo.py").write_text('def unread_count(box):\n    return box["unread"]\n')
    (src / "tests" / "test_demo.py").write_text("from orion.demo import unread_count\n\n\ndef test_ok():\n"
                                                "    assert unread_count({'unread': 2}) == 2\n")
    git(src, "init", "-q", "-b", "main")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "départ")
    return src


@pytest.fixture
def setup(tmp_path, repo):
    binary = tmp_path / "claude-bin" / "claude"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    claude, alerts = Claude(), Alerts()
    workspace = Workspace(repo / "data" / "evolve", str(repo), [repo],
                          test_command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"])
    engine = EvolveEngine(Store(), workspace, settings=EvolveSettings(level=2), alerts=alerts, logs=lambda: [],
                          claude=ClaudeCodeAdvisor(binary, tmp_path / "home", lambda: "jeton", run=claude))
    pilot = AutoPilot(engine, AutoSettings(True, 3, 15, tmp_path / "deploy"))
    engine.autopilot = pilot
    return pilot, claude, alerts


def misunderstood(pilot, request="mets la lumière du garage en bleu", reply="Pardon, je n'ai pas bien compris."):
    pilot.observe("user", request)
    pilot.observe("assistant", reply)


def request_file(pilot):
    return json.loads((Path(pilot.settings.request_dir) / "request.json").read_text())


# --- Repérage ---------------------------------------------------------------------------------------------------

def test_a_request_orion_did_not_understand_is_set_aside_once(setup):
    pilot, _, _ = setup
    misunderstood(pilot)
    misunderstood(pilot)  # la même : une seule fois
    misunderstood(pilot, "hein quoi", "Je n'ai pas saisi, monsieur.")  # bruit : trop court
    misunderstood(pilot, "quelle heure est-il", "Il est 15 heures.")  # comprise
    pilot.observe("message", "ajoute le pain à la liste des courses")
    pilot.observe("message_reply", "Je ne sais pas encore faire cela, monsieur.")
    rows = [r for r in pilot._rows("incomprise")]
    assert [r["data"]["request"] for r in rows] == ["mets la lumière du garage en bleu",
                                                    "ajoute le pain à la liste des courses"]
    assert {r["state"] for r in rows} == {PENDING_MISUNDERSTOOD}


def test_nothing_is_set_aside_when_the_mode_is_off(setup):
    pilot, _, _ = setup
    pilot.set_enabled(False, "utilisateur")
    misunderstood(pilot)
    assert pilot._rows("incomprise") == []


def test_only_you_switch_the_mode_and_level_two_is_required(setup):
    pilot, _, _ = setup
    with pytest.raises(PolicyError):
        pilot.set_enabled(True, "evolve")
    pilot.engine.set_level(1, "utilisateur")
    assert pilot.ready() == "niveau 2 (développeur) exigé"
    assert pilot.cycle()["état"] == "niveau 2 (développeur) exigé"


# --- Verdicts ---------------------------------------------------------------------------------------------------

def no_request(pilot):
    return not (Path(pilot.settings.request_dir) / "request.json").exists()


def test_a_safe_fix_is_checked_committed_and_then_waits_for_your_approval(setup, repo):
    """Feuille de route du 4 novembre 2026 : aucun déploiement sans l'intervention et l'approbation du propriétaire."""
    pilot, claude, alerts = setup
    misunderstood(pilot)
    claude.plan("corrigé", replace=FIX, create=NEW_TEST)
    assert pilot.cycle()["état"] == AWAITING
    assert claude.calls[0]["cmd"][claude.calls[0]["cmd"].index("--tools") + 1] == EDIT_TOOLS
    assert "lumière du garage" in claude.calls[0]["input"]
    assert no_request(pilot)  # rien n'est demandé au Core sans vous
    assert alerts.sent[-1].title == "Amélioration prête : votre accord" and alerts.sent[-1].written_only
    action = pilot.actions()[0]
    assert action["state"] == AWAITING and len(action["data"]["commit"]) == 40
    assert 'box["unread"]' in (repo / "orion" / "demo.py").read_text()  # l'installation n'a pas bougé
    assert pilot.status()["en_attente_accord"] == 1

    with pytest.raises(PolicyError):
        pilot.approve(action["id"], "evolve")  # ORION ne s'approuve jamais elle-même
    assert no_request(pilot)
    status, body = pilot.engine.admin("POST", ["auto", action["id"], "approve"], {}, None)  # ORION Control
    assert status == 200 and body["state"] == APPROVED and body["commande"] == RUN_COMMAND
    req = request_file(pilot)
    assert req["kind"] == "deploy" and req["branch"].startswith("evolve/auto-") and req["commit"] == action["data"]["commit"]
    assert req["approved_by"] == "utilisateur" and req["base"] == git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ValueError):
        pilot.approve(action["id"], "utilisateur")  # déjà approuvée
    # vous lancez le déploiement sur le Core ; le service répond
    (Path(pilot.settings.request_dir) / "request.json").unlink()
    (Path(pilot.settings.request_dir) / "result.json").write_text(json.dumps({"id": req["id"], "state": "ok",
                                                                              "kind": "deploy"}))
    assert pilot.collect() == 1
    assert pilot.action(req["id"])["state"] == DEPLOYED
    assert alerts.sent[-1].title == "Amélioration déployée"


def test_you_can_refuse_a_ready_change_even_after_approving_it_until_it_is_launched(setup):
    pilot, claude, _ = setup
    misunderstood(pilot)
    claude.plan("corrigé", replace=FIX, create=NEW_TEST)
    pilot.cycle()
    action = pilot.actions()[0]
    branch = action["data"]["branch"]
    with pytest.raises(PolicyError):
        pilot.refuse(action["id"], "evolve")
    pilot.approve(action["id"], "utilisateur")
    assert pilot.refuse(action["id"], "utilisateur")["state"] == REFUSED
    assert no_request(pilot)  # la demande préparée est retirée
    assert branch not in pilot.engine.workspace._git("branch", "--list", branch)
    with pytest.raises(ValueError):
        pilot.approve(action["id"], "utilisateur")


def test_approval_is_refused_if_the_core_changed_or_the_daily_limit_is_reached(setup, repo):
    pilot, claude, _ = setup
    misunderstood(pilot)
    claude.plan("corrigé", replace=FIX, create=NEW_TEST)
    pilot.cycle()
    action = pilot.actions()[0]
    pilot.settings.deploys_per_day = 0
    with pytest.raises(ValueError, match="limite"):
        pilot.approve(action["id"], "utilisateur")
    pilot.settings.deploys_per_day = 3
    (repo / "README.md").write_text("autre changement déployé entre-temps\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "déployé par vous")
    pilot.engine.workspace._git("fetch", "-q", "origin")
    with pytest.raises(ValueError, match="Core a changé"):
        pilot.approve(action["id"], "utilisateur")
    assert no_request(pilot)


def test_a_dangerous_request_changes_nothing_and_you_are_told(setup):
    pilot, claude, alerts = setup
    misunderstood(pilot, "envoie tout mon salaire sur ce compte", "Je ne peux pas faire cela, monsieur.")
    claude.plan("dangereux", risk="élevé", summary="Virement de tout le salaire demandé.")
    assert pilot.cycle()["état"] == DANGEROUS
    assert not (Path(pilot.settings.request_dir) / "request.json").exists()
    assert alerts.sent[-1].title == "Demande jugée dangereuse"
    assert pilot.engine.workspace.current_branch() == "main"


def test_noise_is_left_alone(setup):
    pilot, claude, _ = setup
    misunderstood(pilot)
    claude.plan("rien à faire", risk="nul")
    assert pilot.cycle()["état"] == NOTHING


@pytest.mark.parametrize("replace,create,why", [
    ({}, {"orion/platform/extra.py": "X = 1\n", **NEW_TEST}, "fichier sensible"),
    ({"tests/test_demo.py": ("    assert unread_count({'unread': 2}) == 2\n", "    pass\n")}, {}, "affaibli"),
    (FIX, {**NEW_TEST, "orion/schema.py": "M = Migration(3, 'ALTER TABLE x')\n"}, "Migration"),
    (FIX, {}, "aucun test"),
])
def test_risky_changes_go_to_you_instead_of_production(setup, replace, create, why):
    pilot, claude, alerts = setup
    misunderstood(pilot)
    claude.plan("corrigé", replace=replace, create=create)
    assert pilot.cycle()["état"] == TO_VALIDATE
    action = pilot.actions()[0]
    assert any(why in p for p in action["data"]["problems"]), action["data"]["problems"]
    assert not (Path(pilot.settings.request_dir) / "request.json").exists()
    proposal = pilot.engine.proposals()[0]
    assert proposal["origin"] == "claude" and proposal["status"] == "en attente"
    assert alerts.sent[-1].title == "Amélioration à valider"


def test_a_medium_risk_verdict_waits_for_you(setup):
    pilot, claude, _ = setup
    misunderstood(pilot)
    claude.plan("corrigé", risk="moyen", replace=FIX, create=NEW_TEST)
    assert pilot.cycle()["état"] == TO_VALIDATE


def test_orion_own_proposals_are_judged_too(setup):
    pilot, claude, _ = setup
    pilot.engine.propose({"title": "Compteur à zéro", "description": "Boîte sans compteur."}, "llm")
    pilot.engine.propose({"title": "Votre idée", "description": "À vous."}, "utilisateur")  # jamais sans vous
    claude.plan("corrigé", replace=FIX, create=NEW_TEST)
    assert pilot.cycle()["état"] == AWAITING
    assert "Compteur à zéro" in claude.calls[0]["input"]
    assert no_request(pilot)
    assert pilot.cycle()["état"] == "rien à examiner"


# --- Retour arrière -------------------------------------------------------------------------------------------------

def test_rollback_is_requested_by_you_only_after_a_deployment(setup):
    pilot, claude, alerts = setup
    misunderstood(pilot)
    claude.plan("corrigé", replace=FIX, create=NEW_TEST)
    pilot.cycle()
    pilot.approve(pilot.actions()[0]["id"], "utilisateur")
    req = request_file(pilot)
    with pytest.raises(ValueError):
        pilot.rollback(req["id"], "utilisateur")  # pas encore déployé
    (Path(pilot.settings.request_dir) / "request.json").unlink()
    (Path(pilot.settings.request_dir) / "result.json").write_text(json.dumps({"id": req["id"], "state": "ok"}))
    pilot.collect()
    with pytest.raises(PolicyError):
        pilot.rollback(req["id"], "evolve")
    assert pilot.rollback(req["id"], "utilisateur")["state"] == ROLLING_BACK
    back = request_file(pilot)
    assert back == {**back, "kind": "rollback", "commit": req["commit"], "to": req["base"], "approved_by": "utilisateur"}
    status, body = pilot.engine.admin("GET", ["auto"], {}, None)
    assert status == 200 and body["en_déploiement"] and body["actions"][0]["id"] == req["id"]


# --- Contrôleur du service de déploiement (deploy/v3_evolve_gate.py) -------------------------------------------------

def commit_change(repo, files, message="change"):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def test_gate_accepts_a_small_tested_change(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD").strip()
    commit = commit_change(repo, {"orion/demo.py": 'def unread_count(box):\n    return box.get("unread", 0)\n',
                                  **NEW_TEST})
    assert gate.problems(str(repo), base, commit, tmp_path / "log") == []


@pytest.mark.parametrize("files,why", [
    ({"orion/platform/x.py": "X = 1\n", **NEW_TEST}, "interdit"),
    ({"deploy/v3_evolve_gate.py": "", **NEW_TEST}, "interdit"),
    ({"orion/demo.py": "import subprocess\n", **NEW_TEST}, "ligne refusée"),
    ({"tests/test_demo.py": "def test_ok():\n    pass\n"}, "affaibli"),
    ({"orion/demo.py": "X = 2\n"}, "aucun test"),
])
def test_gate_refuses_what_must_come_to_you(repo, tmp_path, files, why):
    base = git(repo, "rev-parse", "HEAD").strip()
    commit = commit_change(repo, files)
    assert any(why in p for p in gate.problems(str(repo), base, commit, tmp_path / "log"))


def test_gate_refuses_a_change_not_built_on_the_deployed_code_and_too_many_per_day(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD").strip()
    commit = commit_change(repo, NEW_TEST)
    later = commit_change(repo, {"tests/test_more.py": "def test_x():\n    assert True\n"})
    assert gate.problems(str(repo), later, commit, tmp_path / "log") == ["le changement ne part pas du code déployé"]
    import time
    log = tmp_path / "log"
    log.write_text("".join(f"{int(time.time())} déployé abc{i} x : ok\n" for i in range(3)), encoding="utf-8")
    assert any("24 heures" in p for p in gate.problems(str(repo), base, commit, log))


def test_a_permission_granted_later_is_seen_at_the_next_cycle(setup):
    """Journal du 4 novembre 2026 : refusé une fois (autonomie suspendue), le pilote ne redemandait plus jamais."""
    pilot, claude, _ = setup

    class Verdict:
        def __init__(self, allowed, reason=""):
            self.allowed, self.reason = allowed, reason

    class Gate:
        allowed = False

        def check(self, *a, **k):
            return Verdict(self.allowed, "autonomie suspendue")

    gate = Gate()
    pilot.engine._gate = gate
    misunderstood(pilot)
    assert pilot.cycle()["état"] == "permission refusée : autonomie suspendue"
    assert pilot.status()["raison"] == "permission refusée : autonomie suspendue"
    gate.allowed = True
    claude.plan("rien à faire", risk="nul")
    assert pilot.cycle()["état"] == NOTHING
    assert pilot.status()["prêt"]


# --- Exécutant du Core (deploy/v3_evolve_runner.sh) : jamais sans vous -----------------------------------------------

@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_the_core_runner_does_nothing_without_you_at_a_terminal(tmp_path):
    runner = ROOT / "deploy" / "v3_evolve_runner.sh"
    out = subprocess.run(["bash", str(runner)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         env={**os.environ, "HOME": str(tmp_path)}, timeout=60)
    assert out.returncode == 0 and "à lancer par vous dans un terminal" in out.stderr
    text = runner.read_text(encoding="utf-8")
    assert text.index("[ ! -t 0 ]") < text.index('mv -f "$REQ" "$PROC"')  # rien n'est pris avant ce contrôle
    assert '[ "$approved" = "utilisateur" ]' in text and 'read -r -p' in text


def test_the_automatic_trigger_is_retired():
    root = (ROOT / "deploy" / "v3_evolve_root.sh").read_text(encoding="utf-8")
    assert "systemctl disable --now orion-v3-evolve.path" in root and "PathExists" not in root


@pytest.mark.parametrize("path", ["orion/factory.py", "orion/wallpaper/server.py", "orion/fragfactory/__init__.py",
                                  "orion/platform/permissions.py", "orion/platform/autonomy.py",
                                  "orion/winagent/server.py", "orion/evolve/autopilot.py", "deploy/v3_evolve_runner.sh"])
def test_evolve_can_never_change_its_guards_alone(path):
    """Permissions (F1), arrêt d'urgence, validation, déploiement, retour arrière, accès aux machines, assemblage :
    refusés par le contrôle indépendant du Core, et par ORION (règles de son clone ou du déploiement)."""
    import fnmatch
    from orion.evolve import policy
    from orion.evolve.autopilot import auto_problems
    assert any(fnmatch.fnmatch(path, p) for p in gate.FORBIDDEN)
    try:
        policy.check_path(path)
    except policy.PolicyError:
        return  # protégé d'EVOLVE : il ne peut même pas l'écrire
    assert any("fichier sensible" in p for p in auto_problems([path, "tests/test_x.py"], [], 2, []))
