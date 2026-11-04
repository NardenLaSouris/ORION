"""KNOWLEDGE (V3) : connaissances personnelles d'ORION, structurées, sourcées, datées, chiffrées (lot F4).

- ``model`` : entités, assertions (faits, préférences, intérêts, hypothèses), provenances, historique, conflits ;
- ``base`` : règles (dédoublonnage, contradictions, confiance, expiration, oubli vérifiable) ;
- ``module`` : module V3, interface tracée des autres modules, routes d'ORION Control ;
- ``importers`` : imports idempotents (``memory.json`` de V2).

Document : ``docs/v3/knowledge.md``.
"""

from orion.knowledge.base import KnowledgeBase, KnowledgeError
from orion.knowledge.module import KnowledgeModule, KnowledgeReader, KnowledgeSettings

__all__ = ["KnowledgeBase", "KnowledgeError", "KnowledgeModule", "KnowledgeReader", "KnowledgeSettings"]
