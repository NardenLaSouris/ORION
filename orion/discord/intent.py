"""Demandes Discord reconnues sans LLM (déterministe) : rejoindre, quitter, micro, sourdine, état, participants.

Vocabulaire de Discord en français : « couper le micro » / « mute » = MUTE ; « mettre en sourdine » / « deafen » =
DEAFEN (casque : vous n'entendez plus rien, et Discord coupe aussi votre micro). Jamais de bascule : chaque demande
devient une commande explicite (MUTE, UNMUTE, DEAFEN, UNDEAFEN).

Pour ne pas confondre avec le son du PC :
- « mets-moi en sourdine », « mute-moi », « coupe mon micro », ou un mot de Discord (discord, vocal, micro) : Discord ;
- « mets en sourdine », « coupe le son » sans « moi » ni Discord : le son du PC (inchangé) ;
- « réactive le son », « remets le son » : Discord seulement si vous y êtes en sourdine (``deafened``), sinon le PC.

Une question (« est-ce que je suis mute ? ») ne change jamais rien : elle rend l'état. Le démute n'est reconnu que
dit explicitement (démute, unmute, réactive / rouvre / rallume mon micro).
"""

from __future__ import annotations

import re
from typing import Callable

from orion.personality import normalize

DISCORD_WORDS = re.compile(r"\b(?:discord|vocal|vocaux|vocale|micro|micros|microphone|salon|salons|canal|channel|"
                           r"deafen|undeafen|mute|demute|unmute)\b")
QUESTION = re.compile(r"^(?:orion |jarvis )?(?:est ce que|est ce qu|suis je|je suis|est il|est elle|qui|quel|quelle|"
                      r"quels|quelles|dans quel|dans quelle|ou suis je|ou est ce que|ou je suis|combien|y a t il|"
                      r"y a qui|mon micro est|le micro est|il y a qui|c est qui)\b")
PARTICIPANTS = re.compile(r"\b(?:qui est|qui sont|qui y a|y a qui|il y a qui|qui d autre|c est qui|combien)\b.*"
                          r"\b(?:vocal|salon|discord|canal|channel|avec moi|connecte|connectes|en ligne)\b"
                          r"|\b(?:participants|liste des membres|les gens du vocal|membres du vocal)\b")
UNDEAFEN = re.compile(r"\b(?:undeafen|un deafen|enleve moi la sourdine|enleve la sourdine|retire moi la sourdine|"
                      r"retire la sourdine|sors moi de la sourdine|desactive la sourdine|plus en sourdine|"
                      r"reactive le son|remets le son|remets moi le son|remet le son|rallume le son|reactive mon casque|"
                      r"remets mon casque)\b")
DEAFEN = re.compile(r"\b(?:deafen|mets moi en sourdine|met moi en sourdine|mettez moi en sourdine|sourdine discord|"
                    r"en sourdine sur discord|coupe moi le son sur discord|coupe le son de discord|rends moi sourd|"
                    r"coupe mon casque)\b")
GENERIC_DEAFEN = re.compile(r"\b(?:mets en sourdine|sourdine)\b")
UNMUTE = re.compile(r"\b(?:demute|demuter|demutes|de mute|unmute|un mute|reactive mon micro|reactive le micro|"
                    r"rouvre mon micro|rouvre le micro|rallume mon micro|rallume le micro|remets mon micro|"
                    r"remets le micro|active mon micro|ouvre mon micro|enleve le mute|retire le mute|enleve moi le mute)\b")
MUTE = re.compile(r"\b(?:mute|muter|mutes|mute moi|coupe mon micro|coupe le micro|coupe moi le micro|eteins mon micro|"
                  r"eteins le micro|desactive mon micro|desactive le micro|ferme mon micro)\b")
LEAVE = re.compile(r"\b(?:quitte|quitter|quittes|sors|sors moi|deconnecte moi|deconnecte|raccroche|lache)\b.*"
                   r"\b(?:discord|vocal|salon|canal|channel|appel)\b|\b(?:deconnecte moi|sors moi)\b$")
JOIN = re.compile(r"^(?:orion |jarvis )?(?:(?:tu peux |peux tu )?(?:rejoins|rejoint|rejoindre|rejoins moi|"
                  r"connecte moi|connecte toi|mets moi|met moi|va|vas|passe|passe moi|bascule|bascule moi|entre|"
                  r"change de salon|change moi de salon|change)\b)(?P<rest>.*)$")
