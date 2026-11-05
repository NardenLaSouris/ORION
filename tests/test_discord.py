"""DISCORD : client RPC de l'agent (faux Discord qui parle le même protocole de trames), actions de l'agent,
module du Core (noms de salons, permissions, registre, agent hors ligne, redémarrages), compréhension (mute et
deafen jamais confondus, démute seulement explicite), construction et raccordements."""

from __future__ import annotations

import json
import struct
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.discord import DiscordEngine, DiscordSettings, DiscordUnavailable  # noqa: E402
from orion.discord.intent import discord_plan  # noqa: E402
from orion.tools.base import ToolError  # noqa: E402
from orion.winagent.discord_actions import discord_actions  # noqa: E402
from orion.winagent.discord_rpc import (CLOSE, FRAME, HANDSHAKE, DiscordLink, DiscordSettings as LinkSettings,  # noqa: E402
                                        RpcError, TokenBox, encode)

ME, ALICE, BOB = "100000000000000001", "100000000000000002", "100000000000000003"
AMIS, TRAVAIL = "200000000000000001", "200000000000000002"
GENERAL, JEUX, TEXTE, GENERAL2 = ("300000000000000001", "300000000000000002", "300000000000000003",
                                  "300000000000000004")


# --- Faux Discord Desktop -----------------------------------------------------------------------------------------


