"""ORION EVOLVE : permissions imposées par le code, observation du journal (logiciel contre panne extérieure),
diagnostic, propositions en file de validation, correctifs dans un dépôt isolé (vrai git, dépôt temporaire) :
commit seulement si tout passe, retour arrière complet sinon, installation « de production » jamais touchée."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.evolve import EvolveEngine, EvolveSettings, Level, PolicyError, Workspace  # noqa: E402
from orion.evolve import policy  # noqa: E402
from orion.evolve.engine import FAILED_CHECKS, READY, REFUSED  # noqa: E402
from orion.evolve.observe import EXTERNAL, LATENCY, SOFTWARE, parse  # noqa: E402
from orion.evolve.workspace import clean_env  # noqa: E402
from orion.platform import Store  # noqa: E402
from orion.platform.intelligence import StructuredLLM  # noqa: E402

BUGGY = '''def unread_count(box):
    return box["unread"]
'''
FIXED = '''def unread_count(box):
    return box.get("unread", 0)
'''
TRACE = """09:10:00 ERROR   Mails : erreur de surveillance
Traceback (most recent call last):
  File "/opt/orion/orion/mail/watch.py", line 204, in _run
    self.check()
    ~~~~~~~~~~^^
  File "/opt/orion/orion/demo.py", line 2, in unread_count
    return box["unread"]
KeyError: 'unread'
""".splitlines()
NETWORK = """09:12:00 ERROR   Suivi des mails : erreur
Traceback (most recent call last):
  File "/opt/orion/orion/mail/provider.py", line 180, in _fetch
    status, data = imap.uid("FETCH")
  File "/usr/lib/python3.14/ssl.py", line 1140, in read
    return self._sslobj.read(len)
