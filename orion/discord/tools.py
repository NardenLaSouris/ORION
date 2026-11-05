"""Outils vocaux du module DISCORD. Une commande par réglage (jamais de bascule) ; décision du moteur central prise
par le Core avant l'appel (capacité ``CONTROL_DEVICE`` pour agir, ``READ`` pour lire)."""

from __future__ import annotations

from orion.discord.engine import QUESTIONS, DiscordEngine
from orion.personality import normalize
from orion.tools.base import EXECUTION_FAILED, NEEDS_DETAILS, Param, Risk, Tool, ToolError


def _said(value, text: str) -> bool:
    """Nom de salon ou de serveur proposé par le LLM : il doit figurer dans la demande."""
    return not value or normalize(str(value)) in normalize(text)


def discord_tools(engine: DiscordEngine) -> list[Tool]:
    def act(action: str):
        def run(**kwargs) -> dict:
            result = engine.act(action, "user", decided=True, **kwargs)
            if not result["ok"]:
                # « Quel salon ? », « sur A ou sur B ? » : une question, que votre réponse suivante complète.
                raise ToolError(NEEDS_DETAILS if result.get("code") in QUESTIONS else EXECUTION_FAILED,
                                result["text"])
            return {"text": result["text"]}
        return run

    def status() -> dict:
        engine.poll()
        return {"text": engine.summary()}

    def participants() -> dict:
        engine.poll()
        return {"text": engine.participants()}

    say = lambda r: r["text"]  # noqa: E731
    out = {"text": "réponse"}
    control = "CONTROL_DEVICE"
    return [
        Tool("discord_status", "Discord : salon vocal actuel, serveur, micro coupé ou non, sourdine.", {}, out,
             Risk.SAFE, status, say=say, capability="READ"),
        Tool("discord_participants", "Discord : qui est dans le salon vocal.", {}, out, Risk.SAFE, participants,
             say=say, capability="READ"),
        Tool("discord_join", "Discord : rejoint le salon vocal nommé (change de salon si besoin).",
             {"salon": Param(str, "nom du salon vocal tel qu'il a été dit", required=False, max_length=100,
                             evidence=_said),
              "serveur": Param(str, "nom du serveur, seulement s'il est dit", required=False, max_length=100,
                               evidence=_said)},
             out, Risk.SAFE, act("join"), say=say, capability=control),
        Tool("discord_leave", "Discord : quitte le salon vocal.", {}, out, Risk.SAFE, act("leave"), say=say,
             capability=control),
        Tool("discord_mute", "Discord : coupe mon micro (mute).", {}, out, Risk.SAFE, act("mute"), say=say,
             capability=control),
        Tool("discord_unmute", "Discord : réactive mon micro (démute), seulement si la demande le dit.", {}, out,
             Risk.SAFE, act("unmute"), say=say, capability=control),
        Tool("discord_deafen", "Discord : me met en sourdine (deafen : son et micro coupés).", {}, out, Risk.SAFE,
             act("deafen"), say=say, capability=control),
        Tool("discord_undeafen", "Discord : enlève la sourdine (undeafen) ; le micro reste coupé.", {}, out,
             Risk.SAFE, act("undeafen"), say=say, capability=control),
    ]
