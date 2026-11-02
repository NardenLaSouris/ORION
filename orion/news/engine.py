"""ORION NEWS : veille d'actualités, bulletins programmés et alertes spontanées.

Collecte -> normalisation -> dédoublonnage -> regroupement par événement -> analyse -> score -> synthèse ->
distribution (orion/news/pipeline.py, orion/news/synthesis.py).

- Bulletins aux heures réglées (08:00 et 20:00 par défaut, modifiables dans ORION Control) : les événements au-dessus
  du seuil « bulletin » nouveaux ou qui ont évolué depuis le précédent ; rien n'est ajouté pour remplir.
- Alertes spontanées : un événement au-dessus du seuil « alerte » est signalé une fois ; de nouveau seulement s'il
  évolue vraiment (nouveaux faits et score en hausse) ou s'il vient d'être confirmé, jamais pour une simple répétition
  médiatique. Appels : au plus 3 par jour et 15 minutes d'écart (budget propre à NEWS, en plus de celui des alertes),
  jamais pendant les heures calmes.
- Katana indisponible : collecte, regroupement et score continuent ; synthèses simples (titres, statut, sources).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from orion.platform.alerts import CALL, Alert, AlertRouter, Level
from orion.platform.feeds import Fetcher, SourceError, fetch_feed, http_get
from orion.platform.history import History
from orion.platform.intelligence import StructuredLLM
from orion.platform.limits import RateLimiter
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.scheduler import Scheduler
from orion.platform.store import Migration, Store, dumps, loads
from orion.news import pipeline as pl
from orion.news.synthesis import CATEGORY_LABELS, compose, summarize
from orion.watch.connectors import ITEMS, Connector, Item

log = logging.getLogger(__name__)

MAX_AGE = 48 * 3600  # un article plus ancien n'est pas une actualité
CLUSTER_WINDOW = 48 * 3600
CLUSTER_MIN, CLUSTER_ASK_LLM = 0.30, 0.15
MAX_BULLETIN_ITEMS = 8
EVOLUTION_ALERT_DELTA = 10

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE news_articles (
            id TEXT PRIMARY KEY, story_id TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL,
            reliability TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, url TEXT NOT NULL,
            domain TEXT NOT NULL, published_at REAL NOT NULL, fetched_at REAL NOT NULL
        );
        CREATE INDEX news_articles_story ON news_articles (story_id);
        CREATE TABLE news_stories (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, score INTEGER NOT NULL, tier TEXT NOT NULL,
            updated_at REAL NOT NULL, data TEXT NOT NULL
        );
        CREATE INDEX news_stories_updated ON news_stories (updated_at);
        CREATE TABLE news_bulletins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, slot TEXT NOT NULL, day TEXT NOT NULL,
            text TEXT NOT NULL, short TEXT NOT NULL, stories TEXT NOT NULL, delivered TEXT NOT NULL
        );
        CREATE TABLE news_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE news_sources (
            name TEXT PRIMARY KEY, last_at REAL, ok INTEGER, error TEXT, articles INTEGER NOT NULL DEFAULT 0
        )
    """),
    # Sujets du bulletin, structurés pour la mise en page (orion/news/page.py).
    Migration(2, "ALTER TABLE news_bulletins ADD COLUMN entries TEXT NOT NULL DEFAULT '[]'"),
)
PAGE_STALE = 6 * 3600  # bulletin plus ancien : un nouveau est composé (aperçu, jamais compté comme envoyé)


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: str
    reliability: str = "media"


@dataclass
class NewsSettings:
    bulletin_times: tuple[str, ...] = ("08:00", "20:00")
    categories: tuple[str, ...] = tuple(CATEGORY_LABELS)
    thresholds: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    max_calls_per_day: int = 3
    min_call_interval_minutes: float = 15.0
    alerts_enabled: bool = True
    bulletins_enabled: bool = True
    collect_minutes: float = 30.0


def _time(text: Any) -> str:
    if not isinstance(text, str):
        raise ValueError("heure attendue (« 08:00 »)")
    hours, _, minutes = text.partition(":")
    if not (hours.isdigit() and minutes.isdigit() and 0 <= int(hours) < 24 and 0 <= int(minutes) < 60):
        raise ValueError(f"heure invalide : {text!r}")
    return f"{int(hours):02d}:{int(minutes):02d}"


