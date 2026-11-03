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
    """Transport du Core : action « fragfactory » de l'agent Windows V3 (``agent_url``, jeton ``token_env``), jamais
    l'agent ni le jeton de la production. Agent injoignable ou refus : ``ToolError`` au code stable (le module en
    déduit « Frag Factory indisponible »)."""
    from orion.config import secret
    from orion.factory import ENV_FILE
    from orion.platform.agentlink import AgentLink, AgentUnavailable
    from orion.tools.base import ToolError

    f = cfg.fragfactory
    if not f.agent_url:
        raise ValueError("[fragfactory] agent_url : adresse de l'agent Windows V3 (ex. http://192.168.1.128:8875)")
    link = AgentLink(f.agent_url, secret(f.token_env, ENV_FILE), timeout=max(f.timeout, 5.0))

    def call(op: str, args: dict) -> Any:
        try:
            result = link.call("fragfactory", {"op": op, "args": args}, timeout=max(f.timeout, 5.0))
        except AgentUnavailable as exc:
            raise ToolError(exc.code, exc.message) from exc
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
