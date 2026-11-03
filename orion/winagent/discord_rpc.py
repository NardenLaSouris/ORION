"""Client RPC local de Discord (IPC officiel, docs : discord.com/developers/docs/topics/rpc), côté agent Windows.

ORION ne se fait jamais passer pour Discord ni pour vous :
- **IPC officiel seulement** : le tube nommé ``\\\\?\\pipe\\discord-ipc-N`` que Discord Desktop ouvre lui-même
  pour les applications locales. Ni self-bot, ni jeton de compte extrait, ni API non documentée, ni lecture d'écran ;
- **votre application Discord** (Developer Portal, ``client_id``) : vous l'autorisez une fois dans Discord (fenêtre
  « Autoriser »), et l'agent échange le code contre un jeton OAuth2 (portées ``rpc``, ``rpc.voice.read``,
  ``rpc.voice.write``). Le secret de l'application reste dans le ``.env`` de l'agent V3 ; le jeton, chiffré par
  Windows (DPAPI, lié à votre session), dans son dossier de données. Rien de cela ne part vers le Core ;
- **aucun audio** : ni flux vocal, ni micro. Seuls des états (salon, participants, micro coupé, sourdine).

Un seul fil parle à Discord (un tube Windows synchrone sérialise lectures et écritures) : il se connecte, s'authentifie,
s'abonne aux événements vocaux et exécute les demandes des requêtes HTTP mises en file. Discord fermé, redémarré ou
mis à jour : reconnexion automatique, avec délai croissant.

Limite connue de Discord (documentée) : les réglages vocaux posés par une application (micro coupé, sourdine) sont
annulés quand elle se déconnecte, et réappliqués quand elle revient. Pour qu'une coupure de l'agent ne rouvre jamais
votre micro en silence, le dernier état observé est gardé sur disque : si, à la reconnexion, Discord montre un micro
ouvert alors qu'il était coupé, l'agent le recoupe aussitôt et le signale (jamais l'inverse).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

HANDSHAKE, FRAME, CLOSE, PING, PONG = 0, 1, 2, 3, 4
SCOPES = ("rpc", "rpc.voice.read", "rpc.voice.write")
TOKEN_URL = "https://discord.com/api/oauth2/token"
VOICE_TYPES = (2, 13)  # salon vocal de serveur, scène
MAX_FRAME = 1 << 20
EVENTS_KEPT = 200
GLOBAL_EVENTS = ("VOICE_CHANNEL_SELECT", "VOICE_SETTINGS_UPDATE", "VOICE_CONNECTION_STATUS")
CHANNEL_EVENTS = ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE")
# Codes d'erreur RPC utiles (docs Discord).
ALREADY_IN_VOICE, INVALID_TOKEN, NOT_AUTHENTICATED = 5003, 4009, 4006


class RpcError(Exception):
    """Échec prévu : un code court (pour le Core) et un message en français."""

    def __init__(self, code: str, message: str, rpc_code: int | None = None):
        super().__init__(message)
        self.code, self.message, self.rpc_code = code, message, rpc_code


# --- Transport -------------------------------------------------------------------------------------------------


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def available(self) -> int: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class WindowsPipe:
    """Tube nommé ouvert par Discord, en accès synchrone (ctypes) ; ``available`` sans bloquer (PeekNamedPipe)."""

    def __init__(self, handle, kernel32):
        self._h, self._k = handle, kernel32

    @classmethod
    def open(cls, index: int) -> "WindowsPipe":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        handle = k.CreateFileW(rf"\\?\pipe\discord-ipc-{index}", 0xC0000000, 0, None, 3, 0, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), f"discord-ipc-{index} absent")
        return cls(handle, k)

    def write(self, data: bytes) -> None:
        import ctypes
        from ctypes import wintypes

        done = wintypes.DWORD()
        if not self._k.WriteFile(self._h, data, len(data), ctypes.byref(done), None) or done.value != len(data):
            raise OSError(ctypes.get_last_error(), "écriture vers Discord impossible")

    def available(self) -> int:
        import ctypes
        from ctypes import wintypes

        count = wintypes.DWORD()
        if not self._k.PeekNamedPipe(self._h, None, 0, None, ctypes.byref(count), None):
            raise OSError(ctypes.get_last_error(), "Discord a fermé la connexion")
        return count.value

    def read(self, size: int) -> bytes:
        import ctypes
        from ctypes import wintypes

        out = b""
        while len(out) < size:
            buf = ctypes.create_string_buffer(size - len(out))
            got = wintypes.DWORD()
            if not self._k.ReadFile(self._h, buf, size - len(out), ctypes.byref(got), None) or not got.value:
                raise OSError(ctypes.get_last_error(), "lecture depuis Discord impossible")
            out += buf.raw[:got.value]
        return out

    def close(self) -> None:
        if self._h is not None:
            self._k.CloseHandle(self._h)
            self._h = None


def open_discord_pipe() -> Transport:
    """Premier tube ``discord-ipc-0`` à ``-9`` ouvert ; OSError si Discord Desktop n'est pas lancé."""
    if sys.platform != "win32":
        raise OSError("Discord Desktop : tube nommé Windows seulement")
    last: OSError | None = None
    for index in range(10):
        try:
            return WindowsPipe.open(index)
        except OSError as exc:
            last = exc
    raise last or OSError("Discord absent")