class NewsEngine(BaseModule):
    name, title = "news", "NEWS"

    def __init__(self, store: Store, feeds: list[Feed], *, alerts: AlertRouter | None = None,
                 scheduler: Scheduler | None = None, history: History | None = None, llm: StructuredLLM | None = None,
                 settings: NewsSettings | None = None, fetcher: Fetcher = http_get,
                 clock: Callable[[], float] = time.time, now: Callable[[], datetime] | None = None):
        self._store, self.feeds, self._alerts, self._llm = store, list(feeds), alerts, llm
        self._defaults = settings or NewsSettings()
        self._fetcher, self._clock = fetcher, clock
        self._now = now or (lambda: datetime.fromtimestamp(self._clock()))
        self._scheduler = scheduler or Scheduler(clock)
        self._history = history or History(store, clock)
        store.migrate("news", MIGRATIONS)
        s = self.settings
        self._calls = RateLimiter(max(1, s.max_calls_per_day), 86400, s.min_call_interval_minutes * 60, clock=clock)

    # --- Réglages (ORION Control) ----------------------------------------------------------------------------

    @property
    def settings(self) -> NewsSettings:
        saved = {r["key"]: loads(r["value"]) for r in self._store.query("SELECT key, value FROM news_settings")}
        d = self._defaults
        return NewsSettings(tuple(saved.get("bulletin_times", d.bulletin_times)),
                            tuple(saved.get("categories", d.categories)), {**d.thresholds,
                                                                           **saved.get("thresholds", {})},
                            dict(d.weights), int(saved.get("max_calls_per_day", d.max_calls_per_day)),
                            float(saved.get("min_call_interval_minutes", d.min_call_interval_minutes)),
                            bool(saved.get("alerts_enabled", d.alerts_enabled)),
                            bool(saved.get("bulletins_enabled", d.bulletins_enabled)), d.collect_minutes)

    def update_settings(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("réglages attendus")
        clean: dict[str, Any] = {}
        if "bulletin_times" in data:
            times = data["bulletin_times"]
            if not isinstance(times, list) or len(times) > 6:
                raise ValueError("au plus 6 heures de bulletin")
            clean["bulletin_times"] = sorted({_time(t) for t in times})
        if "categories" in data:
            cats = data["categories"]
            if not isinstance(cats, list) or not set(cats) <= set(CATEGORY_LABELS):
                raise ValueError(f"catégories parmi {', '.join(CATEGORY_LABELS)}")
            clean["categories"] = list(dict.fromkeys(cats))
        if "thresholds" in data:
            t = {**pl.THRESHOLDS, **self.settings.thresholds, **(data["thresholds"] or {})}
            if set(t) != set(pl.THRESHOLDS) or not all(isinstance(v, int) and 0 <= v <= 100 for v in t.values()) \
                    or not t["bulletin"] <= t["priority"] <= t["alert"] <= t["priority_alert"]:
                raise ValueError("seuils entiers de 0 à 100, croissants (bulletin ≤ prioritaire ≤ alerte ≤ "
                                 "alerte prioritaire)")
            clean["thresholds"] = t
        if "max_calls_per_day" in data:
            value = data["max_calls_per_day"]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10:
                raise ValueError("appels par jour : de 0 à 10")
            clean["max_calls_per_day"] = value
        if "min_call_interval_minutes" in data:
            value = data["min_call_interval_minutes"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 5 <= value <= 1440:
                raise ValueError("intervalle entre deux appels : de 5 à 1440 minutes")
            clean["min_call_interval_minutes"] = float(value)
        for flag in ("alerts_enabled", "bulletins_enabled"):
            if flag in data:
                if not isinstance(data[flag], bool):
                    raise ValueError(f"{flag} : vrai ou faux")
                clean[flag] = data[flag]
        with self._store.transaction():
            for key, value in clean.items():
                self._store.write("INSERT INTO news_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                                  "SET value = excluded.value", (key, dumps(value)))
        s = self.settings
        self._calls = RateLimiter(max(1, s.max_calls_per_day), 86400, s.min_call_interval_minutes * 60,
                                  clock=self._clock)
        self._restore_calls()
        self._history.record("news", "", "réglages", clean)
        return self.settings.__dict__

    def _restore_calls(self) -> None:
        for row in self._alerts.history(200, "news") if self._alerts is not None else []:
            if CALL in row["channels"] and self._clock() - row["at"] < 86400:
                self._calls.use("", row["at"])

    # --- Cycle de vie ----------------------------------------------------------------------------------------

    def start(self) -> None:
        self._restore_calls()
        self._scheduler.add("news:collect", self.settings.collect_minutes * 60, self.collect)
        self._scheduler.add("news:bulletins", 60, self.bulletin_due)

    def stop(self) -> None:
        self._scheduler.remove("news:collect")
        self._scheduler.remove("news:bulletins")

    # --- Collecte et traitement ------------------------------------------------------------------------------

    def collect(self) -> dict:
        now = self._clock()
        categories = set(self.settings.categories)
        report = {"articles": 0, "nouveaux": 0, "sources_en_échec": []}
        touched: set[str] = set()
        for feed in self.feeds:
            if feed.category not in categories:
                continue
            try:
                entries = fetch_feed(feed.url, self._fetcher)
            except SourceError as exc:
                self._source_status(feed.name, now, False, str(exc), 0)
                report["sources_en_échec"].append(feed.name)
                continue
            new = 0
            for entry in entries:
                article = pl.normalize(feed.name, feed.category, feed.reliability, entry.title, entry.summary,
                                       entry.url, entry.published_at, now)
                if article is None or now - article.published_at > MAX_AGE:
                    continue
                report["articles"] += 1
                story = self._ingest(article)
                if story is not None:
                    new += 1
                    touched.add(story.id)
            self._source_status(feed.name, now, True, "", new)
            report["nouveaux"] += new
        for story_id in touched:
            story = self._story(story_id)
            if story is not None:
                self._evaluate(story, now)
        if self.settings.alerts_enabled:
            for story_id in touched:
                story = self._story(story_id)
                if story is not None:
                    self._maybe_alert(story)
        return report

    def _source_status(self, name: str, at: float, ok: bool, error: str, articles: int) -> None:
        self._store.write("INSERT INTO news_sources (name, last_at, ok, error, articles) VALUES (?, ?, ?, ?, ?) "
                          "ON CONFLICT(name) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, "
                          "error = excluded.error, articles = news_sources.articles + excluded.articles",
                          (name, at, int(ok), error[:200], articles))

    def _ingest(self, article: pl.Article) -> pl.Story | None:
        """Article nouveau -> son événement (existant ou créé) ; None si déjà connu (même adresse ou même titre)."""
        if self._store.one("SELECT 1 FROM news_articles WHERE id = ? OR (title = ? AND fetched_at > ?)",
                           (article.id, article.title, article.fetched_at - CLUSTER_WINDOW)):
            return None
        story = self._cluster(article)
        developing = pl.attach(story, article)
        if len(story.articles) > 1 and developing and story.facts - story.communicated_facts:
            story.development = True
        with self._store.transaction():
            self._store.write("INSERT INTO news_articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (article.id, story.id, article.source, article.category, article.reliability,
                               article.title, article.summary, article.url, article.domain, article.published_at,
                               article.fetched_at))
            self._save(story)
        return story

    def _cluster(self, article: pl.Article) -> pl.Story:
        words = pl.tokens(article.title)
        rows = self._store.query("SELECT data FROM news_stories WHERE category = ? AND updated_at > ?",
                                 (article.category, article.fetched_at - CLUSTER_WINDOW))
        best, best_score = None, 0.0
        for row in rows:
            story = pl.Story.from_dict(loads(row["data"], {}))
            if not pl.same_names(story, article.title):
                continue  # autres lieux, autres acteurs : un autre événement
            similarity = pl.jaccard(words, story.tokens)
            if similarity > best_score:
                best, best_score = story, similarity
        if best is not None and (best_score >= CLUSTER_MIN or (best_score >= CLUSTER_ASK_LLM
                                                               and self._same_event(best, article))):
            return best
        return pl.Story(article.id, article.category, article.title, set(), article.fetched_at, article.fetched_at)

    def _same_event(self, story: pl.Story, article: pl.Article) -> bool:
        if self._llm is None or not self._llm.usable:
            return False
        answer = self._llm.ask("Ces deux titres décrivent-ils le même événement d'actualité ?",
                               {"type": "object", "required": ["same"], "additionalProperties": False,
                                "properties": {"same": {"type": "boolean"}}},
                               {"evenement": story.title, "article": article.title})
        return bool(answer and answer["same"])

    def _articles(self, story: pl.Story) -> list[pl.Article]:
        rows = self._store.query("SELECT * FROM news_articles WHERE story_id = ? ORDER BY published_at", (story.id,))
        articles = []
        for row in rows:
            row.pop("story_id", None)
            articles.append(pl.Article(**row))
        return articles

    def _evaluate(self, story: pl.Story, now: float) -> None:
        pl.analyze(story)
        texts = " ".join(f"{a.title} {a.summary}" for a in self._articles(story))
        s = self.settings
        pl.score(story, texts, now, s.weights, s.thresholds)
        self._save(story)

    def _save(self, story: pl.Story) -> None:
        self._store.write("INSERT INTO news_stories (id, category, score, tier, updated_at, data) VALUES "
                          "(?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET score = excluded.score, tier = "
                          "excluded.tier, updated_at = excluded.updated_at, data = excluded.data",
                          (story.id, story.category, story.score, story.tier, story.updated_at,
                           dumps(story.as_dict())))

    def _story(self, story_id: str) -> pl.Story | None:
        row = self._store.one("SELECT data FROM news_stories WHERE id = ?", (story_id,))
        return pl.Story.from_dict(loads(row["data"], {})) if row else None

    # --- Alertes spontanées ----------------------------------------------------------------------------------

    def _maybe_alert(self, story: pl.Story) -> None:
        if story.tier not in (pl.ALERT, pl.PRIORITY_ALERT):
            return
        first = story.alerted_score == 0
        newly_confirmed = story.confirmed and story.alerted_status != "confirmé" and not first
        evolved = story.development and story.score >= story.alerted_score + EVOLUTION_ALERT_DELTA
        if not (first or newly_confirmed or evolved):
            return  # répétition médiatique : rien de nouveau à dire
        articles = self._articles(story)
        summary = summarize(story, articles, self._llm)
        prefix = "Alerte" if first else "Confirmation" if newly_confirmed else "Évolution"
        status = "confirmé" if story.confirmed else "non confirmé à ce stade"
        message = f"{prefix} : {story.title} ({status}). " + (f"{summary['summary']} " if summary["summary"] else "") \
            + f"Sources : {summary['sources']}."
        level = Level.CRITICAL if story.tier == pl.PRIORITY_ALERT else Level.HIGH
        refusal = self._calls.refusal()
        delivery = None
        if self._alerts is not None:
            delivery = self._alerts.deliver(Alert("news", f"{prefix} : {story.title[:90]}", message, level,
                                                  key=f"{story.id}:{prefix}:{story.score // 5}",
                                                  call_allowed=not refusal,
                                                  details={"story": story.id, "score": story.score}))
            if CALL in delivery.channels:
                self._calls.use()
        story.alerted_score, story.alerted_status = story.score, story.status
        story.communicated_facts |= story.facts
        story.development = False
        self._save(story)
        self._history.record("news", story.id, "alerte", {"titre": story.title, "score": story.score,
                                                          "niveau": story.tier, "type": prefix,
                                                          "appel_refusé": refusal or None,
                                                          "canaux": list(delivery.channels) if delivery else []})

    # --- Bulletins -------------------------------------------------------------------------------------------

    def bulletin_due(self) -> int | None:
        """Appelé chaque minute : publie le bulletin d'une heure réglée passée aujourd'hui et pas encore faite
        (après un redémarrage, le bulletin manqué de moins de 2 h est publié, jamais en double)."""
        s = self.settings
        if not s.bulletins_enabled:
            return None
        now = self._now()
        day = now.strftime("%Y-%m-%d")
        for slot in s.bulletin_times:
            hours, minutes = map(int, slot.split(":"))
            at = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
            if not at <= now < at + timedelta(hours=2):
                continue
            if self._store.one("SELECT 1 FROM news_bulletins WHERE day = ? AND slot = ?", (day, slot)):
                continue
            return self.bulletin(slot, deliver=True)
        return None

    def _previous_bulletin_at(self) -> float:
        row = self._store.one("SELECT MAX(at) AS at FROM news_bulletins WHERE delivered != '\"aperçu\"'")
        return row["at"] if row and row["at"] else self._clock() - 86400

    def bulletin(self, slot: str | None = None, deliver: bool = False) -> int:
        now = self._clock()
        since = self._previous_bulletin_at()
        s = self.settings
        floor = {**pl.THRESHOLDS, **s.thresholds}["bulletin"]
        rows = self._store.query("SELECT data FROM news_stories WHERE updated_at > ? AND score >= ? "
                                 "ORDER BY score DESC", (since, floor))
        stories = [pl.Story.from_dict(loads(r["data"], {})) for r in rows]
        stories = [st for st in stories if st.category in s.categories and st.facts - st.bulletin_facts]
        entries, laid_out = [], []
        for story in stories[:MAX_BULLETIN_ITEMS]:
            evolution = bool(story.bulletin_facts)
            articles = self._articles(story)
            summary = summarize(story, articles, self._llm)
            entries.append((summary, story.category, evolution))
            links, domains = [], set()
            for a in articles:
                if a.domain not in domains and len(links) < 4:
                    domains.add(a.domain)
                    links.append({"source": a.source, "url": a.url})
            laid_out.append({**summary, "category": story.category, "evolution": evolution, "links": links})
        slot = slot or datetime.fromtimestamp(now).strftime("%H:%M")
        text, short = compose(entries, slot, now)
        delivered: Any = "aperçu"
        if deliver:
            delivered = []
            if self._alerts is not None:
                d = self._alerts.deliver(Alert("news", f"Bulletin de {slot}", short, Level.NORMAL,
                                               key=f"bulletin:{datetime.fromtimestamp(now):%Y-%m-%d}:{slot}",
                                               call_allowed=False))
                delivered = list(d.channels)
            for story in stories[:MAX_BULLETIN_ITEMS]:
                story.bulletin_facts = set(story.facts)
                story.communicated_facts |= story.facts
                self._save(story)
        bulletin_id = self._store.write(
            "INSERT INTO news_bulletins (at, slot, day, text, short, stories, delivered, entries) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, slot, datetime.fromtimestamp(now).strftime("%Y-%m-%d") if deliver else "", text, short,
             dumps([st.id for st in stories[:MAX_BULLETIN_ITEMS]]), dumps(delivered), dumps(laid_out)))
        if deliver:
            self._history.record("news", str(bulletin_id), "bulletin", {"créneau": slot, "sujets": len(entries),
                                                                        "canaux": delivered})
        return bulletin_id

    def latest_bulletin(self, delivered_only: bool = False) -> dict | None:
        """Dernier bulletin ; ``delivered_only`` : seulement ceux réellement envoyés (pas les aperçus)."""
        where = " WHERE delivered != '\"aperçu\"'" if delivered_only else ""
        row = self._store.one(f"SELECT * FROM news_bulletins{where} ORDER BY at DESC, id DESC LIMIT 1")
        return self._bulletin_row(row) if row else None

    @staticmethod
    def _bulletin_row(row: dict) -> dict:
        return {**row, "stories": loads(row["stories"], []), "delivered": loads(row["delivered"], []),
                "entries": loads(row.get("entries"), [])}

    def page(self, fresh: bool = True) -> tuple[dict, str]:
        """Dernier bulletin mis en page (HTML) ; s'il date de plus de 6 h (ou n'existe pas), un aperçu est composé."""
        from orion.news.page import render_html

        latest = self.latest_bulletin()
        if fresh and (latest is None or self._clock() - latest["at"] > PAGE_STALE):
            self.bulletin(deliver=False)
            latest = self.latest_bulletin()
        return latest, render_html(latest)

    # --- Lecture ---------------------------------------------------------------------------------------------

    def stories(self, category: str | None = None, limit: int = 50) -> list[dict]:
        sql, params = "SELECT data FROM news_stories", []
        if category:
            sql, params = sql + " WHERE category = ?", [category]
        rows = self._store.query(sql + " ORDER BY updated_at DESC LIMIT ?", (*params, limit))
        out = []
        for r in rows:
            d = loads(r["data"], {})
            out.append({k: d[k] for k in ("id", "category", "title", "status", "score", "tier", "updated_at",
                                         "first_seen", "components")} | {"sources": len(d.get("domains", {})),
                                                                         "articles": len(d.get("articles", []))})
        return out

    def recent_items(self, hours: float = 48) -> list[Item]:
        """Articles récents, pour la source « news » de WATCH (fiabilité de leur source)."""
        rows = self._store.query("SELECT * FROM news_articles WHERE fetched_at > ? ORDER BY fetched_at DESC "
                                 "LIMIT 500", (self._clock() - hours * 3600,))
        mapping = {"agency": "agency", "official": "official", "media": "media", "blog": "community"}
        return [Item(f"news:{r['source']}", r["title"], r["summary"], r["url"], r["published_at"],
                     mapping.get(r["reliability"], "unknown"), r["id"]) for r in rows]

    def status(self) -> dict:
        s = self.settings
        latest = self.latest_bulletin()
        return {"sources": len(self.feeds), "événements_48h": len(self.stories(limit=500)),
                "bulletins": list(s.bulletin_times), "dernier_bulletin": latest["at"] if latest else None,
                "appels_24h": self._calls.count(), "llm": bool(self._llm and self._llm.usable)}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        try:
            if not parts and method == "GET":
                return 200, self.status()
            if parts == ["settings"]:
                if method == "GET":
                    return 200, {**self.settings.__dict__, "category_labels": CATEGORY_LABELS,
                                 "default_thresholds": pl.THRESHOLDS}
                if method == "PUT":
                    return 200, self.update_settings(data)
            if parts == ["sources"] and method == "GET":
                states = {r["name"]: r for r in self._store.query("SELECT * FROM news_sources")}
                return 200, [{"name": f.name, "url": f.url, "category": f.category, "reliability": f.reliability,
                              **{k: v for k, v in states.get(f.name, {}).items() if k != "name"}} for f in self.feeds]
            if parts == ["stories"] and method == "GET":
                return 200, self.stories((query.get("category") or [None])[0])
            if len(parts) == 2 and parts[0] == "stories" and method == "GET":
                story = self._story(parts[1])
                if story is None:
                    return NOT_FOUND
                return 200, {**story.as_dict(), "articles": [a.__dict__ for a in self._articles(story)]}
            if parts == ["bulletins"]:
                if method == "GET":
                    rows = self._store.query("SELECT * FROM news_bulletins ORDER BY at DESC, id DESC LIMIT 30")
                    return 200, [self._bulletin_row(r) for r in rows]
                if method == "POST":  # aperçu : composé et gardé, jamais envoyé
                    bulletin_id = self.bulletin(deliver=False)
                    return 201, self._bulletin_row(self._store.one("SELECT * FROM news_bulletins WHERE id = ?",
                                                                   (bulletin_id,)))
            if parts == ["bulletins", "page"] and method == "GET":
                bulletin, html = self.page(fresh=False) if self.latest_bulletin() else self.page()
                return 200, {"id": bulletin["id"], "at": bulletin["at"], "html": html}
            if parts == ["alerts"] and method == "GET":
                return 200, self._alerts.history(100, "news") if self._alerts is not None else []
            if parts == ["collect"] and method == "POST":
                return 200, self.collect()
        except ValueError as exc:
            return 400, {"status": "error", "error": "invalid_settings", "message": str(exc)}
        return NOT_FOUND


class NewsConnector(Connector):
    """Source « news » de WATCH : les articles récents de NEWS (« préviens-moi si l'on annonce… »)."""

    name, kind, reliability = "news", ITEMS, "media"

    def __init__(self, engine: NewsEngine):
        self._engine = engine

    def fetch(self, query: dict) -> list[Item]:
        return self._engine.recent_items()
