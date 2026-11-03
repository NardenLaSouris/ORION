"""Journal quotidien de NEWS (lot P1, docs/v3/news_personal_intelligence.md § 4) : sans aucune donnée personnelle.

Composition, déterministe (le LLM ne sert qu'aux résumés, vérifiés par ``synthesis.summarize``) :
1. Candidats : événements mis à jour depuis le précédent journal (24 h au plus), au-dessus du seuil « bulletin ».
2. Exclus : déjà donnés sans fait nouveau (doublons) ; titres trompeurs (``misleading``).
3. « À ne pas manquer » (niveau alerte) : toujours repris, même au-delà des quotas.
4. Sélection : 10 au plus, 3 au plus par rubrique, au moins une information « International » et une « Économie »
   quand il y en a. Jusqu'à 5 si l'actualité importante est maigre, avec des sujets moins importants (seuil moitié).
5. Répartition 60 % actualité générale / 40 % vos intérêts : tant que le moteur d'intérêts n'existe pas (P3), toutes
   les places vont à l'actualité générale ; la rubrique « Vos sujets » reste vide. Le journal n'invente pas d'intérêts.

Chaque fiche : titre (celui de l'article le plus fiable, jamais réécrit), résumé vérifié, date de publication, source
et lien, fiabilité (officielle, confirmée par plusieurs sources, source unique non confirmée) et pourquoi elle est là.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orion.news import pipeline as pl
from orion.watch.products import plain

RUBRICS = {"essentiel": "À ne pas manquer", "vos_sujets": "Vos sujets", "evolutions": "Évolutions des sujets suivis",
           "tech_sciences": "Technologie et sciences", "international": "International", "economie": "Économie",
           "france": "France"}
RUBRIC_OF = {"geopolitique": "international", "defense": "international", "tech_ia": "tech_sciences",
             "sciences": "tech_sciences", "economie": "economie", "france": "france"}
REQUIRED = ("international", "economie")
MIN_ITEMS, MAX_ITEMS, PER_RUBRIC = 5, 10, 3
GENERAL_SHARE = 0.6  # décision du 2 novembre ; sans moteur d'intérêts, tout va à l'actualité générale
WHY = {"essentiel": "À ne pas manquer : actualité majeure.",
       "evolutions": "Évolution d'un sujet donné dans le précédent journal.",
       "general": "Actualité majeure du jour.", "filler": "Actualité du jour, moins importante."}

# Titres trompeurs : marqueurs de titre à sensation (comparés sans accents ni majuscules).
SENSATIONAL = ("vous n en reviendrez pas", "vous n allez pas croire", "incroyable", "hallucinant", "choc", "scandale",
               "ce que personne ne", "la verite sur", "on vous explique tout", "les raisons qui", "voici pourquoi",
               "ce qui va changer pour vous", "a couper le souffle", "terrifiant", "stupefiant", "fou rire",
               "buzz", "polemique", "clash", "revelations", "exclusif", "urgent")


@dataclass
class Pick:
    story: pl.Story
    articles: list[pl.Article]
    rubric: str
    why: str
    evolution: bool = False


@dataclass
class Plan:
    picks: list[Pick] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)  # identifiant d'événement -> raison


def misleading(story: pl.Story, articles: list[pl.Article]) -> str:
    """Raison d'écarter un titre trompeur, ou "". Règles seules : un titre à sensation sans confirmation, ou un
    chiffre du titre qu'aucun texte des articles ne donne."""
    if not articles:
        return ""
    lead = best_article(articles)
    title = lead.title
    flat = f" {plain(title)} "
    loud = (title.count("!") >= 2 or bool(re.search(r"\b[A-ZÀ-Ý]{5,}\b(?:\s+\b[A-ZÀ-Ý]{3,}\b)+", title))
            or any(f" {marker} " in flat for marker in SENSATIONAL))
    if loud and not story.confirmed:
        return "titre à sensation, sans source qui le confirme"
    body = " ".join(f"{a.summary} {a.title}" for a in articles if a is not lead) + " " + lead.summary
    missing = claims(title) - pl.numbers(body)
    if missing and lead.summary and not story.confirmed:
        return f"chiffre du titre absent de l'article ({', '.join(sorted(missing))})"
    return ""


