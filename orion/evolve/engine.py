"""ORION EVOLVE : observation, diagnostic, correctifs préparés et propositions d'évolution, sous permissions strictes.

- Niveau 1 (par défaut) : analyse périodique du journal -> anomalies (logicielles, extérieures, latence), diagnostic
  automatique, propositions en file de validation. Rien n'est modifié.
- Niveau 2 (activation explicite) : correctif d'une anomalie logicielle ou d'une proposition **acceptée par
  l'utilisateur**, dans l'espace de travail isolé : branche « evolve/… », modifications vérifiées par la politique, test
  de non-régression obligatoire, vérifications (tests, compilation, sécurité), commit seulement si tout passe, Pull
  Request préparée (fichier) et poussée seulement si un dépôt distant est configuré.
- Niveau 3 : refusé. Aucune fusion, aucun déploiement, aucun accès à l'installation active.
Toute action est tracée (historique commun) ; le niveau ne change que par l'utilisateur (ORION Control).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from orion.evolve import policy
from orion.evolve.diagnose import diagnose
from orion.evolve.observe import SOFTWARE, Anomaly, journal, parse
from orion.evolve.policy import Level, PolicyError
from orion.evolve.workspace import Workspace
from orion.platform.alerts import Alert, AlertRouter, Level as AlertLevel
from orion.platform.history import History
from orion.platform.intelligence import StructuredLLM, validate
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.scheduler import Scheduler
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

PENDING, ACCEPTED, REJECTED = "en attente", "acceptée", "refusée"
READY, PUSHED, FAILED_CHECKS, REFUSED = "prêt (PR préparée)", "poussé", "vérifications en échec", "refusé"
RECURRING = 3  # occurrences avant une proposition automatique de correction

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE evolve_anomalies (
            signature TEXT PRIMARY KEY, kind TEXT NOT NULL, count INTEGER NOT NULL, first_at REAL NOT NULL,
            last_at REAL NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ouverte'
        );
        CREATE TABLE evolve_diagnoses (anomaly TEXT PRIMARY KEY, at REAL NOT NULL, data TEXT NOT NULL);
        CREATE TABLE evolve_patches (
            id TEXT PRIMARY KEY, origin TEXT NOT NULL, branch TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL,
            files TEXT NOT NULL, report TEXT NOT NULL, commit_id TEXT NOT NULL DEFAULT '', pr TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE evolve_proposals (id TEXT PRIMARY KEY, at REAL NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE evolve_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)
    """),
)
PROPOSAL_FIELDS = ("title", "description", "interest", "difficulty", "dependencies", "risks", "cost", "plan", "tests")
PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": list(PROPOSAL_FIELDS),
    "properties": {**{k: {"type": "string", "maxLength": 1200} for k in PROPOSAL_FIELDS},
                   "difficulty": {"type": "string", "enum": ["faible", "moyenne", "élevée"]}},
}
EDITS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["edits", "message"],
    "properties": {
        "message": {"type": "string", "maxLength": 300},
        "edits": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["path"],
            "properties": {"path": {"type": "string", "maxLength": 200}, "search": {"type": "string",
                                                                                    "maxLength": 6000},
                           "replace": {"type": "string", "maxLength": 8000},
                           "content": {"type": "string", "maxLength": 20000}}}},
    },
}
EDITS_TASK = ("Écris le correctif minimal de cette anomalie d'ORION : des remplacements exacts (search apparaît une "
              "seule fois dans le fichier) et un nouveau fichier de test de non-régression dans tests/. Ne touche ni "
              "à la sécurité, ni aux permissions, ni aux secrets, ni aux dépendances.")


@dataclass
class EvolveSettings:
    level: int = 1
    scan_minutes: float = 60.0
    units: tuple[str, ...] = ("orion",)
    remote: str = ""
    external_threshold: int = 50
    latency_threshold: float = 6.0


@dataclass
class Proposal:
    title: str
    description: str
    interest: str = ""
    difficulty: str = "moyenne"
    dependencies: str = ""
    risks: str = ""
    cost: str = ""
    plan: str = ""
    tests: str = ""
    origin: str = "utilisateur"  # usage, erreurs, demande, limitation, opportunité
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


