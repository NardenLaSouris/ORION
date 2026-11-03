"""Configuration de l'agent Windows : réglages dans un fichier TOML, jeton partagé hors du dépôt (.env)."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path

from orion.config import read_with_local, secret

TOKEN_NAME = "ORION_AGENT_TOKEN"
MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True)
class AgentConfig:
    host: str
    port: int
    allowed_ips: frozenset[str]
    token: str
    input_device: str = ""
    output_device: str = ""
    face_port: int | None = None
    tools: dict = field(default_factory=dict)
    # Frag Factory sur ce PC (section [fragfactory], inactive par défaut) ; jeton de liaison dans le .env.
    fragfactory: dict = field(default_factory=dict)
    fragfactory_token: str = field(default="", repr=False)
    audio: bool = True  # relais du micro, de la voix et de la musique (faux : agent sans audio, ex. agent V3)
    pc_tools: bool = True  # outils du PC (applications, volume, fichiers...)
    discord: dict | None = None  # [discord] activé : client_id, client_secret, redirect_uri, data_dir


def load_agent_config(path: str | Path, env_file: str | Path) -> AgentConfig:
    """Lit ``path`` ([agent], [audio], [face], [tools]) et ``<nom>.local.toml`` à côté, puis le jeton ``ORION_AGENT_TOKEN`` (environnement, sinon ``env_file``).

    Lève ValueError si la configuration n'est pas sûre : aucune IP autorisée, IP invalide, jeton absent ou court.
    """
    path = Path(path)
    data = read_with_local(path)
    raw, audio, face = data.get("agent", {}), data.get("audio", {}), data.get("face", {})
    ff, discord = data.get("fragfactory", {}), data.get("discord", {})
    unknown = (set(data) - {"agent", "audio", "face", "tools", "fragfactory", "discord"}) | (
        set(raw) - {"host", "port", "allowed_ips", "audio", "pc_tools", "token_name"}) | (
        set(audio) - {"input_device", "output_device"}) | (set(face) - {"open_on_connect", "port"}) | (
        set(ff) - {"enabled", "url", "allow_lan", "timeout"}) | (
        set(discord) - {"enabled", "client_id", "redirect_uri", "data_dir"})
    if unknown:
        raise ValueError(f"Réglages inconnus dans {path.name} : {sorted(unknown)}")
    port = _port(raw.get("port", 8765))
    face_port = _port(face.get("port", 8765)) if face.get("open_on_connect", False) else None
    ips = raw.get("allowed_ips", [])
    if not isinstance(ips, list) or not ips:
        raise ValueError("allowed_ips doit lister au moins une adresse IP (celle du Core ORION).")
    try:
        allowed = frozenset(str(ipaddress.ip_address(ip)) for ip in ips)
    except ValueError as exc:
        raise ValueError(f"Adresse IP invalide dans allowed_ips : {exc}") from exc
    # Agent V3 : son propre jeton (token_name), jamais celui de la production.
    token_name = str(raw.get("token_name", TOKEN_NAME))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", token_name):
        raise ValueError(f"token_name invalide : {token_name!r}")
    token = secret(token_name, Path(env_file))
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"{token_name} absent ou trop court ({MIN_TOKEN_LENGTH} caractères minimum) : "
                         f"définissez-le dans l'environnement ou dans {Path(env_file).name}.")
    ff_token = ""
    if ff.get("enabled", False):
        from orion.fragfactory.link import MIN_TOKEN, TOKEN_ENV

        ff_token = secret(TOKEN_ENV, Path(env_file))
        if len(ff_token) < MIN_TOKEN:
            raise ValueError(f"[fragfactory] activé mais {TOKEN_ENV} absent ou trop court ({MIN_TOKEN} caractères "
                             f"minimum) : définissez-le dans l'environnement ou dans {Path(env_file).name}.")
    return AgentConfig(str(raw.get("host", "0.0.0.0")), port, allowed, token,
                       str(audio.get("input_device", "")), str(audio.get("output_device", "")), face_port,
                       data.get("tools", {}), dict(ff), ff_token, bool(raw.get("audio", True)),
                       bool(raw.get("pc_tools", True)), _discord(discord, path, Path(env_file)))


def _discord(raw: dict, path: Path, env_file: Path) -> dict | None:
    """[discord] : identifiant de l'application (réglage ou DISCORD_CLIENT_ID) ; son secret jamais dans le TOML
    (DISCORD_CLIENT_SECRET, environnement ou .env de l'agent)."""
    if not raw.get("enabled", False):
        return None
    if raw.get("data_dir"):
        data_dir = Path(str(raw["data_dir"])).expanduser()
    else:
        data_dir = path.resolve().parent / "data" / "agent"
    return {"client_id": str(raw.get("client_id", "") or secret("DISCORD_CLIENT_ID", env_file)).strip(),
            "client_secret": secret("DISCORD_CLIENT_SECRET", env_file).strip(),
            "redirect_uri": str(raw.get("redirect_uri", "")), "data_dir": data_dir}


def _port(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 65535:
        raise ValueError(f"Port invalide : {value!r}")
    return value
