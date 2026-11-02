"""Synthèse des événements et composition des bulletins.

- Sans LLM (mode dégradé, ou réponse refusée) : titre, statut, sources et dates, rien d'autre.
- Avec le LLM : résumé court de faits confirmés et d'éléments incertains, à partir des seuls articles fournis (données
  non fiables isolées). La réponse est refusée si elle contient un nombre absent des articles, une adresse Web, une
  citation entre guillemets ou un nom de source inconnu : rien n'est inventé (source, citation, date, chiffre).
- Les faits confirmés, les analyses et les hypothèses sont présentés séparément.
"""

from __future__ import annotations

import re
from datetime import datetime

from orion.news.pipeline import Article, Story, numbers
from orion.platform.intelligence import StructuredLLM

CATEGORY_LABELS = {"geopolitique": "Géopolitique", "defense": "Défense", "tech_ia": "Technologie et IA",
                   "france": "France", "economie": "Économie"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "confirmed", "uncertain"],
    "properties": {
        "summary": {"type": "string", "maxLength": 400},
        "confirmed": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 200}},
        "uncertain": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 200}},
    },
}
TASK = ("Résume cet événement d'actualité en français, en deux phrases au plus, à partir des seuls articles fournis. "
        "« confirmed » : faits rapportés par plusieurs sources ou par une source officielle ; « uncertain » : ce qui "
        "reste incertain ou n'est rapporté que par une source. Pas de citation, pas d'adresse, pas de chiffre qui "
        "n'est pas dans les articles.")
QUOTE = re.compile(r"[«\"“”]")


def when(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%d/%m %H:%M")


def sources_line(articles: list[Article], limit: int = 4) -> str:
    seen, parts = set(), []
    for a in sorted(articles, key=lambda a: a.published_at):
        if a.domain in seen:
            continue
        seen.add(a.domain)
        parts.append(f"{a.source} ({when(a.published_at)})")
    return ", ".join(parts[:limit]) + (f" et {len(parts) - limit} autre(s)" if len(parts) > limit else "")


def grounded(answer: dict, articles: list[Article]) -> bool:
    texts = " ".join(f"{a.title} {a.summary}" for a in articles)
    allowed = numbers(texts) | {n.lstrip("0") for n in numbers(texts)}
    written = " ".join([answer["summary"], *answer["confirmed"], *answer["uncertain"]])
    if "http" in written.lower() or "www." in written.lower() or QUOTE.search(written):
        return False
    return all(n in allowed or n.lstrip("0") in allowed for n in numbers(written))


def summarize(story: Story, articles: list[Article], llm: StructuredLLM | None = None) -> dict:
    """{"title", "status", "summary", "confirmed", "uncertain", "sources", "by"} ; jamais d'invention."""
    base = {"title": story.title, "status": story.status, "sources": sources_line(articles), "summary": "",
            "confirmed": [], "uncertain": [], "by": "règles"}
    if llm is None or not llm.usable or not articles:
        return base
    blocks = {f"article_{i}": f"Source : {a.source}\nDate : {when(a.published_at)}\nTitre : {a.title}\n"
              f"Résumé : {a.summary}" for i, a in enumerate(articles[:6], 1)}
    answer = llm.ask(TASK, SCHEMA, blocks)
    if answer is None or not grounded(answer, articles):
        return base
    summary = answer["summary"].strip()
    confirmed = distinct(summary, answer["confirmed"])
    return {**base, "summary": summary, "confirmed": confirmed,
            "uncertain": distinct(" ".join([summary, *confirmed]), answer["uncertain"]), "by": "llm"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 3}


def distinct(summary: str, facts: list[str]) -> list[str]:
    """Faits qui ne répètent pas le résumé (ni un fait déjà gardé) : au plus 60 % de mots en commun."""
    kept, seen = [], [_words(summary)] if summary else []
    for fact in facts:
        words = _words(fact)
        if words and any(len(words & other) >= 0.6 * len(words) for other in seen):
            continue
        kept.append(fact)
        seen.append(words)
    return kept


def item_text(entry: dict, evolution: bool) -> str:
    head = ("Évolution : " if evolution else "") + entry["title"]
    status = "confirmé" if entry["status"] == "confirmé" else "non confirmé à ce stade"
    lines = [f"{head} ({status})."]
    if entry["summary"]:
        lines.append(entry["summary"])
    if entry["confirmed"]:
        lines.append("Faits confirmés : " + " ; ".join(entry["confirmed"]) + ".")
    if entry["uncertain"]:
        lines.append("Incertain : " + " ; ".join(entry["uncertain"]) + ".")
    lines.append(f"Sources : {entry['sources']}.")
    return " ".join(lines)


def compose(entries: list[tuple[dict, str, bool]], slot: str, now: float) -> tuple[str, str]:
    """``entries`` : (synthèse, catégorie, évolution). -> (texte complet, version courte pour la voix ou l'écrit)."""
    stamp = datetime.fromtimestamp(now).strftime("%d/%m à %H h %M")
    if not entries:
        text = f"Bulletin de {slot} ({stamp}) : peu d'actualité importante depuis le précédent bulletin."
        return text, text
    sections: dict[str, list[str]] = {}
    for entry, category, evolution in entries:
        sections.setdefault(CATEGORY_LABELS.get(category, category), []).append(item_text(entry, evolution))
    full = [f"Bulletin de {slot} ({stamp})."]
    for label, items in sections.items():
        full.append(f"{label} : " + " ".join(items))
    headlines = "; ".join(("Évolution : " if ev else "") + e["title"] for e, _, ev in entries[:4])
    short = f"Bulletin de {slot} : {len(entries)} sujet(s). {headlines}."
    return "\n".join(full), short
