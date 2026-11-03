"""Client du Core vers l'agent Windows V3 (port 8875 du PC) : ``POST /actions/<action>`` avec le jeton propre à la
V3 (``ORION_V3_AGENT_TOKEN``), jamais celui de la production.

Partagé par les modules qui passent par cet agent (DISCORD, Frag Factory) : une seule façon de le joindre, de
reconnaître qu'il est hors ligne et de reprendre ses erreurs. Seuls un code et un message courts et lisibles sont
gardés d'une réponse d'erreur : la réponse de l'agent n'est pas une donnée fiable.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

TOKEN_ENV = "ORION_V3_AGENT_TOKEN"
MIN_TOKEN = 32


class AgentUnavailable(Exception):
    """Agent injoignable, jeton refusé, ou action refusée par l'agent : ``code`` stable, ``message`` en français."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AgentLink:
    def __init__(self, url: str, token: str, timeout: float = 6.0, reachable: Callable[[str], bool] | None = None):
        if len((token or "").strip()) < MIN_TOKEN:
            raise ValueError(f"jeton de l'agent V3 absent ou trop court ({MIN_TOKEN} caractères minimum)")
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token.strip()}", "Content-Type": "application/json"}
        self._timeout = timeout
        if reachable is None:
            from orion.llm.failover import tcp_reachable

            reachable = lambda target: tcp_reachable(target, 1.0)  # noqa: E731
        self._reachable = reachable

    def call(self, action: str, parameters: dict | None = None, timeout: float | None = None) -> dict:
        # Vérification rapide : un PC éteint ou un agent arrêté est signalé en 1 s, pas au bout du délai complet.
        if not self._reachable(self.url):
            raise AgentUnavailable("agent_offline", "L'agent du PC ne répond pas.")
        from orion import trace

        headers = {**self._headers, **({trace.HEADER: trace.current()} if trace.current() else {})}
        request = urllib.request.Request(f"{self.url}/actions/{action}", headers=headers,
                                         data=json.dumps({"parameters": parameters or {}}).encode())
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as response:
                return json.loads(response.read())["result"]
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read())
            except ValueError:
                data = {}
            code = data.get("error") if isinstance(data, dict) else ""
            message = data.get("message") if isinstance(data, dict) else ""
            if exc.code == 401:
                code, message = "agent_token", "L'agent du PC refuse le jeton d'ORION V3."
            if not isinstance(message, str) or not message.strip() or not message.isprintable() or len(message) > 200:
                message = "L'agent du PC a refusé l'action."
            code = code if isinstance(code, str) and code.replace("_", "").isalnum() else "agent_error"
            raise AgentUnavailable(code[:48] or "agent_error", message) from exc
        except (OSError, ValueError, KeyError) as exc:
            raise AgentUnavailable("agent_offline", "L'agent du PC ne répond pas.") from exc
