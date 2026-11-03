"""Côté agent Windows : appel de Frag Factory sur la boucle locale du PC, avec le jeton de liaison.

- Adresse : ``[fragfactory] url`` de ``windows_agent.toml`` (par défaut ``http://127.0.0.1:8742``) ; seule une
  adresse de boucle locale est acceptée, sauf réglage explicite ``allow_lan = true``.
- Jeton : ``FRAGFACTORY_LINK_TOKEN`` dans le ``.env`` de l'agent (le même que ``ORION_LINK_TOKEN`` côté Frag
  Factory) ; jamais écrit dans un journal ni renvoyé.
- Réponse de Frag Factory = donnée non fiable : taille bornée, JSON vérifié, messages d'erreur courts.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

from orion.fragfactory import protocol

log = logging.getLogger(__name__)

TOKEN_ENV = "FRAGFACTORY_LINK_TOKEN"
MIN_TOKEN = 32
MAX_RESPONSE = 2_000_000
UNAVAILABLE = "fragfactory_unavailable"


class LinkError(Exception):
    """Échec d'une opération : ``code`` stable, ``message`` court en français, ``http`` d'origine."""

    def __init__(self, code: str, message: str, http: int = 0):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LinkClient:
    def __init__(self, url: str = "http://127.0.0.1:8742", token: str | None = None, timeout: float = 15.0,
                 allow_lan: bool = False, opener: Callable[..., Any] | None = None):
        url = url.rstrip("/")
        if not re.match(r"^https?://[\w.\-]+(:\d+)?$", url):
            raise ValueError(f"[fragfactory] url invalide : {url!r}")
        if not allow_lan and not _local(url):
            raise ValueError("[fragfactory] url : Frag Factory doit être sur ce PC (127.0.0.1), sauf allow_lan = true")
        self.url, self.timeout = url, timeout
        self._token = (token if token is not None else os.environ.get(TOKEN_ENV, "")).strip()
        self._open = opener or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return len(self._token) >= MIN_TOKEN

    def call(self, op: str, args: dict | None = None) -> Any:
        clean = protocol.validate(op, args or {})
        if op in protocol.LOCAL_OPS:
            return self._local_op(op, clean)
        if not self.configured:
            raise LinkError("link_not_configured", "Le jeton de liaison avec Frag Factory n'est pas configuré.")
        method, path, query, body = protocol.build_request(op, clean)
        url = self.url + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (OSError, ValueError) as exc:
            log.info("Frag Factory injoignable (%s)", type(exc).__name__)
            raise LinkError(UNAVAILABLE, "Frag Factory ne répond pas.") from exc
        if len(raw) > MAX_RESPONSE:
            raise LinkError("response_too_large", "Réponse de Frag Factory trop volumineuse.")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise LinkError("invalid_response", "Réponse de Frag Factory illisible.") from exc

    def _http_error(self, exc: urllib.error.HTTPError) -> LinkError:
        try:
            data = json.loads(exc.read(20_000) or b"{}")
        except ValueError:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        code, message = f"http_{exc.code}", ""
        if isinstance(detail, dict):
            code = str(detail.get("error") or code)[:48]
            message = str(detail.get("message") or "")
        elif isinstance(detail, str):
            message = detail
        message = message.strip()
        if not message or len(message) > 240 or not message.isprintable():
            message = {401: "Frag Factory refuse le jeton de liaison.", 403: "Frag Factory refuse cet appel.",
                       404: "Introuvable dans Frag Factory.", 409: "Le clip a changé entre-temps.",
                       429: "Frag Factory est trop sollicité, réessayez dans une minute.",
                       503: "L'intégration est désactivée dans Frag Factory."}.get(exc.code, "Frag Factory a refusé.")
        if exc.code == 401:
            log.warning("Frag Factory : jeton de liaison refusé")
        return LinkError(code if re.fullmatch(r"[a-z_0-9]{1,48}", code) else f"http_{exc.code}", message, exc.code)

    def _local_op(self, op: str, clean: dict) -> dict:
        if op == "open_clip":
            # Adresse construite ici à partir d'un identifiant entier validé : jamais un chemin reçu.
            target = f"{self.url}/#/review/{int(clean['clip_id'])}"
            webbrowser.open(target)
            return {"opened": True, "clip_id": clean["clip_id"]}
        raise LinkError("unknown_op", "Opération inconnue.")
