"""Traitement des actualités : normalisation, dédoublonnage, regroupement par événement, analyse, score sur 100.

Tout est déterministe ici (le LLM, facultatif, n'intervient que pour départager un regroupement douteux et pour la
synthèse : orion/news/synthesis.py). Les seuils et poids sont des réglages, pas des vérités.

Score = importance × qualité × 100, réduit de 15 % si l'événement n'est pas confirmé.
- Importance (poids par défaut, réglables) : gravité 30 (conflit, attentat, catastrophe… ; crise, grève,
  sanctions…), portée 20 (international, national, nombre de sources indépendantes), conséquences 10 (pénurie,
  coupures, évacuation, hausse des prix…), évolution 10 (nouveau développement depuis la dernière communication).
- Qualité, entre 0,5 et 1 : fiabilité de la meilleure source et nouveauté. Elles modulent l'importance sans la créer :
  une nouvelle anodine, même fraîche et bien sourcée, reste aux archives.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from orion.platform.feeds import canonical_url, domain, text_of
from orion.watch.products import plain

RELIABILITY_RANK = {"blog": 1, "media": 2, "agency": 3, "official": 4}
WEIGHTS = {"gravity": 30.0, "reach": 20.0, "consequences": 10.0, "evolution": 10.0}
THRESHOLDS = {"bulletin": 30, "priority": 50, "alert": 70, "priority_alert": 85}
ARCHIVE, BULLETIN, PRIORITY = "archive", "bulletin", "prioritaire"
ALERT, PRIORITY_ALERT = "alerte", "alerte prioritaire"

GRAVE = ("guerre", "attaque", "attaques", "frappe", "frappes", "missile", "missiles", "explosion", "attentat",
         "seisme", "tremblement de terre", "tsunami", "morts", "tues", "victimes", "nucleaire", "invasion",
         "coup d etat", "effondrement", "pandemie", "etat d urgence", "bombardement", "bombardements", "otages",
         "war", "attack", "strike", "killed", "dead", "earthquake", "invasion", "nuclear")
SERIOUS = ("sanctions", "greve", "greves", "crise", "demission", "election", "elections", "faillite",
           "licenciements", "inflation", "taux", "panne", "piratage", "cyberattaque", "fuite de donnees",
           "manifestation", "manifestations", "recession", "tensions", "exercice militaire", "mobilisation",
           "outage", "breach", "layoffs", "sanction", "tariffs", "droits de douane")
WIDE = ("mondial", "mondiale", "international", "internationale", "otan", "nato", "onu", "union europeenne", "ue",
        "europe", "etats unis", "chine", "russie", "ukraine", "moyen orient", "g7", "g20", "global", "worldwide")
NATIONAL = ("france", "francais", "francaise", "gouvernement", "assemblee nationale", "elysee", "senat",
            "national", "nationale", "premier ministre", "president de la republique")
CONSEQUENCES = ("penurie", "hausse des prix", "coupure", "coupures", "evacuation", "confinement", "rationnement",
                "fermeture", "fermetures", "licenciements", "couvre feu", "blackout", "rupture d approvisionnement",
                "shortage", "evacuation", "lockdown")
STOPWORDS = set("""le la les un une des du de d l et ou a au aux en dans sur pour par avec sans ce cette ces son sa ses
leur leurs qui que quoi dont est sont ete etre a ont avait plus moins tres apres avant selon contre entre vers chez
il elle ils elles on nous vous se ne pas n y the a an of to in on for with by from at is are was were be been has have
as it its this that new nouveau nouvelle nouveaux""".split())
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Article:
    id: str
    source: str
    category: str
    reliability: str
    title: str
    summary: str
    url: str
    domain: str
    published_at: float
    fetched_at: float


def article_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def normalize(source: str, category: str, reliability: str, title: str, summary: str, url: str,
              published_at: float | None, fetched_at: float) -> Article | None:
    title = text_of(title, 300)
    if not title or not url.startswith(("http://", "https://")):
        return None
    published = published_at if published_at is not None and published_at <= fetched_at + 3600 else fetched_at
    return Article(article_id(url), source, category, reliability if reliability in RELIABILITY_RANK else "blog",
                   title, text_of(summary, 1500), canonical_url(url), domain(url), published, fetched_at)


def tokens(text: str) -> set[str]:
    return {w for w in plain(text).split() if len(w) > 2 and w not in STOPWORDS and not w.isdigit()}


def entities(title: str) -> set[str]:
    """Noms propres d'un titre (lieux, personnes, organisations : mots à majuscule hors début de phrase) ; « Séisme à
    Lyon » et « Séisme à Marseille » ne sont pas le même événement malgré leurs mots communs."""
    found, start = set(), True
    for raw in re.findall(r"[\w'’-]+|[.:;!?]", title):
        if raw in ".:;!?":
            start = start or raw in ".!?"  # après « : », une majuscule reste un nom (« Paris : l'ONU… »)
            continue
        part = raw.split("'")[-1].split("’")[-1]  # « l'ONU » : ONU
        word = plain(part)
        if not start and part[:1].isupper() and len(word) > 1 and word not in STOPWORDS:
            found.add(word)
        start = False
    return found


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in NUMBER.findall(text)}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def same_names(story: "Story", title: str) -> bool:
    """Faux si les deux nomment des lieux ou acteurs, tous différents (sinon, rien ne s'y oppose)."""
    names = entities(title)
    return not (names and story.names) or bool(names & story.names)


@dataclass
class Story:
    id: str
    category: str
    title: str
    tokens: set[str]
    first_seen: float
    updated_at: float
    articles: list[str] = field(default_factory=list)  # identifiants d'articles
    domains: dict[str, str] = field(default_factory=dict)  # domaine -> fiabilité
    facts: set[str] = field(default_factory=set)  # mots et nombres connus (pour distinguer évolution et répétition)
    names: set[str] = field(default_factory=set)  # noms propres des titres (lieux, acteurs)
    communicated_facts: set[str] = field(default_factory=set)
    status: str = "non confirmé"
    score: int = 0
    tier: str = ARCHIVE
    components: dict = field(default_factory=dict)
    development: bool = False  # nouveau depuis la dernière communication
    alerted_score: int = 0  # score lors de la dernière alerte (0 : jamais)
    alerted_status: str = ""
    bulletin_facts: set[str] = field(default_factory=set)  # ce que le dernier bulletin a dit

    def as_dict(self) -> dict:
        return {"id": self.id, "category": self.category, "title": self.title, "tokens": sorted(self.tokens),
                "first_seen": self.first_seen, "updated_at": self.updated_at, "articles": self.articles,
                "names": sorted(self.names),
                "domains": self.domains, "facts": sorted(self.facts), "communicated_facts":
                sorted(self.communicated_facts), "status": self.status, "score": self.score, "tier": self.tier,
                "components": self.components, "development": self.development, "alerted_score": self.alerted_score,
                "alerted_status": self.alerted_status, "bulletin_facts": sorted(self.bulletin_facts)}

    @classmethod
    def from_dict(cls, d: dict) -> "Story":
        sets = ("tokens", "facts", "communicated_facts", "bulletin_facts", "names")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: (set(v) if k in sets else v) for k, v in d.items() if k in known})

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmé"


