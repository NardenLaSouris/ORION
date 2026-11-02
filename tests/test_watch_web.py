"""WATCH automatique : source Web (SearXNG simulé), sources et fréquence recalculées, mots de remplissage, événements
passés. Aucune requête réseau."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform import Scheduler, Store  # noqa: E402
from orion.watch import ConnectorRegistry, StaticConnector, WatchEngine  # noqa: E402
from orion.watch.web import WebSearchConnector, queries_for, reliability_of, strong_keywords  # noqa: E402
from orion.web.base import SearchResult  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0).timestamp()
RAIDS = {"kind": "event", "game": "pokemon_go", "keywords": ["raids", "rayquaza", "disponibles"],
         "require": "confirmed"}


class Engine:
    """SearXNG simulé : requête -> résultats ; garde les requêtes reçues."""

    def __init__(self, results=None):
        self.results, self.asked = results or {}, []

    def search(self, query, time_range=None):
        self.asked.append((query, time_range))
        return self.results.get(query, [])


def result(title, url, snippet="", published="2026-10-30T10:00:00"):
    return SearchResult(title, url, snippet, url.split("/")[2], 1, published)


def test_queries_come_from_the_condition_and_skip_filler_words():
    assert strong_keywords(["raids", "rayquaza", "disponibles"]) == ["raids", "rayquaza"]
    assert queries_for(RAIDS) == ["Pokémon GO raids rayquaza", "raids rayquaza site:pokemongolive.com"]
    assert queries_for({"kind": "announcement", "keywords": ["gta", "6"]}) == ["gta 6"]


def test_reliability_depends_on_the_site():
    assert reliability_of("https://pokemongolive.com/fr/post/raids", RAIDS) == "official"
    assert reliability_of("https://www.jeuxvideo.com/news/x", RAIDS) == "media"
    assert reliability_of("https://forum.example.org/t/1", RAIDS) == "community"
    assert reliability_of("https://pokemongolive.com/x", {"kind": "announcement"}) == "community"


def test_old_results_are_dropped_and_a_month_is_asked():
    engine = Engine({"Pokémon GO raids rayquaza": [
        result("Raids Rayquaza", "https://pokemongolive.com/a"),
        result("Raids Rayquaza 2023", "https://pokemongolive.com/b", published="2023-05-01T10:00:00")]})
    items = WebSearchConnector(engine, clock=lambda: NOW).fetch({"condition": RAIDS})
    assert [i.url for i in items] == ["https://pokemongolive.com/a"]
    assert items[0].reliability == "official" and items[0].source == "web:pokemongolive.com"
    assert all(range_ == "month" for _, range_ in engine.asked)


def world(web_results, clock=None):
    clock = clock or (lambda: NOW)
    connectors = ConnectorRegistry()
    connectors.register(StaticConnector("news", []))
    store = Store()
    engine = WatchEngine(store, connectors, scheduler=Scheduler(clock), clock=clock)
    return engine, connectors, store


def test_an_existing_watch_picks_up_the_web_source_and_its_automatic_frequency():
    engine, connectors, store = world({})
    watch = engine.create({"request": "surveille quand les raids rayquaza sont disponibles sur pokemon go"})
    assert watch.sources == ("news",) and watch.auto_sources and watch.interval_s == 15 * 60
    assert "disponibles" not in watch.condition["keywords"]
    web = Engine({"Pokémon GO raids rayquaza": [
        result("Raids Rayquaza du 5 au 12 novembre 2026", "https://pokemongolive.com/fr/post/rayquaza",
               "Les raids Rayquaza arrivent du 5 au 12 novembre.")]})
    connectors.register(WebSearchConnector(web, clock=lambda: NOW))
    result_ = engine.check(watch.id)
    assert engine.get(watch.id).sources == ("news", "web")
    assert result_.ok and len(result_.findings) == 1 and "Rayquaza" in result_.findings[0].title


def test_old_stored_keywords_with_filler_still_match():
    engine, connectors, store = world({})
    connectors.register(WebSearchConnector(Engine({"Pokémon GO raids rayquaza": [
        result("Raids Rayquaza du 5 au 12 novembre 2026", "https://pokemongolive.com/fr/post/r",
               "Raids Rayquaza du 5 au 12 novembre.")]}), clock=lambda: NOW))
    watch = engine.create({"name": "Rayquaza", "condition": RAIDS})
    assert engine.check(watch.id).findings


def test_a_finished_event_is_never_reported():
    engine, connectors, store = world({})
    connectors.register(WebSearchConnector(Engine({"Pokémon GO raids rayquaza": [
        result("Raids Rayquaza du 1er au 3 octobre 2026", "https://pokemongolive.com/fr/post/old",
               "Raids Rayquaza du 1er au 3 octobre.", published="2026-10-20T10:00:00")]}), clock=lambda: NOW))
    watch = engine.create({"name": "Rayquaza", "condition": RAIDS})
    checked = engine.check(watch.id)
    assert checked.ok and not checked.findings and checked.summary.get("passés") == 1


def test_explicit_sources_and_frequency_are_kept():
    engine, connectors, store = world({})
    connectors.register(WebSearchConnector(Engine(), clock=lambda: NOW))
    watch = engine.create({"name": "Rayquaza", "condition": RAIDS, "sources": ["news"], "interval_minutes": 120})
    assert not watch.auto_sources and not watch.auto_interval
    engine.check(watch.id)
    kept = engine.get(watch.id)
    assert kept.sources == ("news",) and kept.interval_s == 7200


def test_web_failure_is_reported_not_invented():
    class Down:
        def search(self, query, time_range=None):
            raise RuntimeError("SearXNG injoignable")

    engine, connectors, store = world({})
    connectors.register(WebSearchConnector(Down(), clock=lambda: NOW))
    watch = engine.create({"name": "Rayquaza", "condition": RAIDS})
    checked = engine.check(watch.id)
    assert checked.ok and "web" in checked.errors and not checked.findings  # news répond encore


def test_build_v3_registers_the_web_source(tmp_path):
    from dataclasses import replace

    from orion.config import load_config
    from orion.platform.build import build_v3

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, modules=replace(cfg.modules, database=tmp_path / "v3.db"), watch=replace(cfg.watch, enabled=True),
                  web=replace(cfg.web, enabled=True, provider="searxng"))
    modules = build_v3(cfg)
    try:
        assert "web" in modules.registry.get("watch").connectors.names()
    finally:
        modules.stop()