def encode(op: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return struct.pack("<II", op, len(body)) + body


# --- Jeton OAuth2 -------------------------------------------------------------------------------------------------


class TokenBox:
    """Jeton OAuth2 sur disque, chiffré par DPAPI (session Windows) ; ``codec`` injectable pour les tests."""

    def __init__(self, path: Path, codec: tuple[Callable[[bytes], bytes], Callable[[bytes], bytes]] | None = None):
        self.path = Path(path)
        self._seal, self._open = codec or (_dpapi(True), _dpapi(False))

    def load(self) -> dict | None:
        try:
            data = json.loads(self._open(self.path.read_bytes()))
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001 - jeton illisible (autre session, fichier abîmé) : il faudra relier
            log.warning("Discord : jeton illisible, liaison à refaire")
            return None
        return data if isinstance(data, dict) and data.get("access_token") else None

    def save(self, token: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._seal(json.dumps(token).encode()))
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _dpapi(seal: bool) -> Callable[[bytes], bytes]:
    def run(data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                             ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        fn = crypt.CryptProtectData if seal else crypt.CryptUnprotectData
        if not fn(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "DPAPI")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    return run


def oauth_exchange(client_id: str, client_secret: str, redirect_uri: str, *, code: str = "",
                   refresh: str = "", timeout: float = 10.0) -> dict:
    """Code d'autorisation (ou jeton de renouvellement) -> jeton OAuth2, sur l'API officielle de Discord."""
    form = {"client_id": client_id, "client_secret": client_secret}
    if refresh:
        form.update(grant_type="refresh_token", refresh_token=refresh)
    else:
        form.update(grant_type="authorization_code", code=code)
        if redirect_uri:
            form["redirect_uri"] = redirect_uri
    request = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "ORION-V3-Agent"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RpcError("oauth_refused", f"Discord a refusé l'échange du jeton (HTTP {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise RpcError("oauth_unreachable", "L'API de Discord est injoignable.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RpcError("oauth_refused", "Réponse de Discord sans jeton.")
    data["expires_at"] = time.time() + float(data.get("expires_in", 0) or 0)
    return data


# --- Lien avec Discord ----------------------------------------------------------------------------------------------


@dataclass
class DiscordSettings:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    data_dir: Path = Path(".")
    request_timeout: float = 8.0
    authorize_timeout: float = 120.0
    retry_max: float = 30.0


class _Call:
    __slots__ = ("command", "args", "done", "result", "error", "timeout")

    def __init__(self, command: str, args: dict, timeout: float):
        self.command, self.args, self.timeout = command, args, timeout
        self.done, self.result, self.error = threading.Event(), None, None


class DiscordLink:
    """État vocal de Discord Desktop et commandes explicites, via un fil unique."""

    def __init__(self, settings: DiscordSettings, *, connect: Callable[[], Transport] = open_discord_pipe,
                 exchange: Callable[..., dict] = oauth_exchange, tokens: TokenBox | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._connect, self._exchange, self._clock, self._sleep = connect, exchange, clock, sleep
        self.tokens = tokens or TokenBox(Path(settings.data_dir) / "discord_token.bin")
        self._memory = Path(settings.data_dir) / "discord_last_state.json"
        self._memory_lock = threading.Lock()
        self._pipe: Transport | None = None
        self._calls: queue.Queue[_Call] = queue.Queue()
        self._pending_link = threading.Event()
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self._seq = 0
        self._guilds: dict[str, str] = {}
        self._directory: tuple[float, list[dict]] | None = None
        self._subscribed_channel: str | None = None
        self._backlog: deque[tuple[int, dict]] = deque(maxlen=500)
        self.state: dict[str, Any] = self._empty_state()

    @staticmethod
    def _empty_state() -> dict:
        return {"discord": "fermé", "auth": "non lié", "user": "", "voice": None, "mute": None, "deaf": None,
                "connection": "", "error": "", "since": time.time()}

    # --- Cycle de vie ---------------------------------------------------------------------------------------

    def start(self) -> None:
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="discord-rpc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._drop("arrêt de l'agent")

    def configured(self) -> bool:
        return bool(self.settings.client_id and self.settings.client_secret)

    # --- Interface pour les requêtes HTTP (autres fils) ----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            state = json.loads(json.dumps(self.state))
        state.update(configured=self.configured(), last_event=self._seq)
        return state

    def events(self, since: int) -> dict:
        with self._lock:
            return {"events": [e for e in self._events if e["seq"] > since], "last": self._seq}

    def request(self, command, args: dict | None = None, timeout: float | None = None) -> Any:
        """Commande exécutée par le fil Discord ; RpcError si Discord est fermé, non lié, ou refuse."""
        if self.state["discord"] != "connecté":
            raise RpcError("discord_closed", "Discord n'est pas ouvert sur le PC.")
        if self.state["auth"] != "lié":
            raise RpcError("discord_unlinked", "ORION n'est pas encore autorisé dans Discord.")
        call = _Call(command, args or {}, timeout or self.settings.request_timeout)
        self._calls.put(call)
        if not call.done.wait(call.timeout + 2):
            raise RpcError("discord_timeout", "Discord ne répond pas.")
        if call.error is not None:
            raise call.error
        return call.result

    def select(self, channel_id: str | None, force: bool = False) -> dict:
        """Rejoint (ou quitte, ``None``) un salon et met l'état à jour avant de répondre (fil Discord)."""
        def run():
            args = {"channel_id": channel_id, "force": force, **({"timeout": 10} if channel_id else {})}
            self._channel(self._command("SELECT_VOICE_CHANNEL", args, 15) or None)
            return self.snapshot()

        return self.request(run, timeout=20)  # type: ignore[arg-type]

    def directory(self, max_age: float = 600.0) -> list[dict]:
        """Serveurs et salons vocaux visibles par l'application (mis en cache ``max_age`` secondes)."""
        cached = self._directory
        if cached is not None and self._clock() - cached[0] < max_age:
            return cached[1]
        guilds = self.request("GET_GUILDS").get("guilds", [])
        out = []
        for guild in guilds[:25]:
            channels = self.request("GET_CHANNELS", {"guild_id": guild["id"]}).get("channels", [])
            out.append({"id": guild["id"], "name": guild.get("name", ""),
                        "channels": [{"id": c["id"], "name": c.get("name", ""), "type": c.get("type")}
                                     for c in channels if c.get("type") in VOICE_TYPES]})
            self._guilds[guild["id"]] = guild.get("name", "")
        self._directory = (self._clock(), out)
        return out

    def link(self) -> dict:
        """Demande d'autorisation : Discord affiche « Autoriser ORION ? » ; le fil s'en charge dès qu'il est libre."""
        if not self.configured():
            raise RpcError("discord_not_configured", "Application Discord non configurée sur l'agent "
                           "(DISCORD_CLIENT_ID et DISCORD_CLIENT_SECRET).")
        if self.state["discord"] != "connecté":
            raise RpcError("discord_closed", "Discord n'est pas ouvert sur le PC.")
        self._pending_link.set()
        return {"status": "pending", "message": "Acceptez la demande d'ORION dans Discord."}

    def unlink(self) -> dict:
        self.tokens.clear()
        self._drop("liaison retirée")
        return {"status": "ok"}

    # --- Fil Discord ------------------------------------------------------------------------------------------

    def _loop(self) -> None:
        delay = 1.0
        while not self._stopping.is_set():
            try:
                self._session()
                delay = 1.0
            except Exception as exc:  # noqa: BLE001 - quoi qu'il arrive, le fil se reconnecte
                if not isinstance(exc, (OSError, RpcError, ValueError, struct.error)):
                    log.exception("Discord : erreur inattendue")
                was_open = self.state["discord"] == "connecté"
                self._drop(str(exc)[:160] if was_open else "")
                if was_open:
                    log.info("Discord : connexion perdue (%s)", exc)
                delay = min(self.settings.retry_max, delay * 2)
            self._fail_pending("discord_closed", "Discord n'est pas ouvert sur le PC.")
            self._stopping.wait(delay)

    def _session(self) -> None:
        self._pipe = self._connect()
        if not self.configured():
            # Discord est ouvert, mais aucune application ORION n'est déclarée : rien à lui demander.
            self._pipe.close()
            self._pipe = None
            self._set(discord="ouvert", auth="non configuré", error="")
            self._stopping.wait(self.settings.retry_max)
            return
        self._send(HANDSHAKE, {"v": 1, "client_id": self.settings.client_id})
        op, ready = self._recv()
        if op == CLOSE or ready.get("evt") != "READY":
            raise RpcError("discord_refused", f"Discord a refusé la connexion ({ready.get('message', op)})")
        self._set(discord="connecté", error="")
        self._event("discord", {"state": "connecté"})
        log.info("Discord : connecté au client local")
        self._authenticate()
        while not self._stopping.is_set():
            if self._pending_link.is_set():
                self._pending_link.clear()
                self._authorize()
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                call = None
            if call is not None:
                try:
                    call.result = call.command() if callable(call.command) else                         self._command(call.command, call.args, call.timeout)
                except RpcError as exc:
                    call.error = exc
                except (OSError, ValueError, struct.error):
                    call.error = RpcError("discord_closed", "Discord a fermé la connexion.")
                    raise
                except Exception:  # noqa: BLE001 - une réponse inattendue ne tue jamais le fil Discord
                    log.exception("Discord : réponse inattendue pour %s", getattr(call.command, "__name__",
                                                                                  call.command))
                    call.error = RpcError("discord_error", "Réponse inattendue de Discord.")
                finally:
                    call.done.set()
                continue
            if self._backlog:
                self._dispatch(*self._backlog.popleft())
            elif self._pipe.available():
                self._dispatch(*self._recv())
            else:
                self._sleep(0.05)

    def _authenticate(self) -> None:
        token = self.tokens.load()
        if token is None:
            self._set(auth="non lié")
            return
        if token.get("refresh_token") and float(token.get("expires_at", 0)) - self._clock() < 86400:
            try:
                token = self._exchange(self.settings.client_id, self.settings.client_secret,
                                       self.settings.redirect_uri, refresh=token["refresh_token"])
                self.tokens.save(token)
            except RpcError as exc:
                log.warning("Discord : renouvellement du jeton impossible (%s)", exc.message)
        try:
            data = self._command("AUTHENTICATE", {"access_token": token["access_token"]}, 10)
        except RpcError as exc:
            if exc.rpc_code in (INVALID_TOKEN, 4004):
                self.tokens.clear()
            self._set(auth="non lié", error=exc.message)
            return
        user = data.get("user") or {}
        self._set(auth="lié", user=user.get("global_name") or user.get("username", ""))
        self._event("auth", {"state": "lié"})
        for evt in GLOBAL_EVENTS:
            self._command("SUBSCRIBE", {}, evt=evt)
        self._voice_settings(self._command("GET_VOICE_SETTINGS", {}), initial=True)
        self._channel(self._command("GET_SELECTED_VOICE_CHANNEL", {}))

    def _authorize(self) -> None:
        self._set(auth="en attente d'autorisation dans Discord")
        try:
            data = self._command("AUTHORIZE", {"client_id": self.settings.client_id, "scopes": list(SCOPES)},
                                 self.settings.authorize_timeout)
            token = self._exchange(self.settings.client_id, self.settings.client_secret, self.settings.redirect_uri,
                                   code=data.get("code", ""))
        except RpcError as exc:
            self._set(auth="non lié", error=exc.message)
            self._event("auth", {"state": "refusé", "message": exc.message})
            return
        self.tokens.save(token)
        self._authenticate()

    # --- Protocole ------------------------------------------------------------------------------------------

    def _send(self, op: int, payload: dict) -> None:
        self._pipe.write(encode(op, payload))

    def _recv(self) -> tuple[int, dict]:
        op, size = struct.unpack("<II", self._pipe.read(8))
        if size > MAX_FRAME:
            raise ValueError("trame Discord trop grande")
        payload = json.loads(self._pipe.read(size) or b"{}")
        if op == PING:
            self._send(PONG, payload)
            return op, {}
        if op == CLOSE:
            raise RpcError("discord_closed", f"Discord a fermé la connexion ({payload.get('message', '')})")
        return op, payload

    def _command(self, command: str, args: dict, timeout: float | None = None, evt: str | None = None) -> Any:
        nonce = str(uuid.uuid4())
        frame = {"cmd": command, "args": args, "nonce": nonce}
        if evt:
            frame["evt"] = evt
        self._send(FRAME, frame)
        deadline = self._clock() + (timeout or self.settings.request_timeout)
        while True:
            if self._clock() > deadline:
                raise RpcError("discord_timeout", "Discord ne répond pas.")
            if not self._pipe.available():
                self._sleep(0.02)
                continue
            op, payload = self._recv()
            if payload.get("nonce") != nonce:
                # Événement arrivé pendant l'attente : traité après, jamais imbriqué dans cette commande.
                if payload.get("cmd") == "DISPATCH":
                    self._backlog.append((op, payload))
                continue
            if payload.get("evt") == "ERROR":
                data = payload.get("data") or {}
                raise RpcError("discord_error", _french(data.get("code"), data.get("message", "")),
                               data.get("code"))
            return payload.get("data") or {}

    def _dispatch(self, op: int, payload: dict) -> None:
        evt, data = payload.get("evt"), payload.get("data") or {}
        if op != FRAME or payload.get("cmd") != "DISPATCH":
            return
        if evt == "VOICE_SETTINGS_UPDATE":
            self._voice_settings(data)
        elif evt == "VOICE_CHANNEL_SELECT":
            if data.get("channel_id"):
                self._channel(self._command("GET_SELECTED_VOICE_CHANNEL", {}))
            else:
                self._channel(None)
        elif evt == "VOICE_CONNECTION_STATUS":
            self._set(connection=str(data.get("state", ""))[:40])
        elif evt in CHANNEL_EVENTS:
            self._participant(evt, data)

    def settled(self, data: dict) -> None:
        """Réglages rendus par SET_VOICE_SETTINGS : état et dernier état gardé à jour tout de suite."""
        if isinstance(data, dict) and "mute" in data:
            self._voice_settings(data)

    # --- État -------------------------------------------------------------------------------------------------

    def _voice_settings(self, data: dict, initial: bool = False) -> None:
        mute, deaf = bool(data.get("mute")), bool(data.get("deaf"))
        before = (self.state["mute"], self.state["deaf"])
        self._set(mute=mute, deaf=deaf)
        if initial:
            remembered = self._remembered()
            if remembered.get("mute") is True and not mute:
                # Discord a annulé la coupure du micro pendant l'absence de l'agent : jamais un micro rouvert en silence.
                log.warning("Discord : micro rouvert pendant la coupure de l'agent ; recoupé")
                settled = self._command("SET_VOICE_SETTINGS", {"mute": True})
                self._set(mute=bool(settled.get("mute", True)))
                self._event("safety", {"message": "micro rouvert par Discord pendant la coupure de l'agent : recoupé"})
        elif before != (mute, deaf):
            self._event("voice_settings", {"mute": mute, "deaf": deaf})
        self._remember()

    def _channel(self, channel: dict | None) -> None:
        old = self.state["voice"]
        if self._subscribed_channel and (channel is None or channel.get("id") != self._subscribed_channel):
            for evt in CHANNEL_EVENTS:
                try:
                    self._command("UNSUBSCRIBE", {"channel_id": self._subscribed_channel}, evt=evt)
                except RpcError:
                    pass
            self._subscribed_channel = None
        if not channel:
            self._set(voice=None)
            if old:
                self._event("channel", {"action": "quitté", "channel": old["channel"], "guild": old["guild"]})
            return
        guild_id = channel.get("guild_id") or ""
        guild = self._guilds.get(guild_id, "")
        if guild_id and not guild:
            try:
                guild = self._command("GET_GUILD", {"guild_id": guild_id}).get("name", "")
                self._guilds[guild_id] = guild
            except RpcError:
                guild = ""
        voice = {"channel_id": channel.get("id", ""), "channel": channel.get("name", ""), "guild_id": guild_id,
                 "guild": guild, "participants": [_person(v) for v in channel.get("voice_states") or []]}
        self._set(voice=voice)
        if channel.get("id") != self._subscribed_channel:
            for evt in CHANNEL_EVENTS:
                self._command("SUBSCRIBE", {"channel_id": channel["id"]}, evt=evt)
            self._subscribed_channel = channel.get("id")
        if not old or old["channel_id"] != voice["channel_id"]:
            self._event("channel", {"action": "rejoint" if not old else "changé", "channel": voice["channel"],
                                    "guild": guild, "from": (old or {}).get("channel", "")})

    def _participant(self, evt: str, data: dict) -> None:
        voice = self.state["voice"]
        if not voice:
            return
        person = _person(data)
        people = [p for p in voice["participants"] if p["id"] != person["id"]]
        if evt != "VOICE_STATE_DELETE":
            people.append(person)
        known = any(p["id"] == person["id"] for p in voice["participants"])
        self._set(voice={**voice, "participants": people})
        if evt == "VOICE_STATE_CREATE" and not known:
            self._event("participant", {"action": "arrivé", "name": person["name"]})
        elif evt == "VOICE_STATE_DELETE":
            self._event("participant", {"action": "parti", "name": person["name"]})

    def _set(self, **changes) -> None:
        with self._lock:
            self.state.update(changes)
            self.state["since"] = self._clock()

    def _event(self, kind: str, data: dict) -> None:
        with self._lock:
            self._seq += 1
            self._events.append({"seq": self._seq, "at": self._clock(), "kind": kind, **data})

    def _drop(self, reason: str) -> None:
        if self._pipe is not None:
            try:
                self._pipe.close()
            except OSError:
                pass
            self._pipe = None
        was = self.state["discord"]
        self._subscribed_channel, self._directory = None, None
        self._backlog.clear()
        with self._lock:
            self.state = {**self._empty_state(), "error": reason}
        if was == "connecté":
            self._event("discord", {"state": "fermé"})

    def _fail_pending(self, code: str, message: str) -> None:
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                return
            call.error = RpcError(code, message)
            call.done.set()

    def _remembered(self) -> dict:
        try:
            data = json.loads(self._memory.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _remember(self) -> None:
        # Écrit par le fil Discord et par les requêtes : fichier remplacé d'un coup, jamais lu à moitié écrit (un
        # fichier vide ferait oublier que le micro était coupé).
        try:
            with self._memory_lock:
                self._memory.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._memory.with_name(f"{self._memory.name}.{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps({"mute": self.state["mute"], "deaf": self.state["deaf"],
                                           "at": self._clock()}), encoding="utf-8")
                os.replace(tmp, self._memory)
        except OSError:
            log.warning("Discord : dernier état non enregistré")


def _person(data: dict) -> dict:
    user = data.get("user") or {}
    state = data.get("voice_state") or {}
    return {"id": str(user.get("id", "")), "name": (data.get("nick") or user.get("global_name")
                                                   or user.get("username") or "?")[:60],
            "mute": bool(state.get("self_mute") or state.get("mute")),
            "deaf": bool(state.get("self_deaf") or state.get("deaf"))}


def _french(code, message: str) -> str:
    known = {ALREADY_IN_VOICE: "Vous êtes déjà dans un salon vocal.", NOT_AUTHENTICATED: "ORION n'est pas autorisé.",
             INVALID_TOKEN: "Autorisation Discord expirée : reliez ORION à nouveau.",
             5000: "Discord a refusé : salon introuvable ou inaccessible.", 4002: "Commande refusée par Discord.",
             5004: "Discord a refusé l'autorisation.", 5005: "Accès au salon refusé par Discord.",
             4000: "Commande invalide pour Discord.", 4005: "Discord : fonction non autorisée pour cette application."}
    return known.get(code, f"Discord a refusé ({str(message)[:80] or code}).")