def facts_of(article: Article) -> set[str]:
    return tokens(f"{article.title} {article.summary}") | {f"#{n}" for n in numbers(f"{article.title} "
                                                                                     f"{article.summary}")}


def attach(story: Story, article: Article) -> bool:
    """Ajoute l'article ; rend True s'il apporte un développement (assez de faits nouveaux), False si c'est une
    répétition médiatique (mêmes faits, autre média)."""
    new_facts = facts_of(article) - story.facts
    story.articles.append(article.id)
    story.domains[article.domain] = max(story.domains.get(article.domain, "blog"), article.reliability,
                                        key=lambda r: RELIABILITY_RANK.get(r, 0))
    story.tokens |= tokens(article.title)
    story.names |= entities(article.title)
    story.facts |= facts_of(article)
    story.updated_at = max(story.updated_at, article.fetched_at)
    developing = len([f for f in new_facts if f.startswith("#")]) >= 1 or len(new_facts) >= 6
    return developing


def analyze(story: Story) -> None:
    """Confirmé : une source officielle, ou au moins deux sources indépendantes fiables (agence, média)."""
    reliable = [r for r in story.domains.values() if RELIABILITY_RANK.get(r, 0) >= 2]
    story.status = "confirmé" if "official" in story.domains.values() or len(reliable) >= 2 else "non confirmé"


def _has(text: str, words: tuple[str, ...]) -> bool:
    padded = f" {text} "
    return any(f" {w} " in padded for w in words)


def score(story: Story, texts: str, now: float, weights: dict | None = None, thresholds: dict | None = None) -> None:
    w = {**WEIGHTS, **{k: float(v) for k, v in (weights or {}).items() if k in WEIGHTS}}
    t = {**THRESHOLDS, **{k: int(v) for k, v in (thresholds or {}).items() if k in THRESHOLDS}}
    text = plain(texts)
    parts = {
        "gravity": 1.0 if _has(text, GRAVE) else 0.5 if _has(text, SERIOUS) else 0.0,
        "reach": max(0.8 if _has(text, WIDE) else 0.6 if _has(text, NATIONAL) else 0.15,
                     min(1.0, len(story.domains) / 5)),
        "consequences": 1.0 if _has(text, CONSEQUENCES) else 0.0,
        "evolution": 1.0 if story.development else 0.0,
    }
    importance = sum(w[k] * v for k, v in parts.items()) / max(1e-6, sum(w.values()))
    reliability = max((RELIABILITY_RANK.get(r, 0) for r in story.domains.values()), default=0) / 4
    novelty = max(0.0, 1 - (now - story.first_seen) / 86400)
    parts |= {"reliability": reliability, "novelty": novelty}
    value = 100 * importance * (0.5 + 0.25 * reliability + 0.25 * novelty)
    if not story.confirmed:
        value *= 0.85
    story.score = int(round(min(100.0, value)))
    story.components = {k: round(v, 2) for k, v in parts.items()}
    story.tier = (PRIORITY_ALERT if story.score >= t["priority_alert"] else ALERT if story.score >= t["alert"]
                  else PRIORITY if story.score >= t["priority"] else BULLETIN if story.score >= t["bulletin"]
                  else ARCHIVE)
