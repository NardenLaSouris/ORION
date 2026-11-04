"""Module KNOWLEDGE (V3) : la base de connaissances partagée par ORION (docs/v3/knowledge.md).

- **Seul gardien** des connaissances personnelles : les autres modules (NEWS, MISSIONS, PROACTIVE…) n'y touchent que
  par ``reader(principal)``, une interface déclarée et tracée :
  - ``context(requête, but)`` : lecture (capacité ``READ``), décidée par le moteur central et inscrite au registre
    d'actions (principal, but, nombre de réponses ; jamais le contenu) ; un résumé minimal est rendu (texte, nature,
    confiance, état), sans provenance ;
  - ``propose(...)`` : une **hypothèse** seulement (capacité ``WRITE_DATA``), refusée pendant la suspension de
    l'autonomie comme toute écriture d'initiative ; jamais sur une catégorie sensible.
- **ORION Control** (section Connaissances) : état, recherche, fiche avec provenance et historique, arbitrage des
  contradictions, confirmation, retrait, oubli, export ; chaque action passe par le moteur central (« admin »).
- **Conservation** : ``knowledge:retention`` une fois par jour (règle que vous avez fixée : tâche « delegated »).
- **Base verrouillée** (clé absente) : le module reste actif et le dit ; lectures vides, écritures refusées.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from orion.knowledge.base import KnowledgeBase, KnowledgeError
from orion.knowledge.model import KINDS
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.scheduler import Scheduler

log = logging.getLogger(__name__)

RETENTION_JOB = "knowledge:retention"
LOCKED = (423, {"status": "error", "error": "locked",
                "message": "Base personnelle verrouillée : les connaissances ne sont pas accessibles (clé absente)."})


def _locked_error(exc: Exception) -> bool:
    from orion.platform.vault import Locked

    return isinstance(exc, Locked)


@dataclass
class KnowledgeSettings:
    retention_hours: float = 24.0
    context_limit: int = 8


class KnowledgeReader:
    """Ce qu'un module reçoit : lire selon un but déclaré, proposer une hypothèse. Principal fixé une fois."""

    __slots__ = ("_module", "principal")

    def __init__(self, module: "KnowledgeModule", principal: str):
        self._module, self.principal = module, principal

    def context(self, query: str, purpose: str, limit: int | None = None, kinds=KINDS) -> list[dict]:
        return self._module.context_for(self.principal, query, purpose, limit, kinds)

    def propose(self, predicate: str, value: str, *, subject: str = "self", text: str = "", source: str = "",
                obj: str = "") -> dict | None:
        return self._module.propose(self.principal, predicate, value, subject=subject, text=text, source=source,
                                    obj=obj)


