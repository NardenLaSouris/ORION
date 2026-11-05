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

    def alias(serveur: str, surnom: str) -> dict:
        result = engine.set_alias(serveur, surnom)
        if not result["ok"]:
            raise ToolError(NEEDS_DETAILS if result.get("code") in QUESTIONS else EXECUTION_FAILED, result["text"])
        return {"text": result["text"]}

    def forget(surnom: str) -> dict:
        result = engine.forget_alias(surnom)
        if not result["ok"]:
            raise ToolError(EXECUTION_FAILED, result["text"])
        return {"text": result["text"]}

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
        Tool("discord_alias", "Discord : donne un surnom à un serveur (« surnomme le serveur X en Y »).",
             {"serveur": Param(str, "nom du serveur tel qu'il a été dit", max_length=100, evidence=_said),
              "surnom": Param(str, "surnom dit", max_length=40, evidence=_said)},
             out, Risk.SAFE, alias, say=say, capability="WRITE_DATA"),
        Tool("discord_forget_alias", "Discord : oublie un surnom de serveur.",
             {"surnom": Param(str, "surnom dit", max_length=40, evidence=_said)},
             out, Risk.SAFE, forget, say=say, capability="WRITE_DATA"),
        Tool("discord_aliases", "Discord : liste les surnoms de serveurs.", {}, out, Risk.SAFE,
             lambda: {"text": engine.alias_summary()}, say=say, capability="READ"),
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
