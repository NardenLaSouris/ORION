"""Actions Discord de l'agent Windows (``POST /actions/discord_*``), appelées par le module DISCORD du Core.

L'agent ne décide rien : il exécute la commande explicite reçue (rejoindre tel salon, couper le micro, etc.) et rend
l'état. Permissions, registre d'actions et compréhension des demandes sont au Core. Pas de bascule (« toggle ») :
chaque réglage vocal est une valeur explicite, jamais l'inverse de l'état supposé.
"""

from __future__ import annotations

import re

from orion.tools.base import EXECUTION_FAILED, Param, Risk, Tool, ToolError
from orion.winagent.discord_rpc import ALREADY_IN_VOICE, DiscordLink, RpcError

SNOWFLAKE = re.compile(r"^\d{15,22}$")


def _snowflake(value: str) -> str:
    if not SNOWFLAKE.match(value):
        raise ToolError("invalid_parameters", "Identifiant Discord invalide.")
    return value


def discord_actions(link: DiscordLink) -> list[Tool]:
    def guarded(fn):
        def run(**kwargs):
            try:
                return fn(**kwargs)
            except RpcError as exc:
                raise ToolError(exc.code if exc.code else EXECUTION_FAILED, exc.message) from exc
        return run

    def state() -> dict:
        return link.snapshot()

    def events(since: int = 0) -> dict:
        return link.events(since)

    def channels() -> dict:
        return {"guilds": link.directory()}

    def join(channel_id: str, force: bool = False) -> dict:
        try:
            return link.select(channel_id, force)
        except RpcError as exc:
            if exc.rpc_code == ALREADY_IN_VOICE:
                raise ToolError("discord_already_in_voice", "Vous êtes déjà dans un salon vocal.") from exc
            raise

    def leave() -> dict:
        return link.select(None, True)

    def voice(mute: bool | None = None, deaf: bool | None = None) -> dict:
        changes = {k: v for k, v in (("mute", mute), ("deaf", deaf)) if v is not None}
        if not changes:
            raise ToolError("invalid_parameters", "Aucun réglage demandé.")
        link.settled(link.request("SET_VOICE_SETTINGS", changes))
        return link.snapshot()

    def link_start() -> dict:
        return link.link()

    def unlink() -> dict:
        return link.unlink()

    flag = lambda d: Param(bool, d, required=False)  # noqa: E731
    return [
        Tool("discord_state", "État de Discord Desktop (salon vocal, micro, sourdine, participants).", {},
             {"state": "état"}, Risk.SAFE, guarded(state)),
        Tool("discord_events", "Changements depuis un numéro d'événement.",
             {"since": Param(int, "dernier événement connu", required=False, minimum=0)}, {"events": "liste"},
             Risk.SAFE, guarded(events)),
        Tool("discord_channels", "Serveurs et salons vocaux.", {}, {"guilds": "liste"}, Risk.SAFE, guarded(channels)),
        Tool("discord_join", "Rejoint un salon vocal précis (identifiant).",
             {"channel_id": Param(str, "identifiant du salon", max_length=22, check=_snowflake),
              "force": flag("quitter le salon actuel pour celui-ci (demande explicite)")},
             {"state": "état"}, Risk.SAFE, guarded(join)),
        Tool("discord_leave", "Quitte le salon vocal.", {}, {"state": "état"}, Risk.SAFE, guarded(leave)),
        Tool("discord_voice", "Règle explicitement le micro (mute) et la sourdine (deaf).",
             {"mute": flag("micro coupé"), "deaf": flag("sourdine")}, {"state": "état"}, Risk.SAFE, guarded(voice)),
        Tool("discord_link", "Demande l'autorisation d'ORION dans Discord.", {}, {"status": "état"}, Risk.SAFE,
             guarded(link_start)),
        Tool("discord_unlink", "Retire l'autorisation d'ORION (jeton effacé).", {}, {"status": "état"}, Risk.SAFE,
             guarded(unlink)),
    ]