# Chiffre qui porte une affirmation (« 12 % », « 300 morts », « 5 milliards ») : les dates et les années, que les
# résumés RSS courts omettent souvent, ne comptent pas (constaté sur les vrais flux le 3 novembre 2026).
CLAIM = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|€|\$|pour ?cent|morts?|tués?|blessés?|victimes?|millions?|milliards?"
                   r"|fois|euros?|dollars?)", re.IGNORECASE)


def claims(text: str) -> set[str]:
    return {n.replace(",", ".") for n in CLAIM.findall(text)}


def subject(title: str) -> str:
    """Sujet annoncé avant les deux-points (« Midterms : … »), sans pictogramme ni mention de rubrique."""
    head, sep, _ = title.partition(":")
    if not sep:
        return ""
    words = [w for w in plain(head).split() if w.isalpha() and w not in ("direct", "reportage", "enquete", "info",
                                                                          "video")]
    return " ".join(words) if 0 < len(words) <= 4 else ""


def same_subject(a: pl.Story, b: pl.Story) -> bool:
    """Deux événements que le regroupement a séparés mais qui traitent du même sujet le même jour : même sujet annoncé
    et deux mots significatifs en commun au moins, ou des titres très proches."""
    shared = len(a.tokens & b.tokens)
    head = subject(a.title)
    return (bool(head) and head == subject(b.title) and shared >= 2) or pl.jaccard(a.tokens, b.tokens) >= 0.5


def best_article(articles: list[pl.Article]) -> pl.Article:
    """L'article de la source la plus fiable, le plus ancien à fiabilité égale (celui qui a donné l'information)."""
    return min(articles, key=lambda a: (-pl.RELIABILITY_RANK.get(a.reliability, 0), a.published_at))


def reliability(story: pl.Story) -> tuple[str, str]:
    """(niveau, libellé) : officielle, confirmée, ou source unique non confirmée."""
    if "official" in story.domains.values():
        return "officielle", "Source officielle"
    if story.confirmed:
        return "confirmée", f"Confirmée par {len(story.domains)} sources indépendantes"
    if len(story.domains) <= 1:
        return "non confirmée", "Source unique, non confirmée"
    return "non confirmée", f"{len(story.domains)} sources peu fiables, non confirmée"


