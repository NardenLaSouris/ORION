"""Module DISCORD (V3) : votre Discord Desktop piloté depuis ORION (docs/v3/discord.md).

Répartition :
- **Core** (ici) : compréhension (``intent.py``), permissions (moteur central), registre d'actions, choix du salon
  d'après son nom, phrases ; aucune dépendance à Windows ni à Discord ;
- **agent Windows V3** (``orion/winagent/discord_*.py``) : RPC local officiel de Discord, état et commandes
  explicites ; jamais d'audio ;
- **ORION Control** : vue Discord (``admin``).

Règles :
- jamais de bascule : MUTE, UNMUTE, DEAFEN, UNDEAFEN sont des valeurs explicites ;
- démute et fin de sourdine : seulement à votre demande (conversation ou ORION Control), jamais d'une initiative ;
- fin de sourdine : le micro reste coupé (``undeafen_keeps_mute``), car Discord rouvrirait sinon le micro d'avant ;
- aucun changement de salon sans demande explicite : rejoindre un salon nommé est une demande ; ORION n'en change
  jamais de lui-même ;
- chaque action : décision du moteur central et résultat inscrits au registre d'actions (avec l'identifiant de la
  demande) ; une erreur ou une indisponibilité est rendue telle quelle, jamais maquillée en succès ;
- agent hors ligne : le module reste actif, déclare son état (« agent du PC hors ligne depuis… ») et ce qui ne
  marche plus ; il retrouve l'état dès que l'agent revient (sondage ``poll_seconds``).
- noms des participants : en mémoire seulement (jamais en base ni au registre).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from orion.personality import normalize
from orion.platform.agentlink import AgentLink, AgentUnavailable
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.scheduler import Scheduler

log = logging.getLogger(__name__)

TRUSTED = ("user", "admin")
ACTIONS = ("mute", "unmute", "deafen", "undeafen", "join", "leave", "link", "unlink")
OPENING = ("unmute", "undeafen")  # rouvrent le micro ou le son : jamais d'une initiative
OFFLINE, ONLINE = "hors ligne", "en ligne"


# Erreurs de l'agent et du module : même type (code stable, message en français).
DiscordUnavailable = AgentUnavailable


@dataclass
class DiscordSettings:
    poll_seconds: float = 5.0
    directory_seconds: float = 600.0
    undeafen_keeps_mute: bool = True
    events_kept: int = 100


class DiscordEngine(BaseModule):
    name, title = "discord", "Discord"

    def __init__(self, agent: AgentLink, *, scheduler: Scheduler | None = None,
                 decide: Callable[..., Any] | None = None, ledger=None, settings: DiscordSettings | None = None,
                 clock: Callable[[], float] = time.time):
        self._agent, self._decide, self._ledger = agent, decide, ledger
        self._clock = clock
        self._scheduler = scheduler or Scheduler(clock)
        self.settings = settings or DiscordSettings()
        self._lock = threading.Lock()
        self._remote: dict | None = None  # dernier état rendu par l'agent
        self._agent_state, self._agent_since, self._agent_error = OFFLINE, clock(), "jamais joint"
        self._last_seq = 0
        self._events: deque[dict] = deque(maxlen=self.settings.events_kept)
        self._directory: tuple[float, list[dict]] | None = None

    # --- Cycle de vie ---------------------------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.add("discord:poll", self.settings.poll_seconds, self.poll)

    def stop(self) -> None:
        self._scheduler.remove("discord:poll")

    # --- État -------------------------------------------------------------------------------------------------

    def poll(self) -> dict:
        """État et événements de l'agent ; agent injoignable : état « hors ligne » déclaré, rien d'autre."""
        try:
            remote = self._agent.call("discord_state")
            if remote.get("last_event", 0) < self._last_seq:
                self._last_seq = 0  # agent redémarré : sa numérotation repart de zéro
            news = self._agent.call("discord_events", {"since": self._last_seq})
        except DiscordUnavailable as exc:
            self._went(OFFLINE, exc.message)
            return self.state()
        self._went(ONLINE, "")
        with self._lock:
            self._remote = remote
            for event in news.get("events", []):
                self._events.append(event)
            self._last_seq = max(self._last_seq, int(news.get("last", 0) or 0))
        for event in news.get("events", []):
            if event.get("kind") == "safety":
                log.warning("Discord : %s", event.get("message", ""))
                self._record("module:discord", "discord.safety_remute", True,
                             "sécurité de l'agent : micro rouvert par Discord pendant une coupure, recoupé")
        return self.state()

    def _went(self, state: str, error: str) -> None:
        with self._lock:
            if state != self._agent_state:
                self._agent_state, self._agent_since = state, self._clock()
                self._events.append({"seq": 0, "at": self._clock(), "kind": "agent", "state": state})
                log.info("Discord : agent du PC %s", state)
                if state == OFFLINE:
                    self._remote = None
            self._agent_error = error

    def state(self) -> dict:
        with self._lock:
            remote = dict(self._remote or {})
            agent = {"state": self._agent_state, "since": self._agent_since, "error": self._agent_error}
        return {"agent": agent, "discord": remote.get("discord", "inconnu"), "auth": remote.get("auth", "inconnu"),
                "configured": remote.get("configured"), "user": remote.get("user", ""),
                "voice": remote.get("voice"), "mute": remote.get("mute"), "deaf": remote.get("deaf"),
                "connection": remote.get("connection", ""), "error": remote.get("error", ""),
                "capabilities": self.capabilities(agent["state"], remote)}

    @staticmethod
    def capabilities(agent: str, remote: dict) -> dict:
        """Ce qui marche maintenant (et sinon pourquoi), pour ORION Control et les réponses."""
        if agent != ONLINE:
            why = "agent du PC hors ligne"
        elif remote.get("discord") != "connecté":
            why = "Discord fermé sur le PC" if remote.get("discord") != "ouvert" else "application ORION non configurée"
        elif remote.get("auth") != "lié":
            why = "ORION non autorisé dans Discord"
        else:
            why = ""
        names = ("state", "voice_settings", "join_leave", "participants", "events")
        return {"ready": not why, "reason": why, **{n: not why for n in names}}

    def deafened(self) -> bool | None:
        with self._lock:
            return None if self._remote is None else self._remote.get("deaf")

    def events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._events)[-limit:][::-1]

    def status(self) -> dict:
        state = self.state()
        voice = state["voice"] or {}
        return {"agent": state["agent"]["state"], "discord": state["discord"], "auth": state["auth"],
                "salon": voice.get("channel", ""), "serveur": voice.get("guild", ""), "micro coupé": state["mute"],
                "sourdine": state["deaf"]}

    # --- Actions ----------------------------------------------------------------------------------------------

    def act(self, action: str, principal: str = "user", *, decided: bool = True, salon: str = "",
            serveur: str = "", channel_id: str = "") -> dict:
        """Une action explicite. ``decided`` : la décision du moteur central est déjà prise (outil vocal, passé par
        le Core) ; sinon (ORION Control) elle est demandée ici. Rend {ok, text, state} ; jamais d'exception."""
        if action not in ACTIONS:
            return self._out(False, "Action Discord inconnue.")
        if action in OPENING and principal not in TRUSTED:
            self._record(principal, f"discord.{action}", False, "démute ou fin de sourdine : demande explicite requise")
            return self._out(False, "Je ne rouvre jamais votre micro ni votre son sans votre demande.")
        if not decided and self._decide is not None:
            verdict = self._decide(principal, "CONTROL_DEVICE", {"device": "discord"}, action=f"discord:{action}")
            if not verdict.allowed:
                return self._out(False, f"Action refusée : {verdict.reason}.")
        try:
            text = self._run(action, salon.strip(), serveur.strip(), channel_id)
        except DiscordUnavailable as exc:
            self._record(principal, f"discord.{action}", False, f"{exc.code} : {exc.message}"[:160])
            return self._out(False, self._explain(exc))
        self._record(principal, f"discord.{action}", True, "exécutée")
        return self._out(True, text)

    def _run(self, action: str, salon: str, serveur: str, channel_id: str = "") -> str:
        if action == "mute":
            self._apply(self._agent.call("discord_voice", {"mute": True}))
            return "Micro coupé sur Discord."
        if action == "unmute":
            remote = self._apply(self._agent.call("discord_voice", {"mute": False, "deaf": False}))
            return "Micro réactivé sur Discord." + (" Attention : il est toujours coupé côté Discord."
                                                     if remote.get("mute") else "")
        if action == "deafen":
            self._apply(self._agent.call("discord_voice", {"deaf": True}))
            return "Vous êtes en sourdine sur Discord : son et micro coupés."
        if action == "undeafen":
            keep = self.settings.undeafen_keeps_mute
            remote = self._apply(self._agent.call("discord_voice", {"deaf": False, **({"mute": True} if keep else {})}))
            return "Son réactivé sur Discord." + (" Votre micro reste coupé : dites « démute-moi » pour le rouvrir."
                                                  if remote.get("mute") else "")
        if action == "leave":
            before = (self._remote or {}).get("voice")
            self._apply(self._agent.call("discord_leave"))
            return f"J'ai quitté le salon {before['channel']}." if before else "Vous n'étiez dans aucun salon vocal."
        if action == "join":
            return self._join(salon, serveur, channel_id)
        if action == "link":
            self._agent.call("discord_link")
            return "Acceptez la demande d'ORION dans Discord, sur le PC."
        self._agent.call("discord_unlink")
        self._directory = None
        return "Autorisation d'ORION retirée de Discord."

    def _join(self, salon: str, serveur: str, channel_id: str = "") -> str:
        if channel_id:
            known = [(g, c) for g in self.directory() for c in g.get("channels", []) if c["id"] == channel_id]
            if not known:
                raise DiscordUnavailable("channel_not_found", "Salon inconnu : actualisez la liste.")
            guild, channel = known[0]
        elif not salon:
            raise DiscordUnavailable("channel_missing", "Quel salon vocal voulez-vous rejoindre ?")
        else:
            guild, channel = self.resolve(salon, serveur)
        current = (self._remote or {}).get("voice") or {}
        if current.get("channel_id") == channel["id"]:
            return f"Vous êtes déjà dans {channel['name']}."
        # Rejoindre un salon nommé est une demande explicite : quitter le salon actuel en fait partie (force).
        self._apply(self._agent.call("discord_join", {"channel_id": channel["id"], "force": bool(current)},
                                     timeout=20))
        where = f" sur {guild['name']}" if guild["name"] else ""
        return f"Vous avez rejoint {channel['name']}{where}."

    def directory(self, refresh: bool = False) -> list[dict]:
        cached = self._directory
        if cached and not refresh and self._clock() - cached[0] < self.settings.directory_seconds:
            return cached[1]
        guilds = self._agent.call("discord_channels", timeout=20).get("guilds", [])
        self._directory = (self._clock(), guilds)
        return guilds

    def resolve(self, salon: str, serveur: str = "") -> tuple[dict, dict]:
        """Salon vocal d'après son nom dit (accents, casse, emojis ignorés) ; serveur actuel préféré en cas de
        doublon ; sinon ORION demande lequel, jamais au hasard."""
        wanted, place = normalize(salon), normalize(serveur)
        guilds = self.directory()
        if place:
            guilds = [g for g in guilds if _matches(normalize(g["name"]), place)] or []
            if not guilds:
                raise DiscordUnavailable("guild_not_found", f"Je ne trouve pas le serveur « {serveur} ».")
        found: list[tuple[int, dict, dict]] = []
        for guild in guilds:
            for channel in guild.get("channels", []):
                name = normalize(channel["name"])
                score = 3 if name == wanted else 2 if _matches(name, wanted) else 0
                if score:
                    found.append((score, guild, channel))
        if not found:
            raise DiscordUnavailable("channel_not_found", f"Je ne trouve pas de salon vocal « {salon} ».")
        best = max(score for score, _, _ in found)
        found = [(g, c) for score, g, c in found if score == best]
        current = ((self._remote or {}).get("voice") or {}).get("guild_id")
        if len(found) > 1 and current:
            found = [(g, c) for g, c in found if g["id"] == current] or found
        if len(found) > 1:
            places = sorted({g["name"] for g, _ in found})
            raise DiscordUnavailable("channel_ambiguous", f"Plusieurs salons « {found[0][1]['name']} » : sur "
                                     + " ou sur ".join(places[:3]) + " ?")
        return found[0]

    def _apply(self, remote: dict) -> dict:
        if isinstance(remote, dict) and "discord" in remote:
            with self._lock:
                self._remote = remote
        return remote or {}

    def _record(self, principal: str, action: str, done: bool, reason: str) -> None:
        if self._ledger is None:
            return
        from orion import trace

        try:
            self._ledger.append(principal if principal in TRUSTED or ":" in principal else "module:discord",
                                "CONTROL_DEVICE", action, "allow" if done else "deny", reason,
                                trace.current() or "", {"device": "discord"})
        except Exception:  # noqa: BLE001 - le résultat est aussi dans le journal
            log.exception("Discord : registre d'actions indisponible")

    def _explain(self, exc: DiscordUnavailable) -> str:
        if exc.code == "agent_offline":
            return "Je n'ai pas accès à Discord : l'agent du PC ne répond pas."
        if exc.code == "discord_closed":
            return "Discord n'est pas ouvert sur le PC."
        if exc.code == "discord_not_configured":
            return ("Discord est ouvert, mais l'application ORION n'est pas encore configurée sur l'agent "
                    "(voir docs/v3/discord.md).")
        if exc.code == "discord_unlinked":
            return "ORION n'est pas encore autorisé dans Discord : reliez-le depuis ORION Control."
        return exc.message

    def _out(self, ok: bool, text: str) -> dict:
        return {"ok": ok, "text": text, "state": self.state()}

    # --- Phrases ----------------------------------------------------------------------------------------------

    def summary(self) -> str:
        state = self.state()
        ready = state["capabilities"]
        if not ready["ready"]:
            return {"agent du PC hors ligne": "Je n'ai pas accès à Discord : l'agent du PC ne répond pas.",
                    "Discord fermé sur le PC": "Discord n'est pas ouvert sur le PC.",
                    "application ORION non configurée": "Discord est ouvert, mais l'application ORION n'est pas "
                                                        "encore configurée sur l'agent.",
                    }.get(ready["reason"], "ORION n'est pas encore autorisé dans Discord : reliez-le depuis "
                                           "ORION Control.")
        voice = state["voice"]
        micro = "en sourdine" if state["deaf"] else "micro coupé" if state["mute"] else "micro ouvert"
        if not voice:
            return f"Vous n'êtes dans aucun salon vocal ; {micro}."
        where = f" sur {voice['guild']}" if voice.get("guild") else ""
        return f"Vous êtes dans {voice['channel']}{where}, {micro}."

    def participants(self) -> str:
        state = self.state()
        if not state["capabilities"]["ready"]:
            return self.summary()
        voice = state["voice"]
        if not voice:
            return "Vous n'êtes dans aucun salon vocal."
        me = normalize(state["user"] or "")
        others = [p["name"] for p in voice.get("participants", []) if normalize(p["name"]) != me]
        if not others:
            return f"Vous êtes seul dans {voice['channel']}."
        names = others[:8]
        listed = ", ".join(names[:-1]) + " et " + names[-1] if len(names) > 1 else names[0]
        extra = f" et {len(others) - 8} autres" if len(others) > 8 else ""
        return f"Dans {voice['channel']}, avec vous : {listed}{extra}."

    # --- ORION Control ----------------------------------------------------------------------------------------

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        if method == "GET" and not parts:
            return 200, {**self.state(), "events": self.events()}
        if method == "POST" and parts == ["refresh"]:
            return 200, self.poll()
        if method == "GET" and parts == ["channels"]:
            try:
                return 200, {"guilds": self.directory(refresh=True)}
            except DiscordUnavailable as exc:
                return 503, {"status": "error", "error": exc.code, "message": self._explain(exc)}
        if method == "POST" and len(parts) == 1 and parts[0] in ACTIONS:
            body = data if isinstance(data, dict) else {}
            salon = body.get("salon", "") if isinstance(body.get("salon", ""), str) else ""
            serveur = body.get("serveur", "") if isinstance(body.get("serveur", ""), str) else ""
            channel_id = body.get("channel_id") if isinstance(body.get("channel_id"), str) else ""
            channel_id = channel_id if channel_id.isdigit() and len(channel_id) <= 22 else ""
            return 200, self.act(parts[0], "admin", decided=False, salon=salon[:100], serveur=serveur[:100],
                                 channel_id=channel_id)
        return NOT_FOUND


def _matches(name: str, wanted: str) -> bool:
    if not name or not wanted:
        return False
    return f" {wanted} " in f" {name} " or name.startswith(wanted)
