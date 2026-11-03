"""Outil vocal de SENTINEL : « comment va le système ? » (lecture seule)."""

from __future__ import annotations

from orion.sentinel.engine import SentinelEngine
from orion.tools.base import Risk, Tool


def sentinel_tools(engine: SentinelEngine) -> list[Tool]:
    def status() -> dict:
        engine.probe()  # état à jour avant de répondre
        return {"text": engine.summary()}

    return [Tool("sentinel_status", "Dit si ORION et ses ressources (Katana, transcription, PC, Web) fonctionnent, et "
                 "les incidents en cours.", {}, {"text": "état"}, Risk.SAFE, status, say=lambda r: r["text"])]
