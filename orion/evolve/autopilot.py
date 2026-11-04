"""EVOLVE autonome (docs/v3/evolve.md, « Mode autonome ») : ORION s'améliore seule, Claude Code juge.

1. ORION repère une demande qu'elle n'a pas comprise (sa réponse le dit : « je n'ai pas compris », « je ne sais pas
   faire »…), ou une proposition d'amélioration qu'elle a faite elle-même (EVOLVE).
2. Claude Code (orion/evolve/claude_code.py, mode travail : écriture confinée au clone isolé) examine, juge et, s'il le
   faut, modifie le code du clone avec un test. Son verdict : « corrigé », « rien à faire », « à valider »,
   « dangereux », avec un niveau de risque.
3. Dangereux : rien n'est fait, vous êtes prévenu. À valider (ou risque moyen ou élevé) : proposition en file
   d'attente, vous êtes prévenu. Corrigé et sans risque : règles d'ORION (fichiers sensibles jamais, taille limitée,
   aucun test supprimé ni affaibli, test ajouté), vérifications (compilation, tests, sécurité), commit, puis demande de
   déploiement.
4. Le déploiement n'est jamais fait par ORION : le service orion-v3-evolve (compte user, deploy/v3_evolve_runner.sh)
   revérifie tout de son côté (deploy/v3_evolve_gate.py), refait passer la suite complète, puis déploie
   (deploy/v3_deploy.sh : sauvegarde, avance rapide, santé, retour automatique à la version précédente sinon).
   Vous êtes prévenu de chaque déploiement ; « Revenir en arrière » dans ORION Control le défait.
Interrupteur : ORION Control (vue « Évolution ») ou [evolve] autonomous ; niveau 2 exigé.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orion.evolve import policy
from orion.evolve.claude_code import EDIT_TOOLS
from orion.evolve.policy import PolicyError
from orion.platform.alerts import Alert, Level as AlertLevel
from orion.platform.store import dumps, loads

log = logging.getLogger(__name__)

# Réponses d'ORION qui avouent une incompréhension ou une incapacité (normalisées : minuscules, apostrophes droites).
NOT_UNDERSTOOD = re.compile(
    r"pas (?:bien )?compris|pas saisi|pas sure d'avoir compris|ne sais pas (?:faire|encore|comment)|"
    r"ne peux pas (?:faire|encore)|n'ai pas (?:d'outil|de quoi|la possibilite|cette capacite)|"
    r"je ne suis pas capable|cela ne fait pas partie de ce que je sais|il me manque une precision")
MIN_WORDS = 3  # « hein », « quoi » : bruit de la reconnaissance vocale, pas une demande

# Jamais déployés sans vous, quoi qu'en dise le verdict (en plus des fichiers protégés d'EVOLVE, policy.PROTECTED).
# Copie indépendante côté déploiement : deploy/v3_evolve_gate.py.
AUTO_PROTECTED = (
    "orion/platform/*", "orion/platform/**", "orion/foundation/*", "orion/foundation/**", "orion/control/*",
    "orion/control/**", "orion/call/*", "orion/call/**", "orion/mail/*", "orion/mail/**", "orion/winagent/*",
    "orion/winagent/**", "orion/knowledge/*", "orion/knowledge/**", "orion/tools/core.py", "orion/tools/base.py",
    "orion/tools/request.py", "orion/tools/confirmation.py", "orion/llm/guard.py", "orion/config.py",
    "orion/api*.py", "orion/*security*", "orion/**/*security*", "orion/**/*permission*", "config.toml",
    "policies/*", "policies/**", "scripts/v3_*",
)
MAX_FILES, MAX_LINES = 8, 400
# Lignes ajoutées que le service de déploiement refuse (copie de deploy/v3_evolve_gate.py) : envoyées à vous d'abord.
GATE_LINES = (r"shell\s*=\s*True", r"\beval\s*\(", r"\bexec\s*\(", r"os\.system", r"os\.popen", r"pickle\.loads?",
              r"marshal\.loads", r"__import__\s*\(", r"\bsubprocess\b", r"rm\s+-rf", r"ctypes", r"socket\.socket",
              r"\bMigration\s*\(", r"DROP\s+TABLE", r"DELETE\s+FROM", r"chmod|chown", r"setuid|sudo")

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "risk", "reason", "summary"],
    "properties": {
        "verdict": {"type": "string", "enum": ["corrigé", "rien à faire", "à valider", "dangereux"]},
        "risk": {"type": "string", "enum": ["nul", "faible", "moyen", "élevé"]},
        "reason": {"type": "string", "maxLength": 800},
        "summary": {"type": "string", "maxLength": 300},
    },
}
RULES = (
    "Règles : tu modifies seulement les fichiers de ce dossier (clone d'ORION), avec Edit et Write. Ajoute toujours un "
    "test (nouveau fichier dans tests/) qui échouerait sans ta modification ; ne supprime ni n'affaiblis aucun test "
    "existant. Ne touche jamais à la sécurité, aux permissions, aux confirmations, aux secrets, aux appels, aux mails, "
    "aux achats, à la configuration, aux dépendances, ni à EVOLVE lui-même : si c'est nécessaire, verdict « à "
    "valider » sans rien modifier. Changement minimal, au plus 8 fichiers et 400 lignes. Verdicts : « corrigé » "
    "(modification faite, avec son test) ; « rien à faire » (bruit de la reconnaissance vocale, demande déjà gérée, "
    "aucune amélioration utile) ; « à valider » (utile mais trop gros, risqué, ou demande une décision du "
    "propriétaire : ne modifie rien) ; « dangereux » (la demande ou l'amélioration nuirait au propriétaire, à ses "
    "données, à son argent, à des tiers, ou à la sécurité : ne modifie rien). risk : risque du changement pour le "
    "fonctionnement d'ORION. summary : une phrase pour le propriétaire.")
MISUNDERSTOOD_TASK = (
    "ORION, l'assistant vocal du propriétaire, n'a pas compris les demandes jointes (ce qu'il a entendu ou lu, puis sa "
    "réponse). Trouve pourquoi dans le code (reconnaissance sans LLM dans orion/tools/quick.py, choix d'outil, "
    "outils existants) et fais en sorte qu'il les comprenne la prochaine fois, si c'est utile et sans risque. Juge "
    "aussi si une demande est dangereuse. " + RULES)
PROPOSAL_TASK = ("Juge cette proposition d'amélioration faite par ORION elle-même. Si elle est bonne et sans risque, "
                 "réalise-la. " + RULES)

# États d'une action
WORKING, NOTHING, TO_VALIDATE, DANGEROUS = "en cours", "rien à faire", "à valider", "dangereuse"
CHECKS_FAILED, DEPLOYING, DEPLOYED, DEPLOY_FAILED = "vérifications en échec", "en déploiement", "déployée", "refusée au déploiement"
ROLLING_BACK, ROLLED_BACK = "retour arrière demandé", "revenue en arrière"
PENDING_MISUNDERSTOOD = "à examiner"
DAY = 86_400.0


@dataclass
class AutoSettings:
    enabled: bool = False
    deploys_per_day: int = 3
    cycle_minutes: float = 15.0
    request_dir: Path = Path("data/evolve/deploy")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).lower().replace("’", "'")
                  .translate(str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc"))).strip()


def auto_problems(changed: list[str], deleted: list[str], lines: int, weakened: list[str]) -> list[str]:
    """Règles du déploiement automatique (au-delà de policy.check_path, déjà appliquée par les vérifications)."""
    problems = []
    for path in changed:
        if any(fnmatch.fnmatch(path.lower(), p) for p in AUTO_PROTECTED):
            problems.append(f"fichier sensible : {path}")
    if len(changed) > MAX_FILES:
        problems.append(f"{len(changed)} fichiers (au plus {MAX_FILES})")
    if lines > MAX_LINES:
        problems.append(f"{lines} lignes changées (au plus {MAX_LINES})")
    if any(p.startswith("tests/") for p in deleted):
        problems.append("test supprimé")
    problems += [f"test existant affaibli : {p}" for p in weakened]
    if not any(p.startswith("tests/") and p not in deleted for p in changed):
        problems.append("aucun test ajouté")
    return problems


class AutoPilot:
    def __init__(self, engine, settings: AutoSettings | None = None, *, clock: Callable[[], float] = time.time):
        self.engine, self.settings, self._clock = engine, settings or AutoSettings(), clock
        self._store = engine._store  # table evolve_auto : migration 2 d'EVOLVE (engine.py)
        self._last_request = ""
        self.blocked = ""  # raison si le moteur des permissions refuse (autonomie suspendue)

    # --- Interrupteur (vous seul) ------------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        row = self._store.one("SELECT value FROM evolve_settings WHERE key = 'autonomous'")
        return bool(loads(row["value"])) if row else self.settings.enabled

    def set_enabled(self, value: Any, by: str) -> bool:
        if by != "utilisateur":
            raise PolicyError("seul l'utilisateur active ou coupe le mode autonome")
        if not isinstance(value, bool):
            raise ValueError("vrai ou faux attendu")
        self._store.write("INSERT INTO evolve_settings (key, value) VALUES ('autonomous', ?) ON CONFLICT(key) DO "
                          "UPDATE SET value = excluded.value", (dumps(value),))
        self.engine._history.record("evolve", "", "mode autonome", {"actif": value, "par": by})
        return value

    def ready(self) -> str:
        """Vide si le mode autonome peut travailler, sinon la raison."""
        if not self.enabled:
            return "mode autonome coupé"
        if int(self.engine.level) < 2:
            return "niveau 2 (développeur) exigé"
        claude = self.engine._claude
        if claude is None or not claude.usable:
            return "Claude Code n'est pas relié"
        if self.engine.workspace is None:
            return "aucun espace de travail"
        return ""  # le refus du moteur des permissions (self.blocked) est redemandé à chaque cycle

    # --- Demandes incomprises (événements de la conversation) ---------------------------------------------------

    def observe(self, kind: str, text: str) -> None:
        """Branché sur les événements de l'agent : « user »/« message » (demande), « assistant »/« message_reply »
        (réponse). Une réponse qui avoue ne pas avoir compris met la demande de côté pour Claude Code."""
        if kind in ("user", "message"):
            self._last_request = str(text or "")[:500]
            return
        if kind not in ("assistant", "message_reply") or not self._last_request:
            return
        request, self._last_request = self._last_request, ""
        if not self.enabled or len(request.split()) < MIN_WORDS or not NOT_UNDERSTOOD.search(normalize(text)):
            return
        key = normalize(request)
        for row in self._store.query("SELECT data FROM evolve_auto WHERE kind = 'incomprise' AND at > ?",
                                     (self._clock() - 7 * DAY,)):
            if normalize(loads(row["data"], {}).get("request", "")) == key:
                return  # déjà vue cette semaine
        self._save(uuid.uuid4().hex[:10], "incomprise", "conversation", PENDING_MISUNDERSTOOD,
                   {"request": request, "reply": str(text)[:300]})
        log.info("EVOLVE autonome : demande incomprise mise de côté pour Claude Code")

    # --- Cycle (planificateur : evolve:auto) -------------------------------------------------------------------

    def start(self) -> None:
        self.engine._scheduler.add("evolve:auto", self.settings.cycle_minutes * 60, self.cycle,
                                   first_at=self._clock() + 600)

    def stop(self) -> None:
        self.engine._scheduler.remove("evolve:auto")

    def cycle(self) -> dict:
        finished = self.collect()
        reason = self.ready()
        if reason:
            return {"état": reason, "terminées": finished}
        if self._in_flight():
            return {"état": "déploiement en cours", "terminées": finished}
        if self.engine._gate is not None:  # moteur central des permissions : l'autonomie suspendue l'arrête
            verdict = self.engine._gate.check("CODE_CHANGE", {"sandbox": True}, action="evolve:auto", confirmed=False)
            if not verdict.allowed:
                self.blocked = f"permission refusée : {verdict.reason}"
                return {"état": self.blocked, "terminées": finished}
        self.blocked = ""
        pending = [r for r in self._rows("incomprise") if r["state"] == PENDING_MISUNDERSTOOD][:5]
        if pending:
            return {"état": self.work_misunderstood(pending), "terminées": finished}
        proposal = self._next_proposal()
        if proposal is not None:
            return {"état": self.work_proposal(proposal), "terminées": finished}
        return {"état": "rien à examiner", "terminées": finished}

    def _next_proposal(self) -> dict | None:
        judged = {loads(r["data"], {}).get("proposal") for r in self._store.query(
            "SELECT data FROM evolve_auto WHERE kind = 'proposition'")}
        for p in reversed(self.engine.proposals()):  # la plus ancienne d'abord
            if p["status"] == "en attente" and p["origin"] in ("llm", "erreurs") and p["id"] not in judged:
                return p
        return None

    def work_misunderstood(self, rows: list[dict]) -> str:
        context = {f"demande {n}": f"entendu ou lu : {r['data']['request']}\nréponse d'ORION : {r['data']['reply']}"
                   for n, r in enumerate(rows, 1)}
        action = uuid.uuid4().hex[:10]
        for r in rows:
            self._update(r["id"], "examinée", {"action": action})
        return self._work(action, "incomprises", MISUNDERSTOOD_TASK, context,
                          {"requests": [r["data"]["request"] for r in rows]})

    def work_proposal(self, proposal: dict) -> str:
        fields = ("title", "description", "interest", "dependencies", "risks", "plan", "tests")
        context = {"proposition": "\n".join(f"{k} : {proposal.get(k, '')}" for k in fields)}
        return self._work(uuid.uuid4().hex[:10], "proposition", PROPOSAL_TASK, context,
                          {"proposal": proposal["id"], "title": proposal["title"]})

    def _work(self, action: str, kind: str, task: str, context: dict, data: dict) -> str:
        engine, workspace, claude = self.engine, self.engine.workspace, self.engine._claude
        calls = engine._claude_calls()
        if len(calls) >= engine._defaults.claude_per_day:
            return "limite de consultations de Claude Code atteinte"
        for step in (policy.BRANCH, policy.EDIT, policy.TEST, policy.COMMIT):
            policy.require(engine.level, step)
        branch = f"evolve/auto-{action}"
        self._save(action, kind, "claude", WORKING, data)
        try:
            workspace.start_branch(branch)
        except Exception as exc:  # noqa: BLE001
            return self._finish(action, CHECKS_FAILED, {"problems": [f"clone : {str(exc)[-200:]}"]})
        engine._store.write("INSERT INTO evolve_settings (key, value) VALUES ('claude_calls', ?) ON CONFLICT(key) "
                            "DO UPDATE SET value = excluded.value", (dumps(calls + [self._clock()]),))
        verdict = claude.ask(task, VERDICT_SCHEMA, context, cwd=workspace.repo, tools=EDIT_TOOLS)
        engine._history.record("evolve", action, "travail claude", {"type": kind, "réussi": verdict is not None})
        if verdict is None:
            workspace.discard(branch)
            return self._finish(action, CHECKS_FAILED, {"problems": [f"Claude Code : {claude.last_error}"]})
        data = {**data, **verdict}
        changed = workspace.changed_files()
        if verdict["verdict"] == "dangereux":
            workspace.discard(branch)
            self._tell("Demande jugée dangereuse", f"{verdict['summary']} — {verdict['reason']}", action)
            return self._finish(action, DANGEROUS, data)
        if verdict["verdict"] == "rien à faire":
            workspace.discard(branch)
            return self._finish(action, NOTHING, data)
        if verdict["verdict"] == "à valider" or verdict["risk"] in ("moyen", "élevé"):
            return self._to_validate(action, branch, data, changed, [])
        deleted = workspace.deleted_files()
        problems = auto_problems(changed, deleted, workspace.changed_lines(), self._weakened(changed, deleted))
        added = [line[1:] for line in workspace.diff().splitlines() if line.startswith("+") and not line.startswith("+++")]
        problems += [f"ligne à faire valider ({pattern})" for pattern in GATE_LINES
                     if any(re.search(pattern, line, re.IGNORECASE) for line in added)]
        if problems:
            return self._to_validate(action, branch, data, changed, problems)
        report = workspace.check()
        data = {**data, "files": changed, "report": {"passed": report.passed, "problems": report.problems,
                                                     "steps": [{k: v for k, v in s.items() if k != "sortie"}
                                                               for s in report.steps]}}
        if not report.passed:
            workspace.discard(branch)
            self._tell("Amélioration non déployée", f"{verdict['summary']} — vérifications en échec : "
                                                    + "; ".join(report.problems)[:300], action)
            return self._finish(action, CHECKS_FAILED, data)
        before = self._deployed_head()
        commit = workspace.commit(f"EVOLVE autonome : {verdict['summary'][:200]}\n\n{verdict['reason'][:1500]}\n\n"
                                  f"Action {action} ({kind}), jugée par Claude Code : risque {verdict['risk']}.",
                                  report)
        if self._deploys_today() >= self.settings.deploys_per_day:
            self._tell("Amélioration prête, déploiement reporté", f"{verdict['summary']} (limite de "
                       f"{self.settings.deploys_per_day} déploiements par jour) : à valider dans ORION Control.", action)
            return self._to_validate(action, branch, {**data, "commit": commit}, changed,
                                     ["limite de déploiements atteinte"], keep_branch=True)
        self._request({"id": action, "kind": "deploy", "commit": commit, "branch": branch, "base": before})
        return self._finish(action, DEPLOYING, {**data, "commit": commit, "before": before})

    def _weakened(self, changed: list[str], deleted: list[str]) -> list[str]:
        """Tests existants dont des lignes disparaissent (un test qu'on affaiblit pour qu'il passe)."""
        weak = []
        workspace = self.engine.workspace
        for path in changed:
            if not path.startswith("tests/") or path in deleted:
                continue
            existed = workspace._git("cat-file", "-e", f"{workspace.base}:{path}", check=False) == ""
            if not existed:
                continue
            removed = [line for line in workspace._git("diff", workspace.base, "--", path).splitlines()
                       if line.startswith("-") and not line.startswith("---") and line[1:].strip()]
            if removed:
                weak.append(path)
        return weak

    def _to_validate(self, action: str, branch: str, data: dict, changed: list[str], problems: list[str],
                     keep_branch: bool = False) -> str:
        if not keep_branch:
            self.engine.workspace.discard(branch)
        title = (data.get("title") or data.get("summary") or "Amélioration")[:150]
        description = (f"{data.get('summary', '')} {data.get('reason', '')}".strip()
                       + (f" Demandes : {' | '.join(data['requests'])}." if data.get("requests") else "")
                       + (f" Fichiers : {', '.join(changed)}." if changed else "")
                       + (f" Non déployée seule : {'; '.join(problems)}." if problems else ""))[:1200]
        proposal = self.engine.propose({"title": title, "description": description,
                                        "interest": "demande incomprise" if data.get("requests") else "amélioration",
                                        "difficulty": "moyenne", "risks": f"risque {data.get('risk', '?')}",
                                        "plan": "réalisation par EVOLVE après votre accord", "tests": "suite complète"},
                                       "claude")
        self._tell("Amélioration à valider", f"{data.get('summary', title)} — ORION Control, vue « Évolution ».",
                   action)
        return self._finish(action, TO_VALIDATE, {**data, "files": changed, "problems": problems,
                                                  "proposal_id": proposal["id"]})

    # --- Déploiement (par le service orion-v3-evolve, jamais par ORION) ----------------------------------------

    def _request(self, request: dict) -> None:
        folder = Path(self.settings.request_dir)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "request.json.tmp"
        tmp.write_text(json.dumps({**request, "at": self._clock()}), encoding="utf-8")
        tmp.replace(folder / "request.json")  # le service surveille ce fichier (orion-v3-evolve.path)

    def _in_flight(self) -> bool:
        folder = Path(self.settings.request_dir)
        return (folder / "request.json").exists() or (folder / "processing.json").exists()

    def _deployed_head(self) -> str:
        workspace = self.engine.workspace
        return workspace._git("rev-parse", f"origin/{workspace.base}", check=False).strip() or workspace.base

    def _deploys_today(self) -> int:
        return sum(1 for r in self._store.query("SELECT state, data FROM evolve_auto WHERE at > ?",
                                                (self._clock() - DAY,))
                   if r["state"] in (DEPLOYING, DEPLOYED, ROLLING_BACK, ROLLED_BACK))

    def collect(self) -> int:
        """Lit le résultat du service de déploiement et vous prévient."""
        path = Path(self.settings.request_dir) / "result.json"
        if not path.exists():
            return 0
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return 0
        path.unlink(missing_ok=True)
        row = self._store.one("SELECT * FROM evolve_auto WHERE id = ?", (str(result.get("id", "")),))
        if row is None:
            return 0
        data = loads(row["data"], {})
        ok, kind = result.get("state") == "ok", result.get("kind", "deploy")
        message = str(result.get("message", ""))[:400]
        if kind == "rollback":
            state = ROLLED_BACK if ok else DEPLOYED
            self._tell("Retour arrière fait" if ok else "Retour arrière en échec",
                       f"{data.get('summary', '')} {message}".strip(), row["id"])
        else:
            state = DEPLOYED if ok else DEPLOY_FAILED
            self._tell("Amélioration déployée" if ok else "Amélioration non déployée",
                       (f"{data.get('summary', '')} Retour arrière possible dans ORION Control." if ok else
                        f"{data.get('summary', '')} — {message}").strip(), row["id"])
        self._update(row["id"], state, {"deploy": result})
        return 1

    def rollback(self, action: str, by: str) -> dict:
        if by != "utilisateur":
            raise PolicyError("seul l'utilisateur demande un retour arrière")
        row = self._store.one("SELECT * FROM evolve_auto WHERE id = ?", (action,))
        if row is None:
            raise KeyError(action)
        data = loads(row["data"], {})
        if row["state"] != DEPLOYED or not data.get("before") or not data.get("commit"):
            raise ValueError(f"rien à défaire (état : {row['state']})")
        if self._in_flight():
            raise ValueError("un déploiement est déjà en cours")
        self._request({"id": action, "kind": "rollback", "to": data["before"], "commit": data["commit"]})
        self._update(action, ROLLING_BACK, {})
        self.engine._history.record("evolve", action, "retour arrière demandé", {"par": by})
        return self.action(action)

    # --- Enregistrement ----------------------------------------------------------------------------------------

    def _save(self, ident: str, kind: str, source: str, state: str, data: dict) -> None:
        self._store.write("INSERT INTO evolve_auto (id, at, kind, source, state, data) VALUES (?, ?, ?, ?, ?, ?)",
                          (ident, self._clock(), kind, source, state, dumps(data)))

    def _update(self, ident: str, state: str, extra: dict) -> None:
        row = self._store.one("SELECT data FROM evolve_auto WHERE id = ?", (ident,))
        data = {**(loads(row["data"], {}) if row else {}), **extra}
        self._store.write("UPDATE evolve_auto SET state = ?, data = ? WHERE id = ?", (state, dumps(data), ident))

    def _finish(self, action: str, state: str, data: dict) -> str:
        self._update(action, state, data)
        self.engine._history.record("evolve", action, "autonome", {"état": state,
                                                                   "résumé": str(data.get("summary", ""))[:200]})
        return state

    def _tell(self, title: str, message: str, action: str) -> None:
        alerts = self.engine._alerts
        if alerts is None:
            return
        try:
            alerts.deliver(Alert("evolve", title, message[:500], AlertLevel.NORMAL, key=f"auto:{action}:{title}",
                                 call_allowed=False, written_only=True))
        except Exception:  # noqa: BLE001 - l'alerte ne doit pas casser le cycle
            log.exception("EVOLVE autonome : alerte non remise")

    def _rows(self, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM evolve_auto" + (" WHERE kind = ?" if kind else "") + " ORDER BY at"
        return [{**dict(r), "data": loads(r["data"], {})} for r in self._store.query(sql, (kind,) if kind else ())]

    def action(self, ident: str) -> dict:
        row = self._store.one("SELECT * FROM evolve_auto WHERE id = ?", (ident,))
        return {**dict(row), "data": loads(row["data"], {})} if row else {}

    def actions(self) -> list[dict]:
        return list(reversed(self._rows()))[:100]

    def status(self) -> dict:
        reason = self.ready() or self.blocked
        return {"actif": self.enabled, "prêt": not reason, "raison": reason,
                "incomprises_en_attente": sum(r["state"] == PENDING_MISUNDERSTOOD for r in self._rows("incomprise")),
                "déploiements_24h": self._deploys_today(), "déploiements_max": self.settings.deploys_per_day,
                "en_déploiement": self._in_flight()}
