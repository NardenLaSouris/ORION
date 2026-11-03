"""Action ``fragfactory`` de l'agent Windows : relais vers Frag Factory, sur le PC, en liste fermée.

Le Core appelle ``POST /actions/fragfactory`` (IP autorisée + ``ORION_AGENT_TOKEN``, comme toute action de l'agent)
avec ``{"op": "<opération>", "args": {...}}``. L'agent revérifie l'opération et ses paramètres (``protocol``), puis
construit lui-même la requête vers Frag Factory : aucun chemin ni aucune adresse ne viennent du Core.

La confirmation des actions sensibles (valider, refuser, nouvelle version) est demandée par le Core avant l'appel ;
Frag Factory revérifie en plus le statut du clip et ignore une décision déjà appliquée.
"""

from __future__ import annotations

from orion.fragfactory import protocol
from orion.fragfactory.link import LinkClient, LinkError
from orion.tools.base import Param, Risk, Tool, ToolError

ALL_OPS = tuple(sorted(set(protocol.OPS) | set(protocol.LOCAL_OPS)))


def fragfactory_action(client: LinkClient) -> Tool:
    def run(op: str, args: dict | None = None) -> dict:
        try:
            result = client.call(op, args or {})
        except protocol.ProtocolError as exc:
            raise ToolError("invalid_parameters", str(exc)[:200]) from exc
        except LinkError as exc:
            raise ToolError(exc.code, exc.message) from exc
        return {"op": op, "data": result}

    return Tool("fragfactory", "Relais vers Frag Factory sur ce PC (opérations en liste fermée).",
                {"op": Param(str, "opération Frag Factory", choices=ALL_OPS, max_length=24),
                 "args": Param(dict, "paramètres de l'opération", required=False)},
                {"data": "réponse de Frag Factory"}, Risk.SAFE, run)


def build_from_agent_config(config) -> Tool | None:
    """Outil de l'agent si ``[fragfactory] enabled = true`` dans ``windows_agent.toml`` ; sinon None."""
    ff = getattr(config, "fragfactory", {}) or {}
    if not ff.get("enabled", False):
        return None
    client = LinkClient(str(ff.get("url", "http://127.0.0.1:8742")), token=config.fragfactory_token,
                        timeout=float(ff.get("timeout", 15.0)), allow_lan=bool(ff.get("allow_lan", False)))
    return fragfactory_action(client)
