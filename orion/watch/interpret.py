"""Phrase -> surveillance structurée (« Surveille les RTX 5090 et préviens-moi d'une bonne affaire »).

Les règles reconnaissent les demandes courantes sans LLM ; le LLM n'est consulté qu'en dernier recours, et sa réponse
est une condition validée champ par champ (orion/watch/models.py), jamais du code. Rien n'est créé sans
confirmation de l'utilisateur (outil vocal à confirmation, ou création dans ORION Control).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orion.platform.intelligence import StructuredLLM
from orion.watch.models import ANNOUNCEMENT, AVAILABILITY, EVENT, KINDS, PRICE, WatchError, validate_condition
from orion.watch.products import FAMILY, plain

PRICE_WORDS = ("prix", "bonne affaire", "bonnes affaires", "promo", "promotion", "moins cher", "baisse", "deal")
STOCK_WORDS = ("disponible", "en stock", "de retour", "restock", "dispo", "reapprovisionn")
POGO = re.compile(r"\bpokemon ?go\b|\bpogo\b")
ANNOUNCE = re.compile(r"\b(?:annonce|annonces|sortie|sort|version|mise a jour|release|lancement)\b")
MAX_PRICE = re.compile(r"(?:moins de|sous|max(?:imum)?|en dessous de|<)\s*(\d[\d ]{1,6})\s*(?:€|euros?|e\b)")
STOP = {"previens", "moi", "quand", "les", "des", "une", "un", "le", "la", "sur", "de", "du", "et", "a", "pour",
        "surveille", "surveiller", "dis", "lorsqu", "lorsque", "qu", "arrivent", "arrive", "il", "y", "aura",
        "nouveau", "nouvelle", "officielle", "officiel", "officielles", "annonces", "annonce", "sortie", "pokemon",
        "go", "pogo", "previens-moi", "alerte", "moi", "en", "est", "sont", "d", "l", "s", "que", "tu", "me",
        "veux", "voudrais", "savoir", "signale", "dans", "jeu"}


@dataclass(frozen=True)
class Interpretation:
    name: str
    condition: dict
    source_kinds: tuple[str, ...]  # « offers » ou « items » : sources à proposer
    by: str  # « règles » ou « llm »


def _keywords(text: str) -> list[str]:
    return [w for w in plain(text).split() if len(w) > 2 and w not in STOP][:6]


def by_rules(request: str) -> Interpretation | None:
    text = plain(request)
    family = FAMILY.search(f" {text} ")
    if family is not None:
        product = f"rtx {family.group(1)}" + (f" {family.group(2)}" if family.group(2) else "")
        price = MAX_PRICE.search(request.lower().replace(" ", " ").replace(" ", " "))  # « € » gardé
        max_price = float(price.group(1).replace(" ", "")) if price else None
        if any(w in text for w in STOCK_WORDS) and not any(w in text for w in PRICE_WORDS):
            condition = {"kind": AVAILABILITY, "product": product, "max_price": max_price}
            name = f"{product.upper()} disponible"
        else:
            condition = {"kind": PRICE, "product": product, "max_price": max_price,
                         "min_score": None if max_price else 70}
            name = f"Prix {product.upper()}" + (f" < {max_price:.0f} €" if max_price else " (bonne affaire)")
        return Interpretation(name, validate_condition(condition), ("offers",), "règles")
    if POGO.search(text):
        keywords = _keywords(re.sub(POGO, " ", text)) or ["evenement"]
        condition = {"kind": EVENT, "game": "pokemon_go", "keywords": keywords, "require": "confirmed"}
        return Interpretation("Pokémon GO : " + " ".join(keywords), validate_condition(condition), ("items",),
                              "règles")
    if ANNOUNCE.search(text):
        keywords = _keywords(text)
        if keywords:
            condition = {"kind": ANNOUNCEMENT, "keywords": keywords, "require": "official"}
            return Interpretation("Annonce : " + " ".join(keywords), validate_condition(condition), ("items",),
                                  "règles")
    return None


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["name", "condition"],
    "properties": {
        "name": {"type": "string", "maxLength": 80},
        "condition": {
            "type": "object", "additionalProperties": False, "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "product": {"type": "string", "maxLength": 80},
                "max_price": {"type": "number", "minimum": 0, "maximum": 1_000_000},
                "min_score": {"type": "number", "minimum": 0, "maximum": 100},
                "keywords": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 60}},
                "require": {"type": "string", "enum": ["confirmed", "official", "any"]},
            },
        },
    },
}
TASK = ("Transforme la demande de surveillance de l'utilisateur en condition structurée. kind : « price » (prix d'un "
        "produit), « availability » (retour en stock), « event » (événement de jeu), « announcement » (annonce ou "
        "sortie officielle). Ne mets un prix maximum que s'il est dit. Mots-clés courts, en minuscules.")


def interpret(request: str, llm: StructuredLLM | None = None) -> Interpretation:
    """Règles d'abord, LLM ensuite ; WatchError si la demande reste incomprise (rien n'est deviné)."""
    found = by_rules(request)
    if found is not None:
        return found
    if llm is not None:
        def check(data: dict) -> Interpretation:
            condition = validate_condition(data["condition"])
            kinds = ("offers",) if condition["kind"] in (PRICE, AVAILABILITY) else ("items",)
            return Interpretation(data["name"].strip() or "Surveillance", condition, kinds, "llm")

        answer = llm.ask(TASK, SCHEMA, {"demande": request[:500]}, check)
        if answer is not None:
            return answer
    raise WatchError("Je n'ai pas compris quoi surveiller. Précisez le produit, l'événement ou l'annonce.")