class EvolveEngine(BaseModule):
    name, title = "evolve", "EVOLVE"

    def __init__(self, store: Store, workspace: Workspace | None, *, settings: EvolveSettings | None = None,
                 llm: StructuredLLM | None = None, alerts: AlertRouter | None = None,
                 scheduler: Scheduler | None = None, history: History | None = None,
                 logs: Callable[[], list[str]] | None = None, clock: Callable[[], float] = time.time):
        self._store, self.workspace, self._llm, self._alerts = store, workspace, llm, alerts
        self._defaults = settings or EvolveSettings()
        self._scheduler = scheduler or Scheduler(clock)
        self._history = history or History(store, clock)
        self._logs = logs or (lambda: journal(self._defaults.units))
        self._clock = clock
        store.migrate("evolve", MIGRATIONS)
        self.level_note = ""
        self.level = self._effective_level()

    # --- Niveau (l'utilisateur seul le change) ---------------------------------------------------------------

    def _effective_level(self) -> Level:
        row = self._store.one("SELECT value FROM evolve_settings WHERE key = 'level'")
        wanted = loads(row["value"]) if row else self._defaults.level
        level, note = policy.clamp(wanted)
        self.level_note = note
        return level

    def set_level(self, value: Any, by: str) -> Level:
        """Appelé par l'API d'ORION Control seulement (``by`` : « utilisateur ») ; niveau 3 refusé."""
        if by != "utilisateur":
            raise PolicyError("seul l'utilisateur change le niveau d'EVOLVE")
        if value not in (1, 2) or isinstance(value, bool):
            raise PolicyError("niveau 1 (observateur) ou 2 (développeur) ; le niveau 3 est désactivé")
        self._store.write("INSERT INTO evolve_settings (key, value) VALUES ('level', ?) ON CONFLICT(key) DO UPDATE "
                          "SET value = excluded.value", (dumps(value),))
        previous, self.level = self.level, self._effective_level()
        self._history.record("evolve", "", "niveau", {"de": int(previous), "vers": int(self.level), "par": by})
        return self.level

    # --- Cycle de vie ----------------------------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.add("evolve:scan", self._defaults.scan_minutes * 60, self.scan,
                            first_at=self._clock() + 300)  # laisse ORION démarrer

    def stop(self) -> None:
        self._scheduler.remove("evolve:scan")

    # --- Observation -----------------------------------------------------------------------------------------

    def scan(self, lines: list[str] | None = None) -> dict:
        policy.require(self.level, policy.OBSERVE)
        anomalies = parse(lines if lines is not None else self._logs(), self._defaults.external_threshold,
                          self._defaults.latency_threshold)
        now = self._clock()
        new = []
        for a in anomalies:
            row = self._store.one("SELECT count, data FROM evolve_anomalies WHERE signature = ?", (a.signature,))
            data = asdict(a)
            if row is None:
                self._store.write("INSERT INTO evolve_anomalies (signature, kind, count, first_at, last_at, data) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (a.signature, a.kind, a.count, now, now, dumps(data)))
                new.append(a)
            else:
                total = row["count"] + a.count
                data["count"] = total
                self._store.write("UPDATE evolve_anomalies SET count = ?, last_at = ?, data = ? WHERE signature = ?",
                                  (total, now, dumps(data), a.signature))
        for a in new:
            self._history.record("evolve", a.signature, "anomalie", {"type": a.kind, "titre": a.title,
                                                                     "occurrences": a.count})
            if a.kind == SOFTWARE:
                self.diagnose(a.signature)
                if self._alerts is not None:  # gardée pour ORION Control, jamais dite ni appelée
                    self._alerts.deliver(Alert("evolve", f"Anomalie logicielle : {a.title[:80]}",
                                               f"{a.count} occurrence(s) ; diagnostic disponible dans ORION Control.",
                                               AlertLevel.INFO, key=f"anomaly:{a.signature}"))
        self._propose_recurring()
        return {"anomalies": len(anomalies), "nouvelles": len(new)}

    def anomaly(self, signature: str) -> Anomaly | None:
        row = self._store.one("SELECT data FROM evolve_anomalies WHERE signature = ?", (signature,))
        return Anomaly(**loads(row["data"], {})) if row else None

    def anomalies(self) -> list[dict]:
        rows = self._store.query("SELECT * FROM evolve_anomalies ORDER BY last_at DESC LIMIT 200")
        return [{**loads(r["data"], {}), "status": r["status"], "first_at": r["first_at"], "last_at": r["last_at"]}
                for r in rows]

    # --- Diagnostic ------------------------------------------------------------------------------------------

    def diagnose(self, signature: str) -> dict:
        policy.require(self.level, policy.DIAGNOSE)
        anomaly = self.anomaly(signature)
        if anomaly is None:
            raise KeyError(signature)
        workspace = None
        if self.workspace is not None:
            try:
                self.workspace.ensure()
                workspace = self.workspace
            except Exception as exc:  # noqa: BLE001 - diagnostic sans extraits de code plutôt que pas de diagnostic
                log.warning("EVOLVE : espace de travail indisponible pour le diagnostic (%s)", exc)
        result = asdict(diagnose(anomaly, workspace, self._llm))
        self._store.write("INSERT INTO evolve_diagnoses (anomaly, at, data) VALUES (?, ?, ?) ON CONFLICT(anomaly) "
                          "DO UPDATE SET at = excluded.at, data = excluded.data", (signature, self._clock(),
                                                                                  dumps(result)))
        self._history.record("evolve", signature, "diagnostic", {"résumé": result["summary"], "par": result["by"]})
        return result

    def diagnosis(self, signature: str) -> dict | None:
        row = self._store.one("SELECT data FROM evolve_diagnoses WHERE anomaly = ?", (signature,))
        return loads(row["data"], {}) if row else None

    # --- Propositions ----------------------------------------------------------------------------------------

    def propose(self, data: dict, origin: str = "utilisateur") -> dict:
        policy.require(self.level, policy.PROPOSE)
        if not isinstance(data, dict):
            raise ValueError("proposition attendue")
        clean = {k: str(data.get(k, "") or "").strip()[:1200] for k in PROPOSAL_FIELDS}
        if not clean["title"] or not clean["description"]:
            raise ValueError("titre et description obligatoires")
        if clean["difficulty"] not in ("faible", "moyenne", "élevée"):
            clean["difficulty"] = "moyenne"
        proposal = Proposal(**{**clean, "origin": origin})
        self._store.write("INSERT INTO evolve_proposals (id, at, status, data) VALUES (?, ?, ?, ?)",
                          (proposal.id, self._clock(), PENDING, dumps(asdict(proposal))))
        self._history.record("evolve", proposal.id, "proposition", {"titre": proposal.title, "origine": origin})
        return {**asdict(proposal), "status": PENDING}

    def suggest(self, context: str) -> dict | None:
        """Le LLM rédige une proposition à partir d'un contexte (usages, erreurs) ; elle rejoint la file d'attente,
        jamais le développement."""
        if self._llm is None or not self._llm.usable:
            return None
        answer = self._llm.ask("Rédige une proposition d'amélioration d'ORION (description, intérêt, difficulté, "
                               "dépendances, risques, coût estimé, plan de réalisation, tests nécessaires).",
                               PROPOSAL_SCHEMA, {"contexte": context[:3000]})
        return self.propose(answer, "llm") if answer else None

    def decide(self, proposal_id: str, accepted: bool, by: str) -> dict:
        if by != "utilisateur":
            raise PolicyError("seul l'utilisateur accepte ou refuse une proposition")
        row = self._store.one("SELECT status, data FROM evolve_proposals WHERE id = ?", (proposal_id,))
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] != PENDING:
            raise ValueError(f"proposition déjà {row['status']}")
        status = ACCEPTED if accepted else REJECTED
        self._store.write("UPDATE evolve_proposals SET status = ? WHERE id = ?", (status, proposal_id))
        self._history.record("evolve", proposal_id, "décision", {"statut": status, "par": by})
        return {**loads(row["data"], {}), "status": status}

    def proposals(self) -> list[dict]:
        return [{**loads(r["data"], {}), "status": r["status"], "at": r["at"]} for r in
                self._store.query("SELECT * FROM evolve_proposals ORDER BY at DESC LIMIT 200")]

    def _propose_recurring(self) -> None:
        for row in self._store.query("SELECT signature, data FROM evolve_anomalies WHERE kind = ? AND count >= ? "
                                     "AND status = 'ouverte'", (SOFTWARE, RECURRING)):
            a = loads(row["data"], {})
            marker = f"[anomalie {row['signature']}]"
            if any(marker in p["description"] for p in self.proposals()):
                continue
            files = ", ".join(dict.fromkeys(f["path"] for f in a.get("frames", []) if f["path"].startswith("orion/")))
            self.propose({"title": f"Corriger : {a['title'][:100]}",
                          "description": f"Erreur répétée ({a['count']} fois) {marker}.",
                          "interest": "supprimer une erreur récurrente", "difficulty": "faible",
                          "dependencies": files, "risks": "régression dans le module concerné",
                          "cost": "faible", "plan": "diagnostic, correctif minimal, test de non-régression",
                          "tests": "test reproduisant la trace, suite complète"}, "erreurs")

    # --- Correctifs (niveau 2) -------------------------------------------------------------------------------

    def patch(self, origin: str, edits: list[dict] | None = None, message: str = "") -> dict:
        """``origin`` : signature d'une anomalie logicielle, ou identifiant d'une proposition **acceptée**.
        ``edits`` fournis (ORION Control) ou rédigés par le LLM. Rend l'état du correctif."""
        for action in (policy.BRANCH, policy.EDIT, policy.TEST):
            policy.require(self.level, action)
        if self.workspace is None:
            raise PolicyError("aucun espace de travail configuré")
        context = self._patch_context(origin)
        if edits is None:
            answer = self._llm.ask(EDITS_TASK, EDITS_SCHEMA, context) if self._llm is not None else None
            if answer is None:
                raise ValueError("LLM indisponible ou réponse refusée : correctif à fournir à la main")
            edits, message = answer["edits"], answer["message"]
        else:
            validate({"edits": edits, "message": message or "Correctif"}, EDITS_SCHEMA)
        if not any(str(e.get("path", "")).startswith("tests/") for e in edits):
            raise PolicyError("un test de non-régression (fichier dans tests/) est obligatoire")
        patch_id = uuid.uuid4().hex[:8]
        branch = f"evolve/{re.sub(r'[^a-z0-9]+', '-', origin.lower())[:30].strip('-')}-{patch_id}"
        self.workspace.start_branch(branch)
        record = {"id": patch_id, "origin": origin, "branch": branch, "files": [], "report": {}, "commit_id": "",
                  "pr": ""}
        try:
            record["files"] = self.workspace.apply(edits)
            report = self.workspace.check()
            record["report"] = asdict(report)
            if not report.passed:
                self.workspace.discard(branch)
                return self._save_patch(record, FAILED_CHECKS)
            policy.require(self.level, policy.COMMIT)
            record["commit_id"] = self.workspace.commit(f"EVOLVE : {message or origin}\n\nOrigine : {origin}", report)
            record["pr"] = str(self._write_pr(record, message))
            state = READY
            if self._defaults.remote:
                policy.require(self.level, policy.PULL_REQUEST)
                self.workspace.push(self._defaults.remote, branch)
                state = PUSHED
            return self._save_patch(record, state)
        except (PolicyError, ValueError, RuntimeError) as exc:  # rien ne reste à moitié fait
            self.workspace.discard(branch)
            record["report"] = {"passed": False, "problems": [str(exc)], "steps": []}
            return self._save_patch(record, REFUSED)

    def _patch_context(self, origin: str) -> dict[str, str]:
        diagnosis = self.diagnosis(origin)
        if diagnosis is not None:
            anomaly = self.anomaly(origin)
            if anomaly is None or anomaly.kind != SOFTWARE:
                raise PolicyError("seule une anomalie logicielle se corrige dans le code")
            return {"diagnostic": dumps({k: diagnosis[k] for k in ("summary", "evidence", "hypotheses", "solutions",
                                                                   "files")}),
                    **{k[:30]: v for k, v in diagnosis.get("code", {}).items()}}
        row = self._store.one("SELECT status, data FROM evolve_proposals WHERE id = ?", (origin,))
        if row is None:
            raise KeyError(origin)
        if row["status"] != ACCEPTED:
            raise PolicyError("proposition non acceptée par l'utilisateur : aucun développement")
        return {"proposition": row["data"]}

    def _write_pr(self, record: dict, message: str) -> Path:
        folder = self.workspace.root / "pull-requests"
        folder.mkdir(parents=True, exist_ok=True)
        steps = "\n".join(f"- {s['étape']} : {'réussi' if s['ok'] else 'échec'}" for s in record["report"]["steps"])
        path = folder / f"{record['id']}.md"
        path.write_text(f"# {message or record['origin']}\n\nBranche : `{record['branch']}`\nCommit : "
                        f"`{record['commit_id']}`\nOrigine : {record['origin']}\n\n## Fichiers\n\n"
                        + "\n".join(f"- `{f}`" for f in record["files"]) + f"\n\n## Vérifications\n\n{steps}\n\n"
                        "Préparé par ORION EVOLVE (niveau développeur). Aucune fusion ni aucun déploiement "
                        "automatique : relecture et décision humaines.\n", encoding="utf-8")
        return path

    def _save_patch(self, record: dict, state: str) -> dict:
        self._store.write("INSERT INTO evolve_patches (id, origin, branch, state, at, files, report, commit_id, pr) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (record["id"], record["origin"], record["branch"], state, self._clock(),
                           dumps(record["files"]), dumps(record["report"]), record["commit_id"], record["pr"]))
        self._history.record("evolve", record["id"], "correctif", {"état": state, "branche": record["branch"],
                                                                   "origine": record["origin"]})
        return {**record, "state": state}

    def patches(self) -> list[dict]:
        return [{**r, "files": loads(r["files"], []), "report": loads(r["report"], {})} for r in
                self._store.query("SELECT * FROM evolve_patches ORDER BY at DESC LIMIT 100")]

    def merge(self, *_: Any) -> None:
        policy.require(self.level, policy.MERGE)  # lève toujours : niveau 3 désactivé

    def deploy(self, *_: Any) -> None:
        policy.require(self.level, policy.DEPLOY)  # lève toujours : niveau 3 désactivé

    # --- ORION Control ---------------------------------------------------------------------------------------

    def status(self) -> dict:
        return {"niveau": int(self.level), "niveau_libellé": self.level.label, "remarque": self.level_note,
                "anomalies": len(self.anomalies()), "propositions_en_attente": sum(p["status"] == PENDING
                                                                                  for p in self.proposals()),
                "correctifs": len(self.patches()), "dépôt_distant": bool(self._defaults.remote)}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        try:
            if not parts and method == "GET":
                return 200, self.status()
            if parts == ["level"] and method == "PUT":
                level = self.set_level((data or {}).get("level"), "utilisateur")
                return 200, {"niveau": int(level), "remarque": self.level_note}
            if parts == ["scan"] and method == "POST":
                return 200, self.scan()
            if parts == ["anomalies"] and method == "GET":
                return 200, self.anomalies()
            if len(parts) == 2 and parts[0] == "anomalies":
                if method == "GET":
                    anomaly = self.anomaly(parts[1])
                    return (200, {**asdict(anomaly), "diagnosis": self.diagnosis(parts[1])}) if anomaly else NOT_FOUND
                if method == "POST":
                    return 200, self.diagnose(parts[1])
            if parts == ["patches"]:
                if method == "GET":
                    return 200, self.patches()
                if method == "POST":
                    body = data or {}
                    return 201, self.patch(str(body.get("origin", "")), body.get("edits"), str(body.get("message", "")))
            if parts == ["proposals"]:
                if method == "GET":
                    return 200, self.proposals()
                if method == "POST":
                    return 201, self.propose(data)
            if len(parts) == 3 and parts[0] == "proposals" and method == "POST" and parts[2] in ("accept", "reject"):
                return 200, self.decide(parts[1], parts[2] == "accept", "utilisateur")
            if parts == ["history"] and method == "GET":
                return 200, self._history.list("evolve", limit=200)
        except KeyError:
            return 404, {"status": "error", "error": "not_found"}
        except PolicyError as exc:
            return 403, {"status": "error", "error": "forbidden", "message": str(exc)}
        except ValueError as exc:
            return 400, {"status": "error", "error": "invalid", "message": str(exc)}
        return NOT_FOUND