NEEDS_CONTEXT = ("connecte moi", "connecte toi", "mets moi", "met moi", "va", "vas", "passe", "passe moi", "bascule",
                 "bascule moi", "entre", "change")
FILLERS = {"le", "la", "les", "l", "au", "aux", "a", "du", "de", "des", "d", "dans", "sur", "en", "vers", "pour",
           "salon", "vocal", "vocale", "canal", "channel", "discord", "un", "une", "mon", "ma", "notre", "nom",
           "appele", "qui", "s", "se", "nomme", "voice", "chat"}
SERVER_SPLIT = re.compile(r"\s(?:sur|du|dans|de)\s(?:le\s)?(?:serveur|serv|server|discord)\s")


def _name(words: str) -> str:
    tokens = words.split()
    while tokens and tokens[0] in FILLERS:
        tokens.pop(0)
    while tokens and tokens[-1] in FILLERS | {"s", "il", "te", "plait", "stp", "merci"}:
        tokens.pop()
    return " ".join(tokens)


def discord_plan(text: str, exists: Callable[[str], bool], deafened: Callable[[], bool | None] | None = None
                 ) -> tuple[str, dict] | None:
    """(outil, paramètres) pour une demande Discord certaine, sinon None (le reste du Core prend le relais)."""
    if not exists("discord_status"):
        return None
    raw = text.strip()
    norm = normalize(raw)
    norm = re.sub(r"^(?:orion|jarvis)\s+", "", norm)
    if not norm:
        return None
    alias = _alias_plan(raw, norm, exists)
    if alias is not None:
        return alias
    about = bool(DISCORD_WORDS.search(norm))
    question = raw.endswith("?") or bool(QUESTION.match(norm))
    if question:
        if PARTICIPANTS.search(norm) and exists("discord_participants"):
            return "discord_participants", {}
        if about or re.search(r"\b(?:sourdine|mute|muet|muette)\b", norm):
            return "discord_status", {}
        return None
    if re.search(r"\b(?:ne|n|pas|jamais|plus jamais)\b", norm) and not re.search(r"\bplus en sourdine\b", norm):
        return None  # « ne me démute pas » : jamais traité ici
    if UNDEAFEN.search(norm):
        explicit = about or re.search(r"\b(?:undeafen|sourdine|casque|moi)\b", norm)
        if explicit or (deafened is not None and deafened() is True):
            return "discord_undeafen", {}
        return None
    if DEAFEN.search(norm) or (about and GENERIC_DEAFEN.search(norm)):
        return "discord_deafen", {}
    if UNMUTE.search(norm):
        return "discord_unmute", {}
    if MUTE.search(norm):
        return "discord_mute", {}
    if LEAVE.search(norm):
        return "discord_leave", {}
    join = JOIN.match(norm)
    if join:
        verb = norm[:join.start("rest")].strip()
        verb = re.sub(r"^(?:tu peux |peux tu )", "", verb)
        if verb in NEEDS_CONTEXT and not about:
            return None
        rest = " " + join.group("rest").strip() + " "
        server = ""
        split = SERVER_SPLIT.search(rest)
        if split:
            rest, server = rest[:split.start()], _name(rest[split.end():])
        else:
            # « dans Général sur « Et le Zombie avec ou sans ?? » » : le nom entre guillemets après « sur » est le serveur.
            tail = re.search(r"\bsur\s*[«“\"]\s*([^»”\"]+?)\s*[»”\"]\s*[.!]?\s*$", raw)
            name = normalize(tail.group(1)) if tail else ""
            cut = rest.rfind(f" sur {name} ") if name else -1
            if cut >= 0:
                rest, server = rest[:cut] + " ", name
        channel = _name(rest)
        if channel in ("", "moi", "toi"):
            # « connecte-moi en vocal » : votre salon habituel, sinon ORION demande lequel ; « … sur le serveur X » :
            # un salon de ce serveur (jamais un « salon » vide, refusé par l'outil : 5 novembre 2026).
            return "discord_join", ({"serveur": server} if server else {})
        return "discord_join", {"salon": channel, **({"serveur": server} if server else {})}
    return None


