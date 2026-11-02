"""Modèle d'une surveillance ORION WATCH : identité, condition structurée, sources, fréquence, état, confiance.

Une condition est toujours une donnée (un dictionnaire validé ici), jamais du code : le LLM peut aider à la construire
à partir d'une phrase, mais elle est vérifiée champ par champ et évaluée par des règles déterministes.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from orion.platform.states import StateMachine

ACTIVE, PAUSED, TRIGGERED, COMPLETED, EXPIRED, FAILED = "ACTIVE", "PAUSED", "TRIGGERED", "COMPLETED", "EXPIRED", "FAILED"
STATES = StateMachine({
    ACTIVE: {PAUSED, TRIGGERED, EXPIRED, FAILED, COMPLETED},
    PAUSED: {ACTIVE, EXPIRED, COMPLETED},
    TRIGGERED: {ACTIVE, COMPLETED, PAUSED, EXPIRED},
    FAILED: {ACTIVE, COMPLETED},
    COMPLETED: set(),
    EXPIRED: set(),
}, ACTIVE)
RUNNING = (ACTIVE, TRIGGERED)  # vérifiées périodiquement

PRICE, AVAILABILITY, EVENT, ANNOUNCEMENT = "price", "availability", "event", "announcement"
KINDS = (PRICE, AVAILABILITY, EVENT, ANNOUNCEMENT)
CHANNELS = ("auto", "written")  # auto : selon la présence (orion/platform/alerts.py)
MODES = ("once", "repeat")
RELIABILITY = ("official", "agency", "media", "community", "unknown")
EVENT_REQUIRE = ("confirmed", "official", "any")
MAX_NAME, MAX_TEXT, MAX_KEYWORDS = 80, 300, 8


class WatchError(ValueError):
    pass


def _words(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or len(value) > MAX_KEYWORDS:
        raise WatchError(f"{label} : liste de {MAX_KEYWORDS} mots au plus attendue")
    out = []
    for word in value:
        if not isinstance(word, str) or not word.strip() or len(word) > 60:
            raise WatchError(f"{label} : mot invalide")
        out.append(word.strip().lower())
    return tuple(out)


def _number(value: Any, label: str, minimum: float = 0.0, maximum: float = 1e7) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise WatchError(f"{label} : nombre entre {minimum:g} et {maximum:g} attendu")
    return float(value)


def validate_condition(raw: Any) -> dict:
    """Condition propre (types vérifiés, champs connus seulement) ; WatchError sinon."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise WatchError(f"condition : « kind » parmi {', '.join(KINDS)} attendu")
    kind = raw["kind"]
    allowed = {"kind", "product", "max_price", "min_score", "condition", "keywords", "game", "require"}
    extra = set(raw) - allowed
    if extra:
        raise WatchError(f"condition : champs inconnus {sorted(extra)}")
    out: dict[str, Any] = {"kind": kind}
    if kind in (PRICE, AVAILABILITY):
        product = raw.get("product")
        if not isinstance(product, str) or not 2 <= len(product.strip()) <= 80:
            raise WatchError("condition : produit attendu")
        out["product"] = product.strip().lower()
        out["max_price"] = _number(raw.get("max_price"), "prix maximum")
        out["min_score"] = _number(raw.get("min_score"), "score minimum", 0, 100)
        if kind == PRICE and out["max_price"] is None and out["min_score"] is None:
            out["min_score"] = 70.0  # « bonne affaire » sans seuil : score d'opportunité, jamais « n'importe quel prix »
        state = raw.get("condition", "new")
        if state not in ("new", "any"):
            raise WatchError("condition : état « new » ou « any »")
        out["condition"] = state
    else:
        out["keywords"] = _words(raw.get("keywords"), "mots-clés")
        if not out["keywords"]:
            raise WatchError("condition : au moins un mot-clé")
        game = raw.get("game")
        if game is not None and game not in ("pokemon_go",):
            raise WatchError("condition : jeu inconnu")
        out["game"] = game
        require = raw.get("require", "confirmed" if kind == EVENT else "official")
        if require not in EVENT_REQUIRE:
            raise WatchError(f"condition : exigence parmi {', '.join(EVENT_REQUIRE)}")
        out["require"] = require
    return out


@dataclass
class Watch:
    name: str
    condition: dict
    sources: tuple[str, ...]
    interval_s: float
    description: str = ""
    expires_at: float | None = None
    channel: str = "auto"
    mode: str = "once"
    min_reliability: str = "media"  # fiabilité minimale d'une source pour déclencher
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: float = field(default_factory=time.time)
    state: str = ACTIVE
    last_check_at: float | None = None
    next_check_at: float | None = None
    failures: int = 0
    last_error: str = ""
    triggers: int = 0

    def __post_init__(self) -> None:
        self.name = re.sub(r"\s+", " ", str(self.name or "")).strip()
        if not self.name or len(self.name) > MAX_NAME:
            raise WatchError("nom de 1 à 80 caractères attendu")
        self.description = str(self.description or "").strip()[:MAX_TEXT]
        self.condition = validate_condition(self.condition)
        self.sources = tuple(self.sources)
        if not self.sources or not all(isinstance(s, str) and s for s in self.sources):
            raise WatchError("au moins une source autorisée")
        if self.channel not in CHANNELS:
            raise WatchError(f"canal parmi {', '.join(CHANNELS)}")
        if self.mode not in MODES:
            raise WatchError("mode « once » ou « repeat »")
        if self.min_reliability not in RELIABILITY:
            raise WatchError("fiabilité minimale inconnue")
        if self.state not in STATES.states:
            raise WatchError(f"état inconnu : {self.state}")

    def as_dict(self) -> dict:
        data = asdict(self)
        data["sources"] = list(self.sources)
        data["condition"] = dict(self.condition, **({"keywords": list(self.condition["keywords"])}
                                                    if "keywords" in self.condition else {}))
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Watch":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: (tuple(v) if k == "sources" else v) for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Finding:
    """Ce qu'une vérification a trouvé et qui remplit la condition (une offre, un événement, une annonce)."""
    fingerprint: str  # même chose trouvée deux fois : une seule notification
    title: str
    message: str
    url: str
    source: str
    details: dict = field(default_factory=dict)
    level: int = 2  # Level.HIGH par défaut (orion/platform/alerts.py)
