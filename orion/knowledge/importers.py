"""Imports vers KNOWLEDGE. Idempotents (deux passages, même résultat) ; la source n'est jamais modifiée.

- ``import_memory_facts`` : faits retenus par ORION V2 (``data/memory.json`` : ``id``, ``text``, ``category``,
  ``user``, ``created``). Chaque fait devient une assertion « note » (préférence si sa catégorie l'était), de
  provenance ``import:memory.json`` et de source ``memory.json#<id>``.

L'import de vos vraies données n'est jamais lancé par ORION de lui-même : il attend votre décision (vraie clé de la
base personnelle, puis autorisation explicite ; docs/v3/knowledge.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from orion.knowledge.base import KnowledgeBase, KnowledgeError

PRINCIPAL = "import:memory.json"


def plan_memory_facts(facts: Iterable[dict], user: str = "owner") -> list[dict]:
    """Ce que l'import ferait (sans clé, sans rien écrire) : une ligne par fait valide de l'utilisateur."""
    out = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("user", "owner") != user:
            continue
        text = " ".join(str(fact.get("text", "")).split())
        if not text or not fact.get("id"):
            continue
        kind = "preference" if fact.get("category") == "preference" else "fact"
        created = fact.get("created")
        try:
            at = datetime.fromisoformat(created).timestamp() if isinstance(created, str) else None
        except ValueError:
            at = None
        out.append({"source": f"memory.json#{fact['id']}", "kind": kind, "text": text, "at": at})
    return out


def import_memory_facts(base: KnowledgeBase, facts: Iterable[dict], user: str = "owner") -> dict:
    added = skipped = refused = 0
    for item in plan_memory_facts(facts, user):
        if base.known_source(item["source"]):
            skipped += 1
            continue
        try:
            base.tell("note", item["text"], kind=item["kind"], principal=PRINCIPAL, source=item["source"],
                      text=item["text"])
            added += 1
        except KnowledgeError:
            refused += 1
    return {"added": added, "skipped": skipped, "refused": refused}