QUOTES = re.compile(r"«\s*([^»]+?)\s*»|“\s*([^”]+?)\s*”|\"\s*([^\"]+?)\s*\"")
ALIAS_SET = re.compile(r"^(?:surnomme|renomme)\s+(?:le\s+)?(?:serveur\s+)?(?:discord\s+)?(?P<x>.+?)\s+"
                       r"(?:en|comme|par)\s+(?P<y>.+)$")
ALIAS_GIVE = re.compile(r"^(?:donne|ajoute|mets|met|attribue)\s+(?:le\s+|un\s+)?surnom\s+(?!(?:a|au|aux|pour)\s)"
                        r"(?P<y>.+?)\s+(?:au|a|pour)\s+(?:le\s+)?(?:serveur\s+)?(?:discord\s+)?(?P<x>.+)$")
ALIAS_FORGET = re.compile(r"^(?:oublie|supprime|retire|enleve|efface)\s+(?:le\s+)?surnom\s+(?P<y>.+)$")
ALIAS_LIST = re.compile(r"\b(?:quels? sont (?:mes|les|tes) surnoms|liste (?:des |les |mes )?surnoms|"
                        r"surnoms (?:de|des) (?:mes )?serveurs)\b")


def _alias_plan(raw: str, norm: str, exists: Callable[[str], bool]) -> tuple[str, dict] | None:
    """Surnoms de serveurs. Une demande sans « serveur » ni « Discord » n'en est jamais une (« surnomme Paul en
    Polo » ne touche pas Discord). Les noms entre guillemets sont pris tels qu'écrits."""
    if "surnom" not in norm or not exists("discord_alias"):
        return None
    quoted = [next(g for g in m.groups() if g) for m in QUOTES.finditer(raw)]
    if ALIAS_LIST.search(norm):
        return "discord_aliases", {}
    forget = ALIAS_FORGET.match(norm)
    if forget:
        said = quoted[-1] if quoted else _name(forget.group("y"))
        return ("discord_forget_alias", {"surnom": said}) if said else None
    if not re.search(r"\b(?:serveur|serveurs|discord)\b", norm):
        return None
    for pattern, server_first in ((ALIAS_SET, True), (ALIAS_GIVE, False)):
        match = pattern.match(norm)
        if not match:
            continue
        if len(quoted) >= 2:
            server, nick = (quoted[0], quoted[1]) if server_first else (quoted[1], quoted[0])
        else:
            server, nick = _name(match.group("x")), _name(match.group("y"))
        return ("discord_alias", {"serveur": server, "surnom": nick}) if server and nick else None
    return None


ANSWER_SPLIT = re.compile(r"\s(?:sur|dans)\s(?:(?:le|la|l)\s)?(?:(?:serveur|serv|server)\s)?")
GENERIC_SALONS = {"vocal", "voc", "salon", "discord", "moi", "toi"}


def discord_answer(text: str, previous: dict) -> dict | None:
    """Réponse à une question de « rejoindre un salon » (« Quel salon ? », « Plusieurs salons X : sur A ou sur
    B ? », « Plusieurs serveurs… ») : paramètres de ``discord_join`` complétés, ou None si ce n'est pas une réponse.

    « Général sur Et le Zombie » : salon et serveur ; « sur GTA France » : le serveur ; un nom seul : le serveur si
    la question portait sur le serveur (salon déjà connu), sinon le salon."""
    norm = re.sub(r"^(?:orion|jarvis)\s+", "", normalize(text)).strip()
    if not norm or len(norm.split()) > 12:
        return None
    salon = str(previous.get("salon") or "")
    salon = "" if _name(normalize(salon)) in GENERIC_SALONS or not _name(normalize(salon)) else salon
    serveur = str(previous.get("serveur") or "")
    padded = f" {norm} "
    split = None
    for split in ANSWER_SPLIT.finditer(padded):
        pass
    if split is not None:
        left, right = _name(padded[:split.start()]), _name(padded[split.end():])
        if right:
            salon = left or salon
            return {**({"salon": salon} if salon else {}), "serveur": right}
    name = _name(norm)
    if not name:
        return None
    if salon:
        return {"salon": salon, "serveur": name}  # salon déjà dit : la question portait sur le serveur
    return {"salon": name, **({"serveur": serveur} if serveur else {})}