def plan(stories: list[pl.Story], articles_of, *, floor: int, previous: set[str] = frozenset(),
         categories: set[str] | None = None) -> Plan:
    """``previous`` : événements du précédent journal. ``articles_of(story)`` : ses articles."""
    result = Plan()
    essential, evolutions, general, filler = [], [], [], []
    for story in sorted(stories, key=lambda s: -s.score):
        if categories is not None and story.category not in categories:
            continue
        if story.score < floor // 2:
            continue
        articles = articles_of(story)
        if not articles:
            continue
        if story.id in previous and not (story.facts - story.journal_facts):
            result.excluded[story.id] = "déjà dans le précédent journal, rien de nouveau"
            continue
        reason = misleading(story, articles)
        if reason:
            result.excluded[story.id] = reason
            continue
        if story.id in previous:
            evolutions.append(Pick(story, articles, "evolutions", WHY["evolutions"], True))
        elif story.tier in (pl.ALERT, pl.PRIORITY_ALERT):
            essential.append(Pick(story, articles, "essentiel", WHY["essentiel"]))
        elif story.score >= floor:
            general.append(Pick(story, articles, RUBRIC_OF.get(story.category, "international"), WHY["general"]))
        else:
            filler.append(Pick(story, articles, RUBRIC_OF.get(story.category, "international"), WHY["filler"]))
    picks: list[Pick] = []
    for pick in essential:  # toujours, même au-delà des quotas ; une seule place par sujet
        twin = next((p for p in picks if same_subject(p.story, pick.story)), None)
        if twin is None:
            picks.append(pick)
        else:
            result.excluded[pick.story.id] = f"même sujet que « {twin.story.title[:60]} »"
    counts: dict[str, int] = {}

    def take(pick: Pick) -> bool:
        if len(picks) >= max(MAX_ITEMS, len(essential)) or counts.get(pick.rubric, 0) >= PER_RUBRIC \
                or any(p.story.id == pick.story.id for p in picks):
            return False
        twin = next((p for p in picks if same_subject(p.story, pick.story)), None)
        if twin is not None:  # une seule place par sujet : la plus importante, déjà prise
            result.excluded[pick.story.id] = f"même sujet que « {twin.story.title[:60]} »"
            return False
        picks.append(pick)
        counts[pick.rubric] = counts.get(pick.rubric, 0) + 1
        return True

    for rubric in REQUIRED:  # diversité : une internationale et une économie quand il y en a
        if not any(p.rubric == rubric for p in picks):
            next((take(p) for p in general if p.rubric == rubric), None)
    for pick in evolutions + general:
        take(pick)
    for pick in filler:  # actualité maigre : jusqu'à 5, jamais au-delà, avec des sujets moins importants
        if len(picks) >= MIN_ITEMS:
            break
        take(pick)
    order = list(RUBRICS)
    result.picks = sorted(picks, key=lambda p: (order.index(p.rubric), -p.story.score))
    return result


def card(number: int, pick: Pick, summary: dict) -> dict:
    """Fiche d'une information (rien d'autre que les articles et la synthèse vérifiée)."""
    lead = best_article(pick.articles)
    level, label = reliability(pick.story)
    links, domains = [], set()
    for a in sorted(pick.articles, key=lambda a: (-pl.RELIABILITY_RANK.get(a.reliability, 0), a.published_at)):
        if a.domain not in domains and len(links) < 4:
            domains.add(a.domain)
            links.append({"source": a.source, "url": a.url})
    return {"number": number, "story": pick.story.id, "rubric": pick.rubric, "category": pick.story.category,
            "title": lead.title, "summary": summary.get("summary") or lead.summary[:400],
            "summary_by": summary.get("by", "règles") if summary.get("summary") else "source",
            "confirmed": summary.get("confirmed", []), "uncertain": summary.get("uncertain", []),
            "published_at": min(a.published_at for a in pick.articles), "source": lead.source, "url": lead.url,
            "links": links, "reliability": level, "reliability_label": label, "why": pick.why,
            "evolution": pick.evolution, "score": pick.story.score}


def spoken(items: list[dict], when: str) -> str:
    """Version dite : le nombre d'informations, cinq titres, puis l'invitation à en savoir plus."""
    if not items:
        return f"Journal {when} : peu d'actualité importante depuis le précédent."
    heads = " ".join(f"{i['number']}. {i['title']}." for i in items[:5])
    more = f" Et {len(items) - 5} autre(s) dans la version écrite." if len(items) > 5 else ""
    return (f"Journal {when} : {len(items)} information{'s' if len(items) > 1 else ''}. {heads}{more} "
            "Voulez-vous des détails sur l'une d'elles ?")


def detail(item: dict) -> str:
    """« Approfondis la deuxième » : tout ce que la fiche contient, rien de plus."""
    parts = [f"{item['title']}. {item['reliability_label']}."]
    if item.get("summary"):
        parts.append(item["summary"])
    if item.get("confirmed"):
        parts.append("Faits confirmés : " + " ; ".join(item["confirmed"]) + ".")
    if item.get("uncertain"):
        parts.append("Encore incertain : " + " ; ".join(item["uncertain"]) + ".")
    sources = ", ".join(link["source"] for link in item.get("links", [])) or item.get("source", "")
    parts.append(f"Sources : {sources}.")
    return " ".join(parts)