class KnowledgeModule(BaseModule):
    name, title = "knowledge", "Connaissances"

    def __init__(self, base: KnowledgeBase, *, decide: Callable[..., Any] | None = None,
                 scheduler: Scheduler | None = None, settings: KnowledgeSettings | None = None):
        self.base, self._decide = base, decide
        self._scheduler = scheduler or Scheduler()
        self.settings = settings or KnowledgeSettings()
        self._readers: dict[str, KnowledgeReader] = {}

    # --- Cycle de vie --------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.add(RETENTION_JOB, self.settings.retention_hours * 3600, self.retention)

    def stop(self) -> None:
        self._scheduler.remove(RETENTION_JOB)

    def retention(self) -> int:
        if self.base.locked:
            return 0
        return self.base.expire()

    # --- Interface des autres modules ------------------------------------------------------------------------------

    def reader(self, principal: str) -> KnowledgeReader:
        if principal in ("user", "admin") or ":" not in principal:
            raise ValueError(f"principal de module attendu : {principal!r}")
        return self._readers.setdefault(principal, KnowledgeReader(self, principal))

    def _allowed(self, principal: str, capability: str, action: str, context: dict) -> bool:
        if self._decide is None:
            return True
        verdict = self._decide(principal, capability, context, action=action)
        return bool(getattr(verdict, "allowed", False))

    def context_for(self, principal: str, query: str, purpose: str, limit: int | None = None,
                    kinds=KINDS) -> list[dict]:
        purpose = " ".join(str(purpose).split())[:60]
        if not purpose:
            raise ValueError("une lecture déclare son but")
        if self.base.locked or not self._allowed(principal, "READ", "knowledge:read", {"purpose": purpose}):
            return []
        found = self.base.search(query, kinds=kinds, limit=limit or self.settings.context_limit)
        return [{"text": a.text, "kind": a.kind, "confidence": a.confidence, "status": a.status}
                for _, a in found]

    def propose(self, principal: str, predicate: str, value: str, **kwargs) -> dict | None:
        """Hypothèse d'un module ; None si refusée (suspension, catégorie sensible, base verrouillée)."""
        if self.base.locked or not self._allowed(principal, "WRITE_DATA", "knowledge:propose", {}):
            return None
        try:
            a = self.base.tell(predicate, value, kind="hypothesis", principal=principal, **kwargs)
        except KnowledgeError as exc:
            log.info("Connaissances : proposition de %s refusée (%s)", principal, exc)
            return None
        return {"id": a.id, "status": a.status, "confidence": a.confidence}

    # --- État et ORION Control ----------------------------------------------------------------------------------

    def status(self) -> dict:
        if self.base.locked:
            return {"locked": True}
        try:
            return {"locked": False, **self.base.stats()}
        except Exception as exc:  # noqa: BLE001
            if _locked_error(exc):
                return {"locked": True}
            raise

    @staticmethod
    def _brief(a) -> dict:
        return {"id": a.id, "kind": a.kind, "subject": a.subject, "predicate": a.predicate, "value": a.value,
                "object": a.object, "negated": a.negated, "text": a.text, "confidence": a.confidence,
                "status": a.status, "updated_at": a.updated_at, "expires_at": a.expires_at,
                "sources": sorted({p.principal for p in a.provenance}), "conflict": a.conflict}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        first = lambda k: (query.get(k) or [""])[0]  # noqa: E731
        body = data if isinstance(data, dict) else {}
        try:
            if method == "GET" and not parts:
                return 200, self.status()
            if self.base.locked:
                return LOCKED
            if method == "GET" and parts == ["assertions"]:
                kinds = [first("kind")] if first("kind") in KINDS else list(KINDS)
                items = self.base.about(first("subject") or "self", kinds=kinds,
                                        include_inactive=first("all") == "1")
                return 200, [self._brief(a) for a in items]
            if method == "GET" and parts == ["search"]:
                return 200, [{"score": s, **self._brief(a)} for s, a in self.base.search(first("q"), limit=30)]
            if method == "GET" and len(parts) == 2 and parts[0] == "assertions":
                return 200, self.base.get(parts[1]).as_dict()
            if method == "GET" and parts == ["entities"]:
                return 200, [e.as_dict() for e in self.base.entities()]
            if method == "GET" and parts == ["conflicts"]:
                out = []
                for c in self.base.conflicts():
                    out.append({**c.as_dict(), "choices": [self._brief(self.base.get(i)) for i in c.assertions
                                                           if self._exists(i)]})
                return 200, out
            if method == "GET" and parts == ["export"]:
                if not self._allowed("admin", "READ", "knowledge:export", {}):
                    return 403, {"status": "error", "error": "forbidden"}
                return 200, self.base.export()
            if method == "POST" and parts == ["assertions"]:
                if not self._allowed("admin", "WRITE_DATA", "knowledge:tell", {}):
                    return 403, {"status": "error", "error": "forbidden"}
                a = self.base.tell(str(body.get("predicate", "")), str(body.get("value", "")),
                                   subject=str(body.get("subject") or "self"), kind=str(body.get("kind", "fact")),
                                   principal="admin", source="ORION Control", text=str(body.get("text", "")),
                                   negated=bool(body.get("negated", False)))
                return 200, self._brief(a)
            if method == "POST" and len(parts) == 3 and parts[0] == "assertions" and parts[2] in ("confirm", "retract"):
                if not self._allowed("admin", "WRITE_DATA", f"knowledge:{parts[2]}", {}):
                    return 403, {"status": "error", "error": "forbidden"}
                a = (self.base.confirm if parts[2] == "confirm" else self.base.retract)(parts[1], by="admin")
                return 200, self._brief(a)
            if method == "DELETE" and len(parts) == 2 and parts[0] == "assertions":
                if not self._allowed("admin", "WRITE_DATA", "knowledge:forget", {}):
                    return 403, {"status": "error", "error": "forbidden"}
                return 200, {"forgotten": self.base.forget(parts[1], by="admin")}
            if method == "POST" and len(parts) == 3 and parts[0] == "conflicts" and parts[2] == "resolve":
                if not self._allowed("admin", "WRITE_DATA", "knowledge:resolve", {}):
                    return 403, {"status": "error", "error": "forbidden"}
                keep = body.get("keep")
                c = self.base.resolve(parts[1], keep if isinstance(keep, str) and keep else None, by="admin")
                return 200, c.as_dict()
        except KnowledgeError as exc:
            return 400, {"status": "error", "error": "invalid", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            if _locked_error(exc):
                return LOCKED
            raise
        return NOT_FOUND

    def _exists(self, assertion_id: str) -> bool:
        try:
            self.base.get(assertion_id)
            return True
        except KnowledgeError:
            return False
