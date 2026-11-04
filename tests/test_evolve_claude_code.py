"""EVOLVE consulte Claude Code (orion/evolve/claude_code.py) : ``claude -p`` en lecture seule dans le clone isolé,
réponse structurée vérifiée, contexte technique masqué, jeton passé au seul processus, propositions en file
d'attente (vous seul acceptez), limite par 24 heures, consultation de lui-même après une nouvelle anomalie
logicielle, correctif rédigé par Claude Code puis vérifié et testé par ORION. Le processus est simulé."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.evolve import EvolveEngine, EvolveSettings, PolicyError, Workspace  # noqa: E402
from orion.evolve.claude_code import READ_ONLY_TOOLS, TOKEN_NAME, ClaudeCodeAdvisor, technical  # noqa: E402
from orion.evolve.engine import ACCEPTED, PENDING, READY  # noqa: E402
from orion.platform import Store  # noqa: E402

from tests.test_evolve import FIXED, TRACE, git  # noqa: E402

PROPOSAL = {"title": "Cache des réponses météo", "description": "Garder la météo 10 minutes.", "interest": "rapidité",
            "difficulty": "faible", "dependencies": "orion/tools/weather.py", "risks": "donnée périmée",
            "cost": "une heure", "plan": "cache en mémoire", "tests": "test du cache"}


class FakeClaude:
    """Remplace subprocess.run : garde chaque appel, rend la sortie JSON de ``claude -p --output-format json``."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        out = answer if isinstance(answer, dict) and "subtype" in answer else {
            "type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 4000,
            "result": json.dumps(answer), "structured_output": answer}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "install"
    (src / "orion").mkdir(parents=True)
    (src / "tests").mkdir()
    (src / "orion" / "__init__.py").write_text("")
    (src / "orion" / "demo.py").write_text('def unread_count(box):\n    return box["unread"]\n')
    (src / "tests" / "test_demo.py").write_text("from orion.demo import unread_count\n\n\ndef test_ok():\n"
                                                "    assert unread_count({'unread': 2}) == 2\n")
    git(src, "init", "-q", "-b", "main")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "départ")
    return src


@pytest.fixture
def binary(tmp_path):
    path = tmp_path / "claude-bin" / "claude"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def advisor(tmp_path, binary, run, token="jeton-secret"):
    return ClaudeCodeAdvisor(binary, tmp_path / "home", lambda: token, run=run)


