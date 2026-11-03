"""Contrat entre ORION et Frag Factory : opérations en liste fermée, paramètres typés et bornés.

Partagé par le Core (qui demande) et l'agent Windows (qui relaie à Frag Factory sur la boucle locale du PC). L'agent
ne relaie **aucun chemin libre** : il n'accepte qu'un nom d'opération de cette liste et des paramètres conformes ; il
construit lui-même la requête vers ``/api/integration/v1``.

Frag Factory reste maître de ses données : ORION ne garde que des identifiants et des métadonnées courtes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

API_PREFIX = "/api/integration/v1"
SCHEMA_VERSION = 1
STATUSES = ("PENDING_REVIEW", "APPROVED", "REJECTED", "REVISION_REQUESTED", "PUBLISHED")
TIERS = ("pepite", "bon", "a_trier")
CATEGORIES = ("performance", "comedy", "reactions", "tension", "anomalies", "teamplay", "teamkill")
INTENTS = ("auto", "nerveux", "humour", "references", "court", "tendances", "trois")
ORDERS = ("recent", "score", "comedy", "skill", "oldest")
REWORK_ITEMS = ("start_earlier", "end_later", "start_later", "end_earlier", "framing", "voices", "subtitles",
                "effects")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:\-]{6,64}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?)?$")
MAX_BODY = 16_000


class ProtocolError(ValueError):
    """Opération ou paramètre refusé avant tout appel."""


@dataclass(frozen=True)
class Op:
    method: str
    path: str                      # gabarit, ex. "/clips/{clip_id}" ; seuls des entiers validés y entrent
    params: dict[str, tuple]       # nom -> (type, contraintes…)
    body: bool = False             # paramètres envoyés en JSON (sinon en paramètres d'URL)


def _int(minimum: int, maximum: int) -> tuple:
    return ("int", minimum, maximum)


CLIP = _int(1, 10**9)
OPS: dict[str, Op] = {
    "status": Op("GET", "/status", {}),
    "events": Op("GET", "/events", {"after": _int(0, 10**12), "limit": _int(1, 200)}),
    "ack": Op("POST", "/events/ack", {"upto": _int(0, 10**12), "ids": ("ids",)}, body=True),
    "clips": Op("GET", "/clips", {"status": ("choice", STATUSES), "tier": ("choice", TIERS),
                                  "category": ("choice", CATEGORIES), "since": ("date",), "q": ("text", 120),
                                  "order": ("choice", ORDERS), "limit": _int(1, 100)}),
    "clip": Op("GET", "/clips/{clip_id}", {"clip_id": CLIP}),
    "decide": Op("POST", "/clips/{clip_id}/decision",
                 {"clip_id": CLIP, "action": ("choice", ("approve", "reject")), "request_id": ("request_id",),
                  "expected_status": ("choice", STATUSES), "note": ("text", 1000)}, body=True),
    "revision": Op("POST", "/clips/{clip_id}/revision",
                   {"clip_id": CLIP, "request_id": ("request_id",), "items": ("items", REWORK_ITEMS),
                    "note": ("text", 2000), "shift_s": ("float", 0.5, 30.0),
                    "expected_status": ("choice", STATUSES)}, body=True),
    "style": Op("GET", "/clips/{clip_id}/style", {"clip_id": CLIP, "measure": ("bool",)}),
    "recommend": Op("POST", "/clips/{clip_id}/recommendations", {"clip_id": CLIP, "intent": ("choice", INTENTS)},
                    body=True),
    "version": Op("POST", "/clips/{clip_id}/versions",
                  {"clip_id": CLIP, "request_id": ("request_id",), "base_profile": ("slug", 64),
                   "overrides": ("overrides",), "target_duration_s": ("float", 5.0, 90.0), "title": ("text", 120)},
                  body=True),
    "stats": Op("GET", "/stats", {"since": ("date",)}),
    "history": Op("GET", "/history", {"since": ("date",), "limit": _int(1, 200)}),
    "errors": Op("GET", "/errors", {"limit": _int(1, 100)}),
    "report": Op("POST", "/reports", {"since": ("date",), "until": ("date",), "request_key": ("request_id",)},
                 body=True),
    "reports": Op("GET", "/reports", {"limit": _int(1, 60)}),
}
# Opération propre à l'agent (pas une route de Frag Factory) : ouvrir la page de revue d'un clip sur le PC.
LOCAL_OPS = {"open_clip": {"clip_id": CLIP}}
WRITE_OPS = frozenset({"ack", "decide", "revision", "version", "report"})

# Réglages éditoriaux qu'une version peut porter (miroir de fragfactory.integration.style.SUPPORTED, revérifiés
# par Frag Factory). Un champ inconnu est refusé dès ORION.
OVERRIDE_KEYS = frozenset({"layout", "zoom_peak", "zoom_factor", "zoom_duration_s", "slowmo_peak", "slowmo_speed",
                           "slowmo_duration_s", "cut_dead_time", "subtitles", "loudnorm", "target_lufs", "sfx",
                           "bg_blur_sigma", "subtitle_style", "track_gains_db"})


def _check(name: str, spec: tuple, value: Any) -> Any:
    kind = spec[0]
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not spec[1] <= value <= spec[2]:
            raise ProtocolError(f"{name} : entier de {spec[1]} à {spec[2]} attendu")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not spec[1] <= value <= spec[2]:
            raise ProtocolError(f"{name} : nombre de {spec[1]:g} à {spec[2]:g} attendu")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ProtocolError(f"{name} : vrai ou faux attendu")
        return value
    if kind == "choice":
        if value not in spec[1]:
            raise ProtocolError(f"{name} : valeur parmi {', '.join(spec[1])}")
        return value
    if kind == "text":
        if not isinstance(value, str) or len(value) > spec[1] or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ProtocolError(f"{name} : texte de {spec[1]} caractères au plus")
        return value.strip()
    if kind == "slug":
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_]{1,%d}" % spec[1], value):
            raise ProtocolError(f"{name} : identifiant de profil invalide")
        return value
    if kind == "date":
        if not isinstance(value, str) or not ISO_DATE.match(value):
            raise ProtocolError(f"{name} : date ISO attendue")
        return value
    if kind == "request_id":
        if not isinstance(value, str) or not REQUEST_ID.match(value):
            raise ProtocolError(f"{name} : identifiant de demande invalide")
        return value
    if kind == "ids":
        if not isinstance(value, list) or len(value) > 200 or not all(
                isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v) for v in value):
            raise ProtocolError(f"{name} : liste d'identifiants d'événements attendue")
        return value
    if kind == "items":
        if not isinstance(value, list) or len(value) > 12 or not set(value) <= set(spec[1]):
            raise ProtocolError(f"{name} : éléments parmi {', '.join(spec[1])}")
        return list(dict.fromkeys(value))
    if kind == "overrides":
        if not isinstance(value, dict) or not set(value) <= OVERRIDE_KEYS:
            unknown = sorted(set(value) - OVERRIDE_KEYS) if isinstance(value, dict) else []
            raise ProtocolError(f"{name} : réglages inconnus du moteur de rendu ({', '.join(unknown) or 'format'})")
        return value
    raise ProtocolError(f"{name} : type de paramètre inconnu")


def validate(op: str, args: Any) -> dict:
    """Vérifie une opération et ses paramètres ; rend des paramètres propres. Lève ProtocolError sinon."""
    spec = OPS.get(op).params if op in OPS else LOCAL_OPS.get(op)
    if spec is None:
        raise ProtocolError(f"opération inconnue : {op!r}")
    if not isinstance(args, dict):
        raise ProtocolError("paramètres : objet attendu")
    unknown = set(args) - set(spec)
    if unknown:
        raise ProtocolError(f"paramètres inconnus : {', '.join(sorted(unknown))}")
    clean = {k: _check(k, spec[k], v) for k, v in args.items() if v is not None}
    if "clip_id" in spec and "clip_id" not in clean:
        raise ProtocolError("clip_id obligatoire")
    if op in ("decide", "revision", "version") and "request_id" not in clean:
        raise ProtocolError("request_id obligatoire pour une action")
    if op == "decide" and "action" not in clean:
        raise ProtocolError("action obligatoire")
    return clean


def build_request(op: str, clean: dict) -> tuple[str, str, dict | None, dict | None]:
    """(méthode, chemin, paramètres d'URL, corps JSON) d'une opération déjà validée."""
    o = OPS[op]
    path = o.path
    rest = dict(clean)
    if "{clip_id}" in path:
        path = path.replace("{clip_id}", str(int(rest.pop("clip_id"))))
    if o.body:
        return o.method, API_PREFIX + path, None, rest
    return o.method, API_PREFIX + path, {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in rest.items()}, None
