"""Modèle commun des connaissances d'ORION (module KNOWLEDGE, docs/v3/knowledge.md).

Une **assertion** (``Assertion``) dit quelque chose d'une **entité** (``Entity`` : vous, une personne, un lieu, une
organisation, un sujet, une chose) :

    sujet (entité)  —  prédicat  —  valeur (texte) ou objet (autre entité)  [+ négation]

Elle a une **nature** (``kind``) :
- ``fact`` : un fait (« vous habitez à Nantes ») ;
- ``preference`` : un goût ou une manière de faire (« vous préférez le thé ») ;
- ``interest`` : un centre d'intérêt (« les cartes graphiques ») ;
- ``hypothesis`` : une déduction d'ORION ou d'un module, jamais présentée comme sûre, jamais sur une catégorie
  sensible, qui ne devient un fait que confirmée par vous.

et un **état** : ``active``, ``contested`` (contradiction en attente de votre arbitrage), ``superseded`` (remplacée
par une valeur plus récente), ``retracted`` (retirée), ``expired`` (conservation dépassée).

Chaque assertion garde ses **provenances** (qui l'a dite, d'où, quand), son **historique** (chaque changement de
valeur, de confiance ou d'état, avec sa raison), une **confiance** de 0 à 1 et ses dates de validité et d'expiration.
Tout est chiffré dans la base personnelle (lot F4) : rien de ce modèle n'est jamais écrit en clair.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from orion.personality import normalize

KINDS = ("fact", "preference", "interest", "hypothesis")
STATUSES = ("active", "contested", "superseded", "retracted", "expired")
ENTITY_KINDS = ("self", "person", "place", "organisation", "topic", "thing")
SELF = "self"  # vous : le sujet par défaut

# Prédicats à une seule valeur à la fois : une nouvelle valeur contredit l'ancienne (« habite » Nantes puis Lyon).
FUNCTIONAL = frozenset({"habite", "ne le", "age", "anniversaire", "travaille chez", "metier", "prenom", "nom",
                        "telephone", "adresse", "langue", "fuseau horaire", "voiture", "est"})
# Catégories sensibles (RGPD, art. 9) : jamais déduites. Une hypothèse qui en touche une est refusée.
SENSITIVE = re.compile(
    r"\b(?:"
    # santé
    r"sante|maladie|maladies|malade|medical|medicale|medicament|medicaments|traitement|therapie|psy|psychiatre|"
    r"psychologue|hopital|hospitalise|handicap|handicape|enceinte|grossesse|diabete|diabetique|cancer|asthme|"
    r"asthmatique|depression|depressif|anxiete|allergie|vih|sida|epilepsie|autisme|autiste|tdah|addiction|alcoolique|"
    r"regime medical|ordonnance|"
    # convictions religieuses ou philosophiques
    r"religion|religieux|religieuse|croyant|croyante|foi|musulman|musulmane|chretien|chretienne|catholique|"
    r"protestant|juif|juive|bouddhiste|hindou|athee|eglise|mosquee|synagogue|"
    # opinions politiques, syndicales
    r"politique|politiques|vote|voter|electeur|parti|militant|militante|syndicat|syndique|gauche|droite|"
    r"extreme droite|extreme gauche|"
    # vie et orientation sexuelles, origine, données judiciaires, biométriques, génétiques
    r"orientation|sexualite|sexuel|sexuelle|gay|lesbienne|homosexuel|bisexuel|transgenre|origine ethnique|ethnie|"
    r"race|casier|condamnation|condamne|biometrique|genetique"
    r")\b")

# Confiance de départ selon l'origine ; une hypothèse reste plafonnée (jamais « sûre » sans vous).
BASE_CONFIDENCE = {"user": 0.95, "admin": 0.95, "import": 0.8, "module": 0.5}
HYPOTHESIS_CAP = 0.75
FACT_CAP = 0.99
# Conservation par défaut (jours ; None : sans limite). Décision n° 2 du 2 novembre : interactions 180 jours.
RETENTION_DAYS = {"fact": None, "preference": None, "interest": 365, "hypothesis": 180}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def plain(text: str) -> str:
    return normalize(text or "")


def origin_of(principal: str) -> str:
    """« user », « admin », « import » ou « module » (toute origine automatique)."""
    if principal in ("user", "admin"):
        return principal
    if principal.startswith("import:"):
        return "import"
    return "module"


@dataclass
class Provenance:
    principal: str  # « user », « admin », « import:memory.json », « module:news »…
    source: str = ""  # où : « conversation », « ORION Control », nom du fichier importé, identifiant d'article…
    at: float = 0.0
    request_id: str = ""


@dataclass
class Revision:
    at: float
    change: str  # « créée », « valeur », « confiance », « état », « confirmée »…
    before: Any = None
    after: Any = None
    why: str = ""
    by: str = ""


@dataclass
class Entity:
    id: str
    name: str
    kind: str = "thing"
    aliases: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def names(self) -> list[str]:
        return [self.name, *self.aliases]

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Entity":
        return Entity(**{k: data[k] for k in ("id", "name", "kind", "aliases", "created_at") if k in data})


@dataclass
class Assertion:
    id: str
    kind: str
    subject: str  # identifiant d'entité
    predicate: str  # forme normalisée (« habite », « aime », « note »…)
    value: str = ""  # valeur dite, telle quelle
    object: str = ""  # identifiant d'entité quand la valeur en est une (relation)
    negated: bool = False  # « n'aime pas »
    text: str = ""  # phrase lisible (« vous habitez à Nantes »)
    confidence: float = 0.5
    status: str = "active"
    provenance: list[Provenance] = field(default_factory=list)
    history: list[Revision] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    valid_from: float | None = None
    valid_until: float | None = None
    expires_at: float | None = None
    replaces: str = ""  # assertion remplacée par celle-ci
    conflict: str = ""  # identifiant du conflit en cours

    @property
    def slot(self) -> tuple[str, str]:
        """Place d'une valeur : (sujet, prédicat). Un prédicat à valeur unique n'y admet qu'une valeur active."""
        return self.subject, self.predicate

    @property
    def identity(self) -> tuple[str, str, str, bool]:
        """Même sujet, même prédicat, même valeur, même sens : la même assertion (dédoublonnage)."""
        return self.subject, self.predicate, plain(self.object or self.value), self.negated

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Assertion":
        data = dict(data)
        data["provenance"] = [Provenance(**p) for p in data.get("provenance", [])]
        data["history"] = [Revision(**r) for r in data.get("history", [])]
        return Assertion(**data)


@dataclass
class Conflict:
    """Deux valeurs incompatibles pour la même place : ORION ne choisit pas seul, il vous demande."""
    id: str
    subject: str
    predicate: str
    assertions: list[str]
    created_at: float
    resolved_at: float | None = None
    resolution: str = ""  # identifiant gardé, ou « aucune »
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Conflict":
        return Conflict(**data)


def normalize_predicate(predicate: str) -> str:
    text = plain(predicate)
    if not text or len(text) > 40:
        raise ValueError("prédicat vide ou trop long")
    return text


def sensitive(*texts: str) -> bool:
    return any(SENSITIVE.search(plain(t)) for t in texts if t)
