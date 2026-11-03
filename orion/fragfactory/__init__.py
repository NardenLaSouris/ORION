"""Frag Factory × ORION V3 : ORION supervise et pilote Frag Factory (production de clips CS2 sur le PC fixe).

- ``engine`` : module V3 (synchronisation des événements, notifications, rapport du matin, contexte, état).
- ``tools`` : outils vocaux (lecture, conseil, actions confirmées).
- ``references`` : quel clip la demande désigne (lu dans les mots, jamais proposé par le LLM).
- ``protocol`` : opérations en liste fermée, partagées par le Core et l'agent.
- ``agent_action`` / ``link`` : côté agent Windows, relais vers Frag Factory sur la boucle locale du PC.

Document : ``docs/v3/fragfactory.md``.
"""

from __future__ import annotations

from typing import Any

from orion.fragfactory.engine import FragFactoryModule, FragFactorySettings


def agent_transport(cfg) -> Any:
    """Transport du Core : action « fragfactory » de l'agent de l'appareil qui héberge Frag Factory."""
    from orion.config import secret
    from orion.factory import ENV_FILE
    from orion.tools.devices import AgentClient, load_devices

    devices = load_devices(cfg.tools.devices) if cfg.tools.enabled else None
    if devices is None:
        raise ValueError("[fragfactory] activé : déclarez le PC qui l'héberge dans [tools.devices] (url de son agent)")
    key = cfg.fragfactory.device or devices.default.key
    if key not in devices.keys():
        raise ValueError(f"[fragfactory] device = {key!r} : appareil absent de [tools.devices]")
    device = devices[key]
    if not device.url:
        raise ValueError(f"[fragfactory] device = {key!r} : cet appareil n'a pas d'agent (url)")
    client = AgentClient(secret("ORION_AGENT_TOKEN", ENV_FILE), timeout=max(cfg.fragfactory.timeout, 5.0))

    def call(op: str, args: dict) -> Any:
        result = client.call(device, "fragfactory", {"op": op, "args": args})
        return result.get("data") if isinstance(result, dict) else None

    return call


def build_module(cfg, store, *, alerts=None, scheduler=None, history=None, transport=None) -> FragFactoryModule:
    f = cfg.fragfactory
    settings = FragFactorySettings(poll_seconds=f.poll_seconds, report_enabled=f.report_enabled,
                                   report_time=f.report_time, timezone=f.timezone, voice_enabled=f.voice_enabled,
                                   written_enabled=f.written_enabled, notify_tiers=tuple(f.notify_tiers))
    return FragFactoryModule(store, transport or agent_transport(cfg), alerts=alerts, scheduler=scheduler,
                             history=history, settings=settings)


__all__ = ["FragFactoryModule", "FragFactorySettings", "agent_transport", "build_module"]