TimeoutError: The read operation timed out
""".splitlines()


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """Dépôt « source » (comme /opt/orion) : orion/demo.py défectueux et un test qui passe."""
    src = tmp_path / "install"
    (src / "orion").mkdir(parents=True)
    (src / "tests").mkdir()
    (src / "orion" / "__init__.py").write_text("")
    (src / "orion" / "demo.py").write_text(BUGGY)
    (src / "tests" / "test_demo.py").write_text("from orion.demo import unread_count\n\n\ndef test_ok():\n"
                                                "    assert unread_count({'unread': 2}) == 2\n")
    git(src, "init", "-q", "-b", "main")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "départ")
    return src


def engine(tmp_path, repo, level=2, llm=None, logs=None):
    workspace = Workspace(repo / "data" / "evolve", str(repo), [repo],
                          test_command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"])
    return EvolveEngine(Store(), workspace, settings=EvolveSettings(level=level), llm=llm,
                        logs=logs or (lambda: TRACE))


# --- Politique --------------------------------------------------------------------------------------------

def test_level_three_is_refused_whatever_the_setting():
    assert policy.clamp(3) == (Level.DEVELOPER, "niveau 3 (mainteneur) refusé dans cette version : développeur")
    assert policy.clamp("nimporte")[0] == Level.OBSERVER
    for action in (policy.MERGE, policy.DEPLOY):
        with pytest.raises(PolicyError, match="niveau 3"):
            policy.require(Level.DEVELOPER, action)
    with pytest.raises(PolicyError, match="développeur"):
        policy.require(Level.OBSERVER, policy.COMMIT)


@pytest.mark.parametrize("path", ["orion/evolve/policy.py", "orion/purchase/engine.py", ".env", "config.local.toml",
                                  "data/memory.json", "deploy/appels.sh", "requirements.txt", "../orion/x.py",
                                  "/etc/passwd", "orion/x.sh", "C:/Windows/x.py", "models/voice.onnx",
                                  "tests/test_evolve.py", "jarvis/__init__.py", ".git/config"])
def test_protected_or_outside_paths_are_refused(path):
    with pytest.raises(PolicyError):
        policy.check_path(path)


def test_editable_paths_and_dangerous_lines():
    assert policy.check_path("orion\\mail\\watch.py") == "orion/mail/watch.py"
    found = policy.dangerous_lines(["subprocess.run(cmd, shell=True)", "eval(x)", "TOKEN = 'abcdefghijkl'",
                                    "x = values.get('a')", "path = '/opt/orion/data'", "os.system('rm')"])
    assert len(found) == 5 and not any("values.get" in f for f in found)


def test_workspace_cannot_be_the_installation_or_its_code(tmp_path):
    install = tmp_path / "opt" / "orion"
    for bad in (install, install / "orion", tmp_path / "opt"):
        with pytest.raises(PolicyError):
            policy.check_workspace(bad, [install])
    assert policy.check_workspace(install / "data" / "evolve", [install]) == (install / "data" / "evolve").resolve()


def test_secrets_never_reach_the_commands(monkeypatch, tmp_path):
    monkeypatch.setenv("ORION_AGENT_TOKEN", "secret")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")
    env = clean_env(tmp_path)
    assert "ORION_AGENT_TOKEN" not in env and "IMAP_PASSWORD" not in env and env["HOME"] == str(tmp_path)


def test_only_allowed_programs_run(tmp_path, repo):
    ws = Workspace(repo / "data" / "evolve", str(repo), [repo])
    with pytest.raises(PolicyError, match="commande refusée"):
        ws._cmd(["rm", "-rf", "/"])  # noqa: SLF001


# --- Observation ------------------------------------------------------------------------------------------

def test_software_errors_and_external_failures_are_told_apart():
    found = {a.kind: a for a in parse(TRACE + NETWORK + TRACE, external_threshold=1)}
    software = found[SOFTWARE]
    assert software.count == 2 and software.exception == "KeyError" and software.title.endswith("orion/demo.py:"
                                                                                                  "unread_count")
    assert found[EXTERNAL].exception == "TimeoutError"
    quiet = parse(NETWORK * 3 + ["09:00:00 WARNING Mail : serveur IMAP injoignable (TimeoutError)"] * 10)
    assert quiet == []  # sous le seuil : bruit normal du réseau, pas une anomalie


def test_latency_anomaly_from_measures():
    lines = [f"09:00:0{i} INFO    [r:a.1] [mesure] chemin=llm total=7.{i}" for i in range(6)]
    assert [a.kind for a in parse(lines)] == [LATENCY]
    assert parse(lines[:4]) == []


# --- Niveau 1 : observer, diagnostiquer, proposer -----------------------------------------------------------

def test_scan_diagnoses_new_software_anomalies_and_proposes_recurring_fixes(tmp_path, repo):
    e = engine(tmp_path, repo, level=1)
    report = e.scan()
    assert report == {"anomalies": 1, "nouvelles": 1}
    signature = e.anomalies()[0]["signature"]
    diagnosis = e.diagnosis(signature)
    assert "KeyError" in diagnosis["summary"] and diagnosis["files"] == ["orion/demo.py", "orion/mail/watch.py"]
    assert "clé absente" in diagnosis["hypotheses"][0] and "orion/demo.py:2" in diagnosis["code"]
    assert e.proposals() == []
    e.scan(TRACE * 2)
    proposals = e.proposals()
    assert len(proposals) == 1 and proposals[0]["status"] == "en attente" and proposals[0]["origin"] == "erreurs"
    e.scan(TRACE)
    assert len(e.proposals()) == 1  # jamais deux fois la même proposition


def test_level_one_never_writes_code(tmp_path, repo):
    e = engine(tmp_path, repo, level=1)
    e.scan()
    signature = e.anomalies()[0]["signature"]
    status, body = e.admin("POST", ["patches"], {}, {"origin": signature, "edits": [], "message": "x"})
    assert status == 403 and "développeur" in body["message"]


def test_only_the_user_changes_the_level_and_never_to_three(tmp_path, repo):
    e = engine(tmp_path, repo, level=1)
    with pytest.raises(PolicyError):
        e.set_level(2, "evolve")
    assert e.admin("PUT", ["level"], {}, {"level": 3})[0] == 403
    assert e.admin("PUT", ["level"], {}, {"level": 2})[1]["niveau"] == 2
    assert e.admin("GET", ["history"], {}, None)[1][0]["detail"] == {"de": 1, "vers": 2, "par": "utilisateur"}
    for action in (e.merge, e.deploy):
        with pytest.raises(PolicyError):
            action()


def test_configured_level_three_runs_as_developer(tmp_path, repo):
    e = engine(tmp_path, repo, level=3)
    assert e.level == Level.DEVELOPER and "refusé" in e.status()["remarque"]


def test_proposals_wait_for_the_user_and_are_never_developed_before(tmp_path, repo):
    e = engine(tmp_path, repo)
    p = e.propose({"title": "Résumé hebdomadaire", "description": "Un résumé des mails le lundi."})
    with pytest.raises(PolicyError, match="non acceptée"):
        e.patch(p["id"], [{"path": "tests/test_x.py", "content": "def test_x():\n    pass\n"}], "x")
    with pytest.raises(PolicyError):
        e.decide(p["id"], True, "llm")
    assert e.admin("POST", ["proposals", p["id"], "reject"], {}, None)[1]["status"] == "refusée"
    assert e.admin("POST", ["proposals", p["id"], "accept"], {}, None)[0] == 400  # déjà décidée


# --- Niveau 2 : correctifs dans le dépôt isolé --------------------------------------------------------------

REGRESSION = {"path": "tests/test_unread_missing.py",
              "content": "from orion.demo import unread_count\n\n\ndef test_missing():\n"
                         "    assert unread_count({}) == 0\n"}


def head(path):
    return git(path, "rev-parse", "HEAD").strip()


def test_good_fix_is_committed_on_an_evolve_branch_and_production_is_untouched(tmp_path, repo):
    before = head(repo)
    e = engine(tmp_path, repo)
    e.scan()
    signature = e.anomalies()[0]["signature"]
    result = e.patch(signature, [{"path": "orion/demo.py", "search": 'box["unread"]', "replace":
                                  'box.get("unread", 0)'}, REGRESSION], "Mail : boîte sans compteur de non lus")
    assert result["state"] == READY and result["branch"].startswith("evolve/") and result["commit_id"]
    assert all(step["ok"] for step in result["report"]["steps"])
    assert Path(result["pr"]).read_text(encoding="utf-8").startswith("# Mail : boîte sans compteur")
    assert head(repo) == before and (repo / "orion" / "demo.py").read_text() == BUGGY  # « production » intacte
    clone = repo / "data" / "evolve" / "repo"
    assert git(clone, "show", f"{result['branch']}:orion/demo.py") == FIXED


def test_failing_tests_forbid_the_commit_and_everything_is_rolled_back(tmp_path, repo):
    e = engine(tmp_path, repo)
    e.scan()
    signature = e.anomalies()[0]["signature"]
    bad_test = {**REGRESSION, "content": "def test_missing():\n    assert False\n"}
    result = e.patch(signature, [{"path": "orion/demo.py", "search": 'box["unread"]', "replace": 'box.get("unread")'},
                                 bad_test], "essai")
    assert result["state"] == FAILED_CHECKS and not result["commit_id"]
    clone = repo / "data" / "evolve" / "repo"
    assert git(clone, "branch", "--list", "evolve/*").strip() == ""
    assert git(clone, "status", "--porcelain").strip() == "" and (clone / "orion" / "demo.py").read_text() == BUGGY


def test_dangerous_code_or_protected_files_are_refused(tmp_path, repo):
    e = engine(tmp_path, repo)
    e.scan()
    signature = e.anomalies()[0]["signature"]
    risky = e.patch(signature, [{"path": "orion/demo.py", "search": 'return box["unread"]',
                                 "replace": 'import os\n    os.system("curl x")\n    return box.get("unread", 0)'},
                                REGRESSION], "x")
    assert risky["state"] == FAILED_CHECKS and any("os.system" in p for p in risky["report"]["problems"])
    protected = e.patch(signature, [{"path": "orion/evolve/policy.py", "search": "MAX_LEVEL", "replace": "X"},
                                    REGRESSION], "x")
    assert protected["state"] == REFUSED and "protégé" in protected["report"]["problems"][0]
    with pytest.raises(PolicyError, match="non-régression"):
        e.patch(signature, [{"path": "orion/demo.py", "search": 'box["unread"]', "replace": "0"}], "x")


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def chat_json(self, messages, schema):
        return self.answer


def test_llm_written_fix_goes_through_the_same_checks(tmp_path, repo):
    answer = {"message": "Compteur absent", "edits": [{"path": "orion/demo.py", "search": 'box["unread"]',
                                                       "replace": 'box.get("unread", 0)'}, REGRESSION]}
    e = engine(tmp_path, repo, llm=StructuredLLM(FakeLLM(answer)))
    e.scan()
    result = e.patch(e.anomalies()[0]["signature"])
    assert result["state"] == READY
    no_llm = engine(tmp_path / "b", repo, llm=StructuredLLM(FakeLLM(answer), available=lambda: False))
    no_llm.scan()
    with pytest.raises(ValueError, match="LLM indisponible"):
        no_llm.patch(no_llm.anomalies()[0]["signature"])


def test_external_failures_are_never_patched(tmp_path, repo):
    e = engine(tmp_path, repo, logs=lambda: NETWORK * 60)
    e.scan()
    anomaly = e.anomalies()[0]
    assert anomaly["kind"] == EXTERNAL and "pas un défaut du code" in e.diagnose(anomaly["signature"])["summary"]
    with pytest.raises(PolicyError, match="logicielle"):
        e.patch(anomaly["signature"], [REGRESSION], "x")


def test_admin_overview(tmp_path, repo):
    e = engine(tmp_path, repo, level=1)
    assert e.admin("POST", ["scan"], {}, None)[1]["nouvelles"] == 1
    signature = e.anomalies()[0]["signature"]
    assert e.admin("GET", ["anomalies", signature], {}, None)[1]["diagnosis"]["files"][0] == "orion/demo.py"
    assert e.admin("GET", [], {}, None)[1]["niveau_libellé"] == "observateur"
    assert e.admin("GET", ["history"], {}, None)[1][0]["module"] == "evolve"
