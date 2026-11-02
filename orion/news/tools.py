"""Outil vocal de NEWS : « quelles sont les actualités ? » -> le dernier bulletin (composé maintenant s'il date de plus
de 6 heures, sans le compter comme un bulletin envoyé)."""

from __future__ import annotations

from orion.news.engine import NewsEngine
from orion.tools.base import Risk, Tool

STALE = 6 * 3600
MAX_SPOKEN = 900


def news_tools(engine: NewsEngine) -> list[Tool]:
    def bulletin() -> dict:
        latest = engine.latest_bulletin()
        if latest is None or engine._clock() - latest["at"] > STALE:  # noqa: SLF001 - même horloge que le module
            engine.bulletin(deliver=False)
            latest = engine.latest_bulletin()
        return {"text": latest["text"][:MAX_SPOKEN], "at": latest["at"]}

    return [Tool("news_bulletin", "Donne les actualités importantes (dernier bulletin d'ORION NEWS).", {},
                 {"text": "bulletin"}, Risk.SAFE, bulletin, say=lambda r: r["text"])]