def engine(tmp_path, repo, claude, level=1, **settings):
    workspace = Workspace(repo / "data" / "evolve", str(repo), [repo],
                          test_command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"])
    return EvolveEngine(Store(), workspace, settings=EvolveSettings(level=level, **settings), logs=lambda: TRACE,
                        claude=claude)


# --- Le processus Claude Code -------------------------------------------------------------------------------

def test_claude_code_runs_read_only_in_the_clone_with_the_token_only_in_its_environment(tmp_path, repo, binary):
    run = FakeClaude([{"proposals": [PROPOSAL]}])
    e = engine(tmp_path, repo, advisor(tmp_path, binary, run))
    e.consult("Comment rendre la météo plus rapide ?")
    call = run.calls[0]
    cmd = call["cmd"]
    assert cmd[:2] == [str(binary), "-p"]
    assert cmd[cmd.index("--tools") + 1] == READ_ONLY_TOOLS == "Read,Grep,Glob"
    assert {"--restricted", "--strict-mcp-config", "--no-session-persistence"} <= set(cmd)
    assert "Bash" not in " ".join(cmd) and "bypassPermissions" not in cmd and "--dangerously-skip-permissions" not in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["required"] == ["proposals"]
    assert Path(call["cwd"]) == repo / "data" / "evolve" / "repo"  # le clone, jamais l'installation
    assert "météo plus rapide" in call["input"]  # la demande passe par l'entrée standard, pas par la ligne de commande
    assert "jeton-secret" not in " ".join(cmd) and "jeton-secret" not in call["input"]
    assert call["env"][TOKEN_NAME] == "jeton-secret"
    assert not {k for k in call["env"] if k not in ("PATH", "HOME", "LANG", "TZ", TOKEN_NAME)
                and not k.startswith(("DISABLE_", "CLAUDE_CODE_DISABLE"))}  # rien d'autre de l'environnement d'ORION


def test_personal_details_are_masked_before_leaving():
    text = technical("Erreur pour jean.dupont@mail.fr au 06 12 34 56 78, phrase « rappelle Marie demain », "
                     "https://exemple.fr/a?jeton=abc, compte 123456789012")
    assert "jean.dupont" not in text and "06 12" not in text and "Marie" not in text
    assert "jeton=abc" not in text and "123456789012" not in text
    assert "https://exemple.fr/a" in text


def test_outside_a_clone_claude_code_is_never_launched(tmp_path, binary):
    run = FakeClaude([])
    claude = advisor(tmp_path, binary, run)
    assert claude.ask("t", {"type": "object"}, cwd=tmp_path) is None
    assert run.calls == [] and claude.last_error == "clone isolé du code indisponible"


def test_without_token_or_binary_claude_code_is_not_connected(tmp_path, repo, binary):
    assert advisor(tmp_path, binary, FakeClaude([]), token="").state().startswith("jeton Claude Code absent")
    assert ClaudeCodeAdvisor(tmp_path / "absent", tmp_path, lambda: "x").state() == "Claude Code non installé sur le Core"
    e = engine(tmp_path, repo, advisor(tmp_path, binary, FakeClaude([]), token=""))
    with pytest.raises(PolicyError, match="pas relié"):
        e.consult("idée ?")
    assert e.status()["claude_code"].startswith("jeton")


@pytest.mark.parametrize("answer", [
    {"type": "result", "subtype": "error_max_turns", "is_error": True},
    {"proposals": [{**PROPOSAL, "difficulty": "énorme"}]},  # hors schéma : écarté par ORION aussi
    subprocess.TimeoutExpired("claude", 900),
])
def test_a_failed_or_invalid_answer_adds_nothing(tmp_path, repo, binary, answer):
    e = engine(tmp_path, repo, advisor(tmp_path, binary, FakeClaude([answer])))
    with pytest.raises(ValueError, match="n'a pas répondu"):
        e.consult("idée ?")
    assert e.proposals() == []
    assert any(h["kind"] == "consultation claude" for h in e._history.list("evolve"))


# --- EVOLVE et ses propositions -----------------------------------------------------------------------------

def test_consultation_queues_proposals_that_only_you_accept(tmp_path, repo, binary):
    e = engine(tmp_path, repo, advisor(tmp_path, binary, FakeClaude([{"proposals": [PROPOSAL, {**PROPOSAL,
                                                                                               "title": "Autre"}]}])))
    made = e.consult("Comment rendre la météo plus rapide ?")
    assert [p["title"] for p in made] == ["Cache des réponses météo", "Autre"]
    assert {p["origin"] for p in e.proposals()} == {"claude"} and {p["status"] for p in e.proposals()} == {PENDING}
    with pytest.raises(PolicyError):
        e.decide(made[0]["id"], True, "evolve")
    assert e.decide(made[0]["id"], True, "utilisateur")["status"] == ACCEPTED


def test_consultations_are_limited_per_day(tmp_path, repo, binary):
    e = engine(tmp_path, repo, advisor(tmp_path, binary, FakeClaude([{"proposals": []}] * 3)), claude_per_day=2)
    e.consult()
    e.consult()
    with pytest.raises(ValueError, match="limite atteinte"):
        e.consult()
    assert e.status()["consultations_24h"] == 2


def test_orion_control_asks_claude_code(tmp_path, repo, binary):
    e = engine(tmp_path, repo, advisor(tmp_path, binary, FakeClaude([{"proposals": [PROPOSAL]}])))
    status, body = e.admin("POST", ["claude"], {}, {"question": "Une idée pour la météo ?"})
    assert status == 201 and body[0]["origin"] == "claude"
    assert e.admin("POST", ["claude"], {}, {})[0] == 400  # plus de réponse simulée : refus propre, pas d'erreur 500


def test_evolve_consults_by_itself_after_a_new_software_anomaly(tmp_path, repo, binary):
    run = FakeClaude([{"proposals": [PROPOSAL]}])
    e = engine(tmp_path, repo, advisor(tmp_path, binary, run))
    assert e.scan()["propositions_claude"] == 1
    assert "KeyError" in run.calls[0]["input"] and "orion/demo.py" in run.calls[0]["input"]
    assert e.scan()["propositions_claude"] == 0  # la même anomalie : pas une seconde fois
    assert len(run.calls) == 1


def test_automatic_consultation_can_be_turned_off(tmp_path, repo, binary):
    run = FakeClaude([])
    assert engine(tmp_path, repo, advisor(tmp_path, binary, run), claude_auto=False).scan()["propositions_claude"] == 0
    assert run.calls == []


def test_claude_code_drafts_the_patch_that_orion_checks_and_tests(tmp_path, repo, binary):
    edits = {"message": "Boîte sans compteur", "edits": [
        {"path": "orion/demo.py", "search": 'return box["unread"]', "replace": 'return box.get("unread", 0)'},
        {"path": "tests/test_unread.py", "content": "from orion.demo import unread_count\n\n\ndef test_missing():\n"
                                                    "    assert unread_count({}) == 0\n"}]}
    run = FakeClaude([{"proposals": [PROPOSAL]}, edits])
    e = engine(tmp_path, repo, advisor(tmp_path, binary, run), level=2)
    proposal = e.consult("idée ?")[0]
    e.decide(proposal["id"], True, "utilisateur")
    result = e.patch(proposal["id"], confirmed=True)
    assert result["state"] == READY, result["report"]
    assert (repo / "data" / "evolve" / "repo" / "orion" / "demo.py").read_text().strip().endswith(
        FIXED.strip().splitlines()[-1].strip())
    assert (repo / "orion" / "demo.py").read_text().count('box["unread"]') == 1  # installation intacte
    assert os.path.basename(run.calls[1]["cwd"]) == "repo"
