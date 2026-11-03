"""Quel clip la demande désigne-t-elle ? Lu dans les mots, jamais proposé par le LLM.

``reference(texte)`` rend une référence textuelle, ou None si la demande ne nomme aucun clip :

| Mots | Référence |
|---|---|
| « clip 42 », « le clip numéro 42 » | ``id:42`` |
| « la deuxième », « le 3e clip » (dans la dernière liste présentée) | ``ordinal:2`` |
| « le dernier clip », « la dernière pépite » | ``latest`` / ``latest:pepite`` |
| « le clip du teamkill », « celui avec le fou rire » | ``search:teamkill`` |
| « valide-le », « ce clip », « celui-ci » (après une annonce ou une présentation) | ``context`` |

La référence n'est qu'une étape : ``FragFactoryModule.resolve`` la transforme en clip concret (``clip:57``) avant
toute confirmation ; zéro ou plusieurs clips possibles → « lequel ? », jamais une action au hasard.
"""

from __future__ import annotations

import re

from orion.watch.products import plain

ORDINALS = {"premier": 1, "premiere": 1, "deuxieme": 2, "second": 2, "seconde": 2, "troisieme": 3,
            "quatrieme": 4, "cinquieme": 5, "sixieme": 6, "septieme": 7, "huitieme": 8, "neuvieme": 9,
            "dixieme": 10}
TIER_WORDS = {"pepite": "pepite", "pepites": "pepite", "bon clip": "bon", "bons clips": "bon", "a trier": "a_trier"}
CLIP_WORDS = ("clip", "clips", "montage", "video", "pepite", "sequence", "moment", "version")
CONTEXT = ("valide le", "valides le", "validez le", "accepte le", "acceptez le", "refuse le", "refusez le",
           "rejette le", "garde le", "ce clip", "cette pepite", "celui ci", "celle ci", "ce montage", "cette video",
           "montre le", "montre le moi", "mets le", "lance le", "rejoue le", "revoir le", "le revoir", "le voir",
           "ce moment")
STOP = {"le", "la", "les", "un", "une", "des", "du", "de", "d", "l", "clip", "clips", "montage", "video", "avec",
        "sur", "ou", "on", "qui", "que", "celui", "celle", "a", "au", "aux", "et", "en", "mon", "ma", "mes", "ce",
        "cette", "pepite", "valide", "accepte", "refuse", "rejette", "montre", "moi", "fais", "revoir", "version",
        "nouvelle", "autre", "veux", "je", "orion", "style", "quel", "quelle", "est", "dernier", "derniere"}
SEARCH = re.compile(r"\b(?:clip|montage|video|sequence|moment|celui|celle)\s+(?:du|de la|de l|des|avec|ou|sur)\s+"
                    r"(?:l\s+|la\s+|le\s+|les\s+)?([a-z0-9 ]{3,60})")


def reference(text: str) -> str | None:
    t = f" {plain(text)} "
    if not t.strip():
        return None
    m = re.search(r"\b(?:clip|montage|video)\s+(?:numero\s+|n\s+)?(\d{1,7})\b", t)
    if m:
        return f"id:{int(m.group(1))}"
    for word, n in ORDINALS.items():
        if re.search(rf"\b(?:le|la)\s+{word}\b", t) or re.search(rf"\b{word}\s+(?:clip|montage|video)\b", t):
            return f"ordinal:{n}"
    m = re.search(r"\b(?:le|la)\s+(\d{1,2})\s*(?:e|eme)\b", t)
    if m and 0 < int(m.group(1)) <= 20:
        return f"ordinal:{int(m.group(1))}"
    if re.search(r"\bderni(?:er|ere)\b", t):
        tier = next((v for k, v in TIER_WORDS.items() if f" {k} " in t), None)
        if tier or any(f" {w} " in t for w in CLIP_WORDS):
            return f"latest:{tier}" if tier else "latest"
    m = SEARCH.search(t)
    if m:
        words = [w for w in m.group(1).split() if w not in STOP and len(w) >= 3][:4]
        if words:
            return "search:" + " ".join(words)
    if any(f" {c} " in t for c in CONTEXT):
        return "context"
    return None


def evidence(value: object, text: str) -> bool:
    """Une référence proposée par le LLM n'est retenue que si les mots de la demande la donnent."""
    return isinstance(value, str) and value == reference(text)


def parse(ref: str) -> tuple[str, str]:
    kind, _, arg = (ref or "").partition(":")
    if kind not in ("id", "ordinal", "latest", "search", "context", "clip"):
        raise ValueError(f"référence de clip invalide : {ref!r}")
    return kind, arg