class FakeDiscord:
    """Discord Desktop simulé : mêmes trames (opcode, longueur, JSON), mêmes commandes et événements."""

    def __init__(self):
        self.guilds = {AMIS: "Amis 🎮", TRAVAIL: "Travail"}
        self.channels = {AMIS: [(GENERAL, "Général", 2), (JEUX, "Jeux", 2), (TEXTE, "discussion", 0)],
                         TRAVAIL: [(GENERAL2, "général", 2)]}
        self.selected: str | None = None
        self.people = {GENERAL: [(ALICE, "Alice")], JEUX: [], GENERAL2: []}
        self.mute = self.deaf = False
        self.tokens = {"tok-1"}
        self.open = True
        self.commands: list[dict] = []
        self.subscriptions: set[tuple[str, str]] = set()
        self._out = b""
        self._lock = threading.Lock()

    # Transport vu par l'agent.
    def connect(self):
        if not self.open:
            raise OSError("discord-ipc absent")
        self._out = b""
        return self

    def write(self, data: bytes) -> None:
        if not self.open:
            raise OSError("fermé")
        op, size = struct.unpack("<II", data[:8])
        self._handle(op, json.loads(data[8:8 + size]))

    def available(self) -> int:
        if not self.open:
            raise OSError("Discord a fermé la connexion")
        with self._lock:
            return len(self._out)

    def read(self, size: int) -> bytes:
        deadline = time.time() + 2
        while True:
            if not self.open:
                raise OSError("fermé")
            with self._lock:
                if len(self._out) >= size:
                    chunk, self._out = self._out[:size], self._out[size:]
                    return chunk
            if time.time() > deadline:
                raise OSError("rien à lire")
            time.sleep(0.005)

    def close(self) -> None:
        pass

    # Simulation.
    def _push(self, op: int, payload: dict) -> None:
        with self._lock:
            self._out += encode(op, payload)

    def dispatch(self, evt: str, data: dict) -> None:
        self._push(FRAME, {"cmd": "DISPATCH", "evt": evt, "data": data})

    def _reply(self, frame: dict, data: dict | None = None, error: tuple[int, str] | None = None) -> None:
        payload = {"cmd": frame["cmd"], "nonce": frame["nonce"], "data": data or {}}
        if error:
            payload.update(evt="ERROR", data={"code": error[0], "message": error[1]})
        self._push(FRAME, payload)

    def _channel(self, cid: str | None) -> dict | None:
        if cid is None:
            return None
        guild = next(g for g, chans in self.channels.items() if any(c[0] == cid for c in chans))
        name = next(c[1] for c in self.channels[guild] if c[0] == cid)
        people = [(ME, "Moi")] + self.people.get(cid, [])
        return {"id": cid, "guild_id": guild, "name": name, "type": 2,
                "voice_states": [{"user": {"id": u, "username": n}, "nick": n,
                                  "voice_state": {"self_mute": False, "self_deaf": False}} for u, n in people]}

    def _handle(self, op: int, frame: dict) -> None:
        if op == HANDSHAKE:
            if frame.get("client_id") != "123456":
                self._push(CLOSE, {"code": 4000, "message": "Invalid Client ID"})
            else:
                self._push(FRAME, {"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}})
            return
        self.commands.append(frame)
        cmd, args = frame["cmd"], frame.get("args") or {}
        if cmd == "AUTHORIZE":
            return self._reply(frame, {"code": "code-ok"})
        if cmd == "AUTHENTICATE":
            if args.get("access_token") not in self.tokens:
                return self._reply(frame, error=(4009, "Invalid OAuth2 access token"))
            return self._reply(frame, {"user": {"id": ME, "username": "moi", "global_name": "Moi"}})
        if cmd == "SUBSCRIBE":
            self.subscriptions.add((frame["evt"], args.get("channel_id", "")))
            return self._reply(frame, {"evt": frame["evt"]})
        if cmd == "UNSUBSCRIBE":
            self.subscriptions.discard((frame["evt"], args.get("channel_id", "")))
            return self._reply(frame, {"evt": frame["evt"]})
        if cmd == "GET_VOICE_SETTINGS":
            return self._reply(frame, {"mute": self.mute or self.deaf, "deaf": self.deaf})
        if cmd == "SET_VOICE_SETTINGS":
            if "deaf" in args:
                self.deaf = bool(args["deaf"])
            if "mute" in args:
                self.mute = bool(args["mute"])
            settings = {"mute": self.mute or self.deaf, "deaf": self.deaf}
            self._reply(frame, settings)
            return self.dispatch("VOICE_SETTINGS_UPDATE", settings)
        if cmd == "GET_SELECTED_VOICE_CHANNEL":
            return self._reply(frame, self._channel(self.selected))
        if cmd == "SELECT_VOICE_CHANNEL":
            target = args.get("channel_id")
            if target and self.selected and not args.get("force"):
                return self._reply(frame, error=(5003, "Already in voice"))
            self.selected = target
            self._reply(frame, self._channel(target))
            guild = self._channel(target)["guild_id"] if target else None
            return self.dispatch("VOICE_CHANNEL_SELECT", {"channel_id": target, "guild_id": guild})
        if cmd == "GET_GUILDS":
            return self._reply(frame, {"guilds": [{"id": g, "name": n} for g, n in self.guilds.items()]})
        if cmd == "GET_GUILD":
            return self._reply(frame, {"id": args["guild_id"], "name": self.guilds[args["guild_id"]]})
        if cmd == "GET_CHANNELS":
            return self._reply(frame, {"channels": [{"id": c, "name": n, "type": t}
                                                    for c, n, t in self.channels[args["guild_id"]]]})
        self._reply(frame, error=(4000, "unknown"))

    def someone_joins(self, cid: str, uid: str, name: str) -> None:
        self.people[cid].append((uid, name))
        self.dispatch("VOICE_STATE_CREATE", {"user": {"id": uid, "username": name}, "nick": name,
                                             "voice_state": {}})

    def someone_leaves(self, cid: str, uid: str, name: str) -> None:
        self.people[cid] = [p for p in self.people[cid] if p[0] != uid]
        self.dispatch("VOICE_STATE_DELETE", {"user": {"id": uid, "username": name}, "nick": name})


PLAIN = (lambda b: b[::-1], lambda b: b[::-1])  # « chiffrement » de test : le fichier n'est pas du JSON lisible


def wait(predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def link_for(fake, tmp_path, token="tok-1", exchanges=None):
    box = TokenBox(tmp_path / "discord_token.bin", PLAIN)
    if token:
        box.save({"access_token": token, "refresh_token": "r", "expires_at": time.time() + 7 * 86400})

    def exchange(client_id, secret, redirect, *, code="", refresh=""):
        (exchanges if exchanges is not None else []).append((code, refresh))
        fake.tokens.add("tok-2")
        return {"access_token": "tok-2", "refresh_token": "r2", "expires_at": time.time() + 7 * 86400}

    link = DiscordLink(LinkSettings("123456", "secret-de-test", "http://localhost", tmp_path, request_timeout=2,
                                    retry_max=0.2),
                       connect=fake.connect, exchange=exchange, tokens=box)
    link.start()
    return link


@pytest.fixture
def fake():
    return FakeDiscord()


# --- Agent : RPC local ----------------------------------------------------------------------------------------------


def test_authorization_flow_stores_an_encrypted_token_and_never_a_plain_one(fake, tmp_path):
    exchanges = []
    link = link_for(fake, tmp_path, token="", exchanges=exchanges)
    try:
        assert wait(lambda: link.snapshot()["discord"] == "connecté")
        assert link.snapshot()["auth"] == "non lié"
        with pytest.raises(RpcError):
            link.request("GET_VOICE_SETTINGS")  # rien avant l'autorisation
        assert link.link()["status"] == "pending"
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        assert exchanges == [("code-ok", "")]
        authorize = next(c for c in fake.commands if c["cmd"] == "AUTHORIZE")
        assert authorize["args"]["scopes"] == ["rpc", "rpc.voice.read", "rpc.voice.write"]
        raw = (tmp_path / "discord_token.bin").read_bytes()
        assert b"tok-2" not in raw and link.tokens.load()["access_token"] == "tok-2"
        assert {e for e, _ in fake.subscriptions} >= {"VOICE_CHANNEL_SELECT", "VOICE_SETTINGS_UPDATE"}
    finally:
        link.stop()


def test_explicit_voice_settings_join_change_leave_and_participants(fake, tmp_path):
    link = link_for(fake, tmp_path)
    actions = {t.name: t for t in discord_actions(link)}
    try:
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        assert actions["discord_voice"].execute({"mute": True})["mute"] is True
        assert fake.mute and not fake.deaf
        state = actions["discord_voice"].execute({"deaf": True})
        assert state["deaf"] is True and state["mute"] is True
        actions["discord_voice"].execute({"deaf": False, "mute": False})
        assert not fake.mute and not fake.deaf
        with pytest.raises(ToolError):
            actions["discord_voice"].execute({})  # aucun réglage : jamais de bascule implicite
        state = actions["discord_join"].execute({"channel_id": GENERAL})
        assert state["voice"]["channel"] == "Général" and state["voice"]["guild"] == "Amis 🎮"
        assert {p["name"] for p in state["voice"]["participants"]} == {"Moi", "Alice"}
        with pytest.raises(ToolError) as err:
            actions["discord_join"].execute({"channel_id": JEUX})  # déjà en vocal, sans demande de changement
        assert err.value.code == "discord_already_in_voice" and fake.selected == GENERAL
        assert actions["discord_join"].execute({"channel_id": JEUX, "force": True})["voice"]["channel"] == "Jeux"
        assert ("VOICE_STATE_CREATE", JEUX) in fake.subscriptions
        assert ("VOICE_STATE_CREATE", GENERAL) not in fake.subscriptions  # abonnements de l'ancien salon retirés
        fake.someone_joins(JEUX, BOB, "Bob")
        assert wait(lambda: any(p["name"] == "Bob" for p in link.snapshot()["voice"]["participants"]))
        fake.someone_leaves(JEUX, BOB, "Bob")
        assert wait(lambda: all(p["name"] != "Bob" for p in link.snapshot()["voice"]["participants"]))
        kinds = [(e["kind"], e.get("action")) for e in link.events(0)["events"]]
        assert ("channel", "rejoint") in kinds and ("channel", "changé") in kinds
        assert ("participant", "arrivé") in kinds and ("participant", "parti") in kinds
        assert actions["discord_leave"].execute({})["voice"] is None and fake.selected is None
        from orion.tools import ToolRegistry, parse_request

        registry = ToolRegistry()
        for tool in actions.values():
            registry.register(tool)
        with pytest.raises(ToolError):  # validation de l'agent (comme POST /actions/discord_join)
            parse_request({"tool": "discord_join", "parameters": {"channel_id": "../../x"}}, registry)
        guilds = actions["discord_channels"].execute({})["guilds"]
        assert [c["name"] for c in guilds[0]["channels"]] == ["Général", "Jeux"]  # salons vocaux seulement
    finally:
        link.stop()


def test_discord_closing_and_coming_back_reconnects_by_itself(fake, tmp_path):
    link = link_for(fake, tmp_path)
    try:
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        fake.open = False
        assert wait(lambda: link.snapshot()["discord"] == "fermé")
        with pytest.raises(RpcError):
            link.request("GET_VOICE_SETTINGS")
        fake.open = True
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        assert [e["state"] for e in link.events(0)["events"] if e["kind"] == "discord"][-2:] == ["fermé", "connecté"]
    finally:
        link.stop()


def test_a_mic_reopened_by_discord_while_the_agent_was_away_is_muted_again_never_the_reverse(fake, tmp_path):
    link = link_for(fake, tmp_path)
    try:
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        link.settled(link.request("SET_VOICE_SETTINGS", {"mute": True}))
    finally:
        link.stop()
    fake.mute = False  # Discord annule les réglages de l'application déconnectée
    again = link_for(fake, tmp_path)
    try:
        assert wait(lambda: again.snapshot()["auth"] == "lié")
        assert wait(lambda: fake.mute is True) and again.snapshot()["mute"] is True
        assert any(e["kind"] == "safety" for e in again.events(0)["events"])
    finally:
        again.stop()
    fake.mute = True  # l'inverse (micro coupé à la reconnexion, ouvert avant) : jamais rouvert
    (tmp_path / "discord_last_state.json").write_text(json.dumps({"mute": False}), encoding="utf-8")
    third = link_for(fake, tmp_path)
    try:
        assert wait(lambda: third.snapshot()["auth"] == "lié")
        time.sleep(0.2)
        assert fake.mute is True and not any(e["kind"] == "safety" for e in third.events(0)["events"])
    finally:
        third.stop()


def test_an_expired_token_asks_for_a_new_link(fake, tmp_path):
    link = link_for(fake, tmp_path, token="tok-révoqué")
    try:
        assert wait(lambda: link.snapshot()["discord"] == "connecté")
        assert wait(lambda: link.tokens.load() is None)  # jeton refusé : effacé, liaison à refaire
        assert link.snapshot()["auth"] == "non lié"
    finally:
        link.stop()


def test_unconfigured_agent_never_talks_to_discord(fake, tmp_path):
    link = DiscordLink(LinkSettings("", "", "", tmp_path, retry_max=0.2), connect=fake.connect,
                       tokens=TokenBox(tmp_path / "t.bin", PLAIN))
    link.start()
    try:
        assert wait(lambda: link.snapshot()["auth"] == "non configuré")
        assert link.snapshot()["discord"] == "ouvert" and fake.commands == []
        with pytest.raises(RpcError):
            link.link()
        with pytest.raises(RpcError) as err:
            link.request("GET_VOICE_SETTINGS")
        assert err.value.code == "discord_not_configured"  # jamais « Discord fermé » quand il est ouvert
    finally:
        link.stop()


# --- Agent : configuration et serveur -------------------------------------------------------------------------------


def test_v3_agent_config_has_its_own_token_and_keeps_the_secret_out_of_the_toml(tmp_path, monkeypatch):
    from orion.winagent.config import load_agent_config

    for name in ("ORION_AGENT_TOKEN", "JARVIS_AGENT_TOKEN", "ORION_V3_AGENT_TOKEN", "DISCORD_CLIENT_SECRET",
                 "DISCORD_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("ORION_AGENT_TOKEN=" + "p" * 40 + "\nORION_V3_AGENT_TOKEN=" + "v" * 40 +
                   "\nDISCORD_CLIENT_ID=123456\nDISCORD_CLIENT_SECRET=s3cret\n", encoding="utf-8")
    cfg = tmp_path / "agent.toml"
    cfg.write_text('[agent]\nport = 8875\nallowed_ips = ["192.168.1.91"]\naudio = false\npc_tools = false\n'
                   'token_name = "ORION_V3_AGENT_TOKEN"\n[face]\nopen_on_connect = false\n'
                   '[discord]\nenabled = true\nredirect_uri = "http://localhost"\n', encoding="utf-8")
    config = load_agent_config(cfg, env)
    assert config.token == "v" * 40 and not config.audio and not config.pc_tools
    assert config.discord["client_id"] == "123456" and config.discord["client_secret"] == "s3cret"
    cfg.write_text('[agent]\nallowed_ips = ["192.168.1.91"]\n[discord]\nenabled = true\nclient_secret = "x"\n',
                   encoding="utf-8")
    with pytest.raises(ValueError):
        load_agent_config(cfg, env)  # le secret n'a pas sa place dans le TOML


def test_agent_without_audio_refuses_audio_routes(tmp_path):
    import urllib.error
    import urllib.request

    from orion.tools import ToolRegistry
    from orion.winagent.config import AgentConfig
    from orion.winagent.server import AgentServer

    server = AgentServer(AgentConfig("127.0.0.1", 0, frozenset({"127.0.0.1"}), "t" * 40, audio=False,
                                     pc_tools=False), ToolRegistry(), audio=object(), music=object())
    server.start()
    try:
        host, port = server.address
        request = urllib.request.Request(f"http://{host}:{port}/audio/output?rate=16000", data=b"\0\0",
                                         headers={"Authorization": "Bearer " + "t" * 40})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(request, timeout=3)
        assert err.value.code == 404
    finally:
        server._httpd.shutdown()  # noqa: SLF001
        server._httpd.server_close()  # noqa: SLF001


# --- Core : module DISCORD ------------------------------------------------------------------------------------------


class FakeAgent:
    """Agent V3 simulé côté Core : rend ce que rendrait l'agent, ou tombe."""

    def __init__(self):
        self.online = True
        self.calls: list[tuple[str, dict]] = []
        self.remote = {"discord": "connecté", "auth": "lié", "user": "Moi", "voice": None, "mute": False,
                       "deaf": False, "connection": "", "error": "", "configured": True, "last_event": 0}
        self.queue: list[dict] = []
        self.seq = 0
        self.guilds = [{"id": AMIS, "name": "Amis 🎮", "channels": [{"id": GENERAL, "name": "🔊 Général"},
                                                                    {"id": JEUX, "name": "Jeux vidéo"}]},
                       {"id": TRAVAIL, "name": "Travail", "channels": [{"id": GENERAL2, "name": "général"}]}]

    def call(self, action, parameters=None, timeout=None):
        if not self.online:
            raise DiscordUnavailable("agent_offline", "L'agent du PC ne répond pas.")
        parameters = parameters or {}
        self.calls.append((action, parameters))
        if action == "discord_state":
            return {**self.remote, "last_event": self.seq}
        if action == "discord_events":
            out = [e for e in self.queue if e["seq"] > parameters.get("since", 0)]
            return {"events": out, "last": self.seq}
        if action == "discord_channels":
            return {"guilds": self.guilds}
        if action == "discord_voice":
            self.remote.update({k: v for k, v in parameters.items()})
            self.remote["mute"] = self.remote["mute"] or self.remote["deaf"]
        if action == "discord_join":
            guild = next((g for g in self.guilds if any(c["id"] == parameters["channel_id"] for c in g["channels"])),
                         {"id": "0", "name": "", "channels": [{"id": parameters["channel_id"], "name": "?"}]})
            chan = next(c for c in guild["channels"] if c["id"] == parameters["channel_id"])  # hors liste : par id
            self.remote["voice"] = {"channel_id": chan["id"], "channel": chan["name"], "guild_id": guild["id"],
                                    "guild": guild["name"], "participants": [{"id": ME, "name": "Moi"},
                                                                             {"id": ALICE, "name": "Alice"}]}
        if action == "discord_leave":
            self.remote["voice"] = None
        return dict(self.remote)

    def push(self, **event):
        self.seq += 1
        self.queue.append({"seq": self.seq, "at": 0, **event})


class Ledger:
    def __init__(self):
        self.rows = []

    def append(self, principal, capability, action, decision, reason="", request_id="", detail=None):
        self.rows.append((principal, capability, action, decision, reason))


def engine_with(agent=None, decide=None):
    agent = agent or FakeAgent()
    ledger = Ledger()
    engine = DiscordEngine(agent, decide=decide, ledger=ledger, settings=DiscordSettings())
    engine.poll()
    return engine, agent, ledger


def test_join_by_spoken_name_accents_emojis_and_server_preference():
    engine, agent, ledger = engine_with()
    assert engine.act("join", salon="Général")["ok"] is False  # « général » existe sur deux serveurs
    assert "Amis" in engine.act("join", salon="Général")["text"] and agent.remote["voice"] is None
    result = engine.act("join", salon="general", serveur="amis")
    assert result["ok"] and result["text"] == "Vous avez rejoint Général sur Amis."  # dit sans emojis
    assert agent.calls[-1] == ("discord_join", {"channel_id": GENERAL, "force": False})
    assert engine.act("join", salon="jeux")["ok"]  # nom partiel ; changement de salon demandé : force
    assert agent.calls[-1] == ("discord_join", {"channel_id": JEUX, "force": True})
    assert engine.act("join", salon="général")["text"].startswith("Vous avez rejoint Général")  # serveur actuel
    assert engine.act("join", salon="Général")["text"] == "Vous êtes déjà dans Général."
    assert engine.act("join", salon="cuisine")["text"] == "Je ne trouve pas de salon vocal « cuisine »."
    # déjà dans un salon, aucun nommé (« connecte-moi en vocal ») : rien ne change, c'est dit
    assert engine.act("join")["text"] == "Vous êtes déjà dans Général sur Amis."
    assert ("user", "CONTROL_DEVICE", "discord.join", "allow", "exécutée") in ledger.rows


def test_stylised_discord_names_are_recognised_when_spoken_and_said_without_ornaments():
    """Noms relevés sur le vrai Discord le 4 novembre : lettres mathématiques grasses, emojis, séparateurs."""
    agent = FakeAgent()
    agent.guilds = [{"id": "780814027176935424", "name": "La Taverne d'Odin", "channels": [
        {"id": "1149744683925372928", "name": "🕐┃𝐀𝐭𝐭𝐞𝐧𝐭𝐞"},
        {"id": "1149744681538814033", "name": "🍻┃𝐒𝐤𝐚𝐥𝐢"},
        {"id": "1149744677650710578", "name": "🗿┃𝐄𝐧𝐭𝐫𝐞𝐭𝐢𝐞𝐧 #𝟏"}]},
        {"id": "812111919943123004", "name": "The Hunter Fishing planet", "channels": [
            {"id": "1390061034047541288", "name": "🔊𝗖𝗵𝗶𝗹𝗹🔊"}]}]
    engine, agent, _ = engine_with(agent)
    result = engine.act("join", salon="attente", serveur="taverne")
    assert result["ok"] and result["text"] == "Vous avez rejoint Attente sur La Taverne d'Odin."
    assert agent.calls[-1][1]["channel_id"] == "1149744683925372928"
    assert engine.act("join", salon="Skali")["text"] == "Vous avez rejoint Skali sur La Taverne d'Odin."
    assert engine.act("join", salon="entretien 1")["ok"]
    # « rejoins le salon Attente sur la Taverne » : l'analyse de la phrase garde tout dans le nom du salon
    assert engine.act("join", salon="attente sur la taverne")["text"] == "Vous avez rejoint Attente sur La Taverne d'Odin."
    assert engine.act("join", salon="attente sur la cuisine")["text"] == ("Je ne trouve pas de salon vocal "
                                                                         "« attente sur la cuisine ».")
    assert engine.act("join", salon="chill")["text"] == "Vous avez rejoint Chill sur The Hunter Fishing planet."
    assert "Chill" in engine.summary() and "🔊" not in engine.summary()


def test_mute_and_deafen_are_explicit_and_undeafen_keeps_the_mic_closed():
    engine, agent, _ = engine_with()
    assert engine.act("mute")["text"] == "Micro coupé sur Discord."
    assert agent.calls[-1] == ("discord_voice", {"mute": True})
    engine.act("deafen")
    assert agent.calls[-1] == ("discord_voice", {"deaf": True})
    text = engine.act("undeafen")["text"]
    assert agent.calls[-1] == ("discord_voice", {"deaf": False, "mute": True})
    assert "micro reste coupé" in text and agent.remote["mute"] is True
    assert engine.act("unmute")["text"] == "Micro réactivé sur Discord."
    assert agent.remote["mute"] is False


def test_unmute_never_comes_from_an_initiative_and_control_goes_through_the_central_engine():
    decisions = []

    class Verdict:
        def __init__(self, allowed, reason=""):
            self.allowed, self.reason = allowed, reason

    def decide(principal, capability, context, action=""):
        decisions.append((principal, capability, action))
        return Verdict(principal == "admin", "autonomie suspendue")

    engine, agent, ledger = engine_with(decide=decide)
    for action in ("unmute", "undeafen"):
        result = engine.act(action, "module:routines")
        assert not result["ok"] and "sans votre demande" in result["text"]
    assert not any(c[0] == "discord_voice" for c in agent.calls)
    assert ledger.rows[-1][3] == "deny"
    status, body = engine.admin("POST", ["unmute"], {}, {})
    assert status == 200 and body["ok"] and decisions[-1] == ("admin", "CONTROL_DEVICE", "discord:unmute")
    assert engine.act("mute", "module:discord", decided=False)["text"] == "Action refusée : autonomie suspendue."


def test_agent_offline_is_declared_with_capabilities_and_recovers():
    engine, agent, ledger = engine_with()
    agent.online = False
    state = engine.poll()
    assert state["agent"]["state"] == "hors ligne" and state["capabilities"]["ready"] is False
    assert state["capabilities"]["reason"] == "agent du PC hors ligne"
    result = engine.act("mute")
    assert not result["ok"] and result["text"] == "Je n'ai pas accès à Discord : l'agent du PC ne répond pas."
    assert ledger.rows[-1][2:4] == ("discord.mute", "deny")  # l'échec est inscrit, jamais maquillé
    assert engine.summary() == "Je n'ai pas accès à Discord : l'agent du PC ne répond pas."
    agent.online = True
    assert engine.poll()["capabilities"]["ready"] is True
    assert [e["state"] for e in engine.events() if e["kind"] == "agent"][:2] == ["en ligne", "hors ligne"]


def test_unlinked_or_closed_discord_is_explained_and_agent_restart_resets_events():
    engine, agent, _ = engine_with()
    agent.push(kind="channel", action="rejoint", channel="Général", guild="Amis")
    agent.push(kind="safety", message="micro rouvert par Discord pendant la coupure de l'agent : recoupé")
    engine.poll()
    assert engine._last_seq == 2  # noqa: SLF001
    agent.seq, agent.queue = 0, []  # agent redémarré
    agent.push(kind="participant", action="arrivé", name="Bob")
    engine.poll()
    assert engine.events()[0]["name"] == "Bob"
    assert any(r[2] == "discord.safety_remute" for r in engine._ledger.rows)  # noqa: SLF001
    agent.remote.update(auth="non lié")
    engine.poll()
    assert "reliez-le depuis ORION Control" in engine.summary()
    agent.remote.update(discord="fermé")
    engine.poll()
    assert engine.summary() == "Discord n'est pas ouvert sur le PC."


def test_status_and_participants_sentences():
    engine, agent, _ = engine_with()
    assert engine.summary() == "Vous n'êtes dans aucun salon vocal ; micro ouvert."
    engine.act("join", salon="jeux")
    engine.act("mute")
    assert engine.summary() == "Vous êtes dans Jeux vidéo sur Amis, micro coupé."
    assert engine.participants() == "Dans Jeux vidéo, avec vous : Alice."
    engine.act("leave")
    assert engine.participants() == "Vous n'êtes dans aucun salon vocal."


def test_participant_names_are_never_written_to_the_ledger():
    engine, agent, ledger = engine_with()
    engine.act("join", salon="jeux")
    engine.act("leave")
    assert "Alice" not in json.dumps(ledger.rows)


# --- Compréhension ---------------------------------------------------------------------------------------------------


EXISTS = lambda name: name.startswith("discord_")  # noqa: E731


@pytest.mark.parametrize("text, tool, params", [
    ("ORION, rejoins le vocal Général.", "discord_join", {"salon": "general"}),
    ("rejoins le salon vocal Jeux vidéo sur le serveur Amis", "discord_join", {"salon": "jeux video",
                                                                              "serveur": "amis"}),
    ("change de salon", "discord_join", {}),
    ("mets-moi dans le vocal Jeux", "discord_join", {"salon": "jeux"}),
    ("ORION, mute-moi.", "discord_mute", {}),
    ("coupe mon micro", "discord_mute", {}),
    ("ORION, démute-moi.", "discord_unmute", {}),
    ("dé-mute moi", "discord_unmute", {}),
    ("réactive mon micro", "discord_unmute", {}),
    ("ORION, mets-moi en sourdine.", "discord_deafen", {}),
    ("deafen", "discord_deafen", {}),
    ("enlève-moi la sourdine", "discord_undeafen", {}),
    ("ORION, qui est dans le vocal ?", "discord_participants", {}),
    ("est-ce que je suis mute ?", "discord_status", {}),
    ("suis-je en sourdine ?", "discord_status", {}),
    ("dans quel salon je suis sur Discord ?", "discord_status", {}),
    ("ORION, quitte Discord.", "discord_leave", {}),
    ("déconnecte-moi du vocal", "discord_leave", {}),
])
def test_phrases(text, tool, params):
    assert discord_plan(text, EXISTS) == (tool, params)


def test_mute_and_deafen_are_never_confused_and_questions_never_act():
    for text in ("mute-moi", "coupe mon micro"):
        assert discord_plan(text, EXISTS)[0] == "discord_mute"
    for text in ("mets-moi en sourdine", "deafen"):
        assert discord_plan(text, EXISTS)[0] == "discord_deafen"
    assert discord_plan("est-ce que je suis démute ?", EXISTS)[0] == "discord_status"
    assert discord_plan("ne me démute pas", EXISTS) is None
    assert discord_plan("mets en sourdine", EXISTS) is None  # sans « moi » ni Discord : le son du PC
    assert discord_plan("passe sur la chaîne 3", EXISTS) is None
    assert discord_plan("mets-moi de la musique", EXISTS) is None
    assert discord_plan("quel temps fait-il ?", EXISTS) is None
    assert discord_plan("mute-moi", lambda name: False) is None  # module absent


def test_reactivate_the_sound_goes_to_discord_only_when_deafened_there():
    assert discord_plan("réactive le son", EXISTS, lambda: True) == ("discord_undeafen", {})
    assert discord_plan("réactive le son", EXISTS, lambda: False) is None
    assert discord_plan("réactive le son", EXISTS, lambda: None) is None
    assert discord_plan("réactive le son sur Discord", EXISTS, lambda: False) == ("discord_undeafen", {})


def test_quick_planner_and_router_send_discord_requests_to_discord(tmp_path):
    from orion.tools.quick import quick_plan
    from orion.tools.registry import ToolRegistry
    from orion.discord.tools import discord_tools

    engine, agent, _ = engine_with()
    registry = ToolRegistry()
    for tool in discord_tools(engine):
        registry.register(tool)
    registry.context["discord.deafened"] = engine.deafened
    assert quick_plan("mute-moi", registry)["tool"] == "discord_mute"
    assert quick_plan("rejoins le vocal Général", registry)["parameters"] == {"salon": "general"}
    assert quick_plan("réactive le son", registry) is None
    engine.act("deafen")
    assert quick_plan("réactive le son", registry)["tool"] == "discord_undeafen"
    from orion.tools.planner import READ_ONLY_TOOLS

    assert "discord_status".startswith(READ_ONLY_TOOLS) and "discord_participants".startswith(READ_ONLY_TOOLS)
    assert not "discord_unmute".startswith(READ_ONLY_TOOLS)


def test_voice_tools_declare_capabilities_and_report_failures():
    from orion.discord.tools import discord_tools

    engine, agent, _ = engine_with()
    tools = {t.name: t for t in discord_tools(engine)}
    assert tools["discord_status"].capability == "READ" and tools["discord_participants"].capability == "READ"
    assert all(tools[n].capability == "CONTROL_DEVICE" for n in ("discord_join", "discord_leave", "discord_mute",
                                                                  "discord_unmute", "discord_deafen",
                                                                  "discord_undeafen"))
    assert tools["discord_mute"].run()["text"] == "Micro coupé sur Discord."
    agent.online = False
    with pytest.raises(ToolError):
        tools["discord_mute"].run()
    assert tools["discord_status"].run()["text"].startswith("Je n'ai pas accès à Discord")


# --- Construction ---------------------------------------------------------------------------------------------------


def test_build_needs_an_agent_url_and_its_own_token(tmp_path, monkeypatch):
    from orion.config import load_config
    from orion.platform.build import build_v3, classify_job
    from orion.platform.foundation import Foundation
    from orion.platform.store import Store

    assert classify_job("discord:poll") == ("module:discord", "READ")
    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, discord=replace(cfg.discord, enabled=True, agent_url="http://127.0.0.1:9"),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    monkeypatch.delenv("ORION_V3_AGENT_TOKEN", raising=False)
    monkeypatch.setattr("orion.factory.ENV_FILE", tmp_path / "absent.env")
    alone = build_v3(cfg)
    try:
        assert alone.registry.get("discord") is None  # sans jeton propre : non démarré
    finally:
        alone.stop()
    monkeypatch.setenv("ORION_V3_AGENT_TOKEN", "v" * 40)
    cfg = replace(cfg, modules=replace(cfg.modules, database=tmp_path / "v3b.db"))
    modules = build_v3(cfg, foundation=Foundation(Store()))
    try:
        assert modules.registry.get("discord") is not None
        assert {"discord_mute", "discord_unmute", "discord_join"} <= {t.name for t in modules.tools}
        assert "discord.deafened" in modules.contexts
    finally:
        modules.stop()


def test_an_unknown_client_id_is_reported_clearly_like_the_real_discord_does(fake, tmp_path):
    """Vrai Discord (essai du 4 novembre) : poignée de main avec un identifiant inconnu -> trame CLOSE, code 4000."""
    link = DiscordLink(LinkSettings("999", "secret-de-test", "", tmp_path, retry_max=0.2), connect=fake.connect,
                       tokens=TokenBox(tmp_path / "t.bin", PLAIN))
    link.start()
    try:
        assert wait(lambda: link.snapshot()["auth"] == "identifiant refusé")
        assert "Invalid Client ID" in link.snapshot()["error"] and link.snapshot()["discord"] == "ouvert"
        with pytest.raises(RpcError) as err:
            link.request("GET_VOICE_SETTINGS")
        assert err.value.code == "discord_bad_client_id"
    finally:
        link.stop()
    engine = DiscordEngine(FakeAgent(), settings=DiscordSettings())
    caps = engine.capabilities("en ligne", {"discord": "ouvert", "auth": "identifiant refusé"})
    assert caps["reason"] == "identifiant de l'application refusé par Discord"


def test_connect_me_without_a_channel_uses_your_usual_channel_or_says_you_are_there():
    """« Connecte-moi en vocal » (4 novembre 2026) : votre salon habituel, même sur un serveur que Discord ne liste
    pas (« Et le Zombie avec ou sans ?? », connu parce qu'ORION vous y a vu) ; déjà dans un salon : rien ne change."""
    agent = FakeAgent()
    zombie = {"channel_id": "1400000000000000001", "channel": "Général", "guild_id": "1400000000000000000",
              "guild": "Et le Zombie avec ou sans ??", "participants": []}
    agent.remote["voice"] = zombie
    engine = DiscordEngine(agent, ledger=Ledger(), settings=DiscordSettings(
        default_salon="Général", default_serveur="Et le Zombie avec ou sans"))
    engine.poll()
    assert engine.act("join")["text"] == "Vous êtes déjà dans Général sur Et le Zombie avec ou sans ??."
    engine.act("leave")
    result = engine.act("join")
    assert result["ok"] and agent.calls[-1] == ("discord_join", {"channel_id": zombie["channel_id"], "force": False})
    bare, bare_agent, _ = engine_with()
    assert bare.act("join")["text"] == "Quel salon vocal voulez-vous rejoindre ?"  # aucun salon habituel réglé
    assert bare_agent.calls[-1][0] != "discord_join"

def test_channels_where_orion_saw_you_are_remembered_across_restarts(tmp_path):
    agent = FakeAgent()
    agent.remote["voice"] = {"channel_id": "1400000000000000001", "channel": "Général", "guild_id": "1400000000000000000",
                             "guild": "Et le Zombie avec ou sans ??", "participants": [{"id": ME, "name": "Moi"}]}
    settings = DiscordSettings(default_salon="Général", default_serveur="Et le Zombie avec ou sans")
    DiscordEngine(agent, settings=settings, seen_path=tmp_path / "discord_seen.json").poll()
    saved = (tmp_path / "discord_seen.json").read_text(encoding="utf-8")
    assert "1400000000000000001" in saved and "Moi" not in saved  # identifiants et noms de salon, jamais les participants
    agent.remote["voice"] = None
    again = DiscordEngine(agent, settings=settings, seen_path=tmp_path / "discord_seen.json")
    again.poll()
    assert again.act("join")["ok"] and agent.calls[-1][1]["channel_id"] == "1400000000000000001"

def test_connect_me_in_voice_goes_through_the_tool_core_end_to_end():
    """« Connecte moi en vocal » par message (4 novembre 2026, 13 h 55) : la phrase était comprise, mais le salon vide
    était refusé à la vérification des paramètres (« Je n'ai pas bien compris ») ; l'essai direct du module, lui,
    passait. Ici : phrase -> analyseur -> vérification -> outil -> salon habituel."""
    from orion.discord.tools import discord_tools
    from orion.tools.quick import quick_plan
    from orion.tools.registry import ToolRegistry
    from orion.tools.request import parse_request

    agent = FakeAgent()
    engine = DiscordEngine(agent, ledger=Ledger(), settings=DiscordSettings(default_salon="Jeux vidéo",
                                                                            default_serveur="Amis"))
    engine.poll()
    registry = ToolRegistry()
    for tool in discord_tools(engine):
        registry.register(tool)
    for text in ("Connecte moi en vocal", "connecte-moi en vocal sur discord"):
        agent.remote["voice"] = None
        engine.poll()  # état relu : plus dans un salon
        call = quick_plan(text, registry)
        request = parse_request(call, registry)
        result = registry.get(request.tool).execute(dict(request.parameters))
        assert result["text"] == "Vous avez rejoint Jeux vidéo sur Amis.", text

# --- 5 novembre 2026 : messages Linphone (« Le Pool Club », « Et le Zombie », « connecte-moi en vocal ») ---------------

POOL, POOL_A, POOL_B = "500000000000000000", "500000000000000001", "500000000000000002"


def test_agent_lists_every_server_and_reads_channels_beyond_the_first_25_on_demand(fake, tmp_path):
    """Seuls les 25 premiers serveurs étaient lus : « Le Pool Club », plus loin, était « introuvable »."""
    for i in range(30):
        gid = f"6000000000000000{i:02d}"
        fake.guilds[gid] = f"Serveur {i}"
        fake.channels[gid] = [(f"7000000000000000{i:02d}", "Salon", 2)]
    fake.guilds[POOL] = "Le Pool Club"
    fake.channels[POOL] = [(POOL_A, "On vomit à 5h", 2), (POOL_B, "Chill", 2)]
    link = link_for(fake, tmp_path)
    actions = {t.name: t for t in discord_actions(link)}
    try:
        assert wait(lambda: link.snapshot()["auth"] == "lié")
        guilds = actions["discord_channels"].execute({})["guilds"]
        pool = next(g for g in guilds if g["id"] == POOL)
        assert len(guilds) == 33 and "channels" not in pool  # listé, salons pas encore lus
        asked = sum(c["cmd"] == "GET_CHANNELS" for c in fake.commands)
        guilds = actions["discord_channels"].execute({"guild_id": POOL})["guilds"]
        assert [c["name"] for c in next(g for g in guilds if g["id"] == POOL)["channels"]] == ["On vomit à 5h", "Chill"]
        assert sum(c["cmd"] == "GET_CHANNELS" for c in fake.commands) == asked + 1  # ce serveur seulement
        everything = actions["discord_channels"].execute({"tous": True})["guilds"]
        assert all("channels" in g for g in everything)
    finally:
        link.stop()


class LazyAgent(FakeAgent):
    """Agent qui ne rend les salons du « Pool Club » que sur demande (serveur au-delà des 25 premiers)."""

    def __init__(self, pool_channels=(("On vomit à 5h", POOL_A), ("Chill", POOL_B))):
        super().__init__()
        self.pool = {"id": POOL, "name": "Le Pool Club 🎱",
                     "channels": [{"id": cid, "name": name} for name, cid in pool_channels]}
        self.guilds = self.guilds + [self.pool]

    def call(self, action, parameters=None, timeout=None):
        if action == "discord_channels":
            self.calls.append((action, parameters or {}))
            loaded = (parameters or {}).get("guild_id") == POOL or (parameters or {}).get("tous")
            return {"guilds": [g if g["id"] != POOL or loaded else {"id": POOL, "name": g["name"]}
                               for g in self.guilds]}
        return super().call(action, parameters, timeout)


def test_a_server_beyond_the_first_25_is_found_by_its_name_and_its_channels_fetched():
    agent = LazyAgent()
    engine, _, _ = engine_with(agent)
    result = engine.act("join", salon="on vomit à 5h", serveur="pool club")
    assert result["ok"] and agent.calls[-1] == ("discord_join", {"channel_id": POOL_A, "force": False})
    assert ("discord_channels", {"guild_id": POOL}) in agent.calls
    engine.act("leave")
    assert engine.act("join", salon="chill")["ok"]  # sans serveur : salons déjà lus, gardés en cache


def test_a_named_server_without_a_channel_joins_its_only_channel_or_asks_which():
    single, agent, _ = engine_with(LazyAgent(pool_channels=(("On vomit à 5h", POOL_A),)))
    assert single.act("join", serveur="Le Pool Club")["text"] == "Vous avez rejoint On vomit à 5h sur Le Pool Club."
    several, _, _ = engine_with(LazyAgent())
    asked = several.act("join", serveur="pool club")
    assert asked["ok"] is False and asked["code"] == "channel_missing"
    assert asked["text"] == "Quel salon sur Le Pool Club : On vomit à 5h ou Chill ?"


def test_vocal_is_never_taken_for_a_channel_name():
    """« Connecte-moi en vocal » proposé par le LLM avec salon=« vocal » : « Vocal Animation » était choisi."""
    agent = FakeAgent()
    agent.guilds[0]["channels"].append({"id": "300000000000000009", "name": "Vocal Animation"})
    engine, _, _ = engine_with(agent)
    asked = engine.act("join", salon="vocal")
    assert asked["text"] == "Quel salon vocal voulez-vous rejoindre ?" and asked["code"] == "channel_missing"


@pytest.mark.parametrize("answer, previous, expected", [
    ("Général sur « Et le Zombie avec ou sans ?? »", {"salon": "vocal"},
     {"salon": "general", "serveur": "et le zombie avec ou sans"}),
    ("sur GTA France", {"salon": "vocal animation"}, {"salon": "vocal animation", "serveur": "gta france"}),
    ("GTA France", {"salon": "vocal animation"}, {"salon": "vocal animation", "serveur": "gta france"}),
    ("On vomit à 5h", {"serveur": "pool club"}, {"salon": "on vomit a 5h", "serveur": "pool club"}),
    ("Jeux", {}, {"salon": "jeux"}),
    ("On vomit à 5h dans le serveur Le Pool Club", {}, {"salon": "on vomit a 5h", "serveur": "pool club"}),
])
def test_answers_to_a_channel_question_complete_the_request(answer, previous, expected):
    from orion.discord.intent import discord_answer

    assert discord_answer(answer, previous) == expected


def test_server_only_request_is_understood_without_an_empty_channel():
    exists = lambda name: True  # noqa: E731
    assert discord_plan("Connecte moi en vocal sur le serveur « Le Pool Club »", exists) == \
        ("discord_join", {"serveur": "pool club"})


def test_a_channel_question_becomes_a_follow_up_for_the_tool_core():
    from orion.discord.tools import discord_tools
    from orion.tools.base import NEEDS_DETAILS

    engine, _, _ = engine_with(LazyAgent())
    join = next(t for t in discord_tools(engine) if t.name == "discord_join")
    with pytest.raises(ToolError) as err:
        join.execute({"serveur": "pool club"})
    assert err.value.code == NEEDS_DETAILS
    with pytest.raises(ToolError) as err:
        join.execute({"salon": "cuisine"})
    assert err.value.code == "execution_failed"  # introuvable : pas une question
