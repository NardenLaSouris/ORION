"""Outils vocaux de NEWS.

- « quelles sont les actualités ? » -> le dernier bulletin (composé maintenant s'il date de plus de 6 heures, sans le
  compter comme un bulletin envoyé) ;
- « le journal », « les nouvelles du jour » -> le journal quotidien (lot P1) : cinq titres numérotés, puis l'invitation
  à en savoir plus ;
- « approfondis la deuxième », « parle-moi de l'info sur… » -> la fiche complète d'une information du journal.
"""

from __future__ import annotations

from orion.news.briefing import detail
from orion.news.engine import NewsEngine
from orion.tools.base import Param, Risk, Tool, ToolError
from orion.watch.products import plain

STALE = 6 * 3600
MAX_SPOKEN = 900


def news_tools(engine: NewsEngine) -> list[Tool]:
    def bulletin() -> dict:
        latest = engine.latest_bulletin()
        if latest is None or engine._clock() - latest["at"] > STALE:  # noqa: SLF001 - même horloge que le module
            engine.bulletin(deliver=False)
            latest = engine.latest_bulletin()
        return {"text": latest["text"][:MAX_SPOKEN], "at": latest["at"]}

    def briefing() -> dict:
        journal = engine.current_journal()
        return {"text": journal["spoken"][:MAX_SPOKEN], "at": journal["at"], "count": len(journal["items"])}

    def more(numero: int | None = None, sujet: str | None = None) -> dict:
        items = engine.current_journal()["items"]
        if not items:
            raise ToolError("not_found", "Le journal ne contient aucune information pour l'instant.")
        item = None
        if numero is not None:
            item = next((i for i in items if i["number"] == numero), None)
        elif sujet:
            wanted = set(plain(sujet).split())
            scored = [(len(wanted & set(plain(i["title"] + " " + i.get("summary", "")).split())), i) for i in items]
            best = max(scored, key=lambda s: s[0])
            item = best[1] if best[0] else None
        if item is None:
            raise ToolError("not_found", f"Je ne trouve pas cette information : le journal en compte {len(items)}.")
        return {"text": detail(item)[:MAX_SPOKEN], "number": item["number"]}

    number = Param(int, "numéro de l'information dans le journal (« la deuxième » : 2)", required=False, minimum=1,
                   maximum=20)
    subject = Param(str, "sujet de l'information, avec les mots de la demande", required=False, max_length=120)
    return [Tool("news_bulletin", "Donne les actualités importantes (dernier bulletin d'ORION NEWS).", {},
                 {"text": "bulletin"}, Risk.SAFE, bulletin, say=lambda r: r["text"]),
            Tool("news_briefing", "Donne le journal du jour : les principales informations, numérotées.", {},
                 {"text": "journal"}, Risk.SAFE, briefing, say=lambda r: r["text"]),
            Tool("news_more", "Détaille une information du journal du jour, par son numéro ou son sujet.",
                 {"numero": number, "sujet": subject}, {"text": "fiche"}, Risk.SAFE, more,
                 say=lambda r: r["text"])]
