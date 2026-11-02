"""ORION WATCH : produits, score d'opportunité, interprétation, événements, moteur (déclenchement, dédoublonnage,
pannes, expiration, redémarrage), API d'ORION Control et outils vocaux. Sources simulées, aucune requête réseau."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.events import EventBus  # noqa: E402
from orion.platform import Scheduler, Store  # noqa: E402
from orion.platform.feeds import SourceError  # noqa: E402
from orion.platform.intelligence import StructuredLLM  # noqa: E402
from orion.watch import (  # noqa: E402
    WATCH_FOUND, ConnectorRegistry, Item, Offer, StaticConnector, WatchEngine, WatchError, WatchSettings,
)
from orion.watch.connectors import OFFERS, build_connectors, offer_from  # noqa: E402
from orion.watch.events import CONFIRMED, LEAK, RUMOR, UNVERIFIED, classify, dates_in  # noqa: E402
from orion.watch.interpret import by_rules, interpret  # noqa: E402
from orion.watch.models import validate_condition  # noqa: E402
from orion.watch.opportunity import PriceStats, evaluate, market_price  # noqa: E402
from orion.watch.products import identify, matches  # noqa: E402
from orion.watch.tools import watch_tools  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0).timestamp()


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def offer(seller="LDLC", price=1899.0, shipping=0.0, available=True, condition="new", rating=0.95, reviews=5000,
          title="ASUS ROG Astral GeForce RTX 5090 OC 32 Go", reference=None, url=None, marketplace=False):
    return Offer("boutiques", seller, title, url or f"https://{seller.lower()}.fr/rtx5090", price, shipping,
                 available=available, condition=condition, seller_rating=rating, seller_reviews=reviews,
                 warranty_months=24, returns_days=14, reference_price=reference, marketplace=marketplace)


MARKET = [offer("Materiel", 2400.0, url="https://m.fr/1"), offer("TopAchat", 2350.0, url="https://t.fr/1"),
          offer("Rue du Commerce", 2450.0, url="https://r.fr/1"), offer("Cdiscount", 2420.0, url="https://c.fr/1")]


# --- Produits ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, expected", [
    ("ASUS ROG Astral GeForce RTX 5090 OC Edition 32GB", ("rtx 5090", "", "asus", "rog astral", True)),
    ("MSI GeForce RTX5090 32G SUPRIM LIQUID SOC", ("rtx 5090", "", "msi", "suprim liquid", True)),
    ("NVIDIA GeForce RTX 5090 Founders Edition", ("rtx 5090", "", "nvidia", "founders edition", True)),
    ("PC Gamer i9 + RTX 5090 32 Go", ("rtx 5090", "", "", "", False)),
    ("Boîte vide RTX 5090 Gigabyte", ("rtx 5090", "", "gigabyte", "", False)),
    ("Zotac RTX 5090 D AMP Extreme (Chine)", ("rtx 5090", "d", "zotac", "amp extreme", True)),
])
def test_product_identification(title, expected):
    found = identify(title)
    assert (found.family, found.variant, found.brand, found.model, found.is_card) == expected


def test_only_the_requested_card_matches():
    assert matches("rtx 5090", "Gainward RTX 5090 Phantom GS") is not None
    assert matches("rtx 5090", "Palit RTX 5080 GameRock") is None
    assert matches("rtx 5090", "Zotac RTX 5090 D") is None  # la 5090 D n'est pas la 5090
    assert matches("rtx 5090", "Lot RTX 5090 et RTX 4090") is None
    assert matches("rtx 5090", "Laptop MSI Titan RTX 5090") is None
    assert identify("Écran 27 pouces 4K") is None


# --- Score d'opportunité ----------------------------------------------------------------------------------

STATS = PriceStats(30, 2050.0, 2300.0, 2500.0)


def test_real_bargain_with_full_data_is_exceptional():
    opp = evaluate(offer(price=1899.0), market_price(MARKET), STATS)
    assert opp.score >= 85 and opp.label == "exceptionnelle" and opp.sufficient and not opp.flags


def test_missing_data_never_gives_an_exceptional_score():
    opp = evaluate(offer(price=1899.0, shipping=None), market_price(MARKET), STATS)
    assert opp.score <= 69 and opp.label != "exceptionnelle" and "shipping" in opp.missing
    assert evaluate(offer(price=1899.0), None, None).score <= 69  # pas de prix de marché


def test_too_good_to_be_true_and_fake_discounts_are_flagged():
    cheap = evaluate(offer(price=900.0), market_price(MARKET), STATS)
    assert cheap.suspicious and cheap.score <= 40
    shady = evaluate(offer(price=1950.0, rating=0.6, reviews=4), market_price(MARKET), STATS)
    assert any("vendeur peu noté" in f for f in shady.flags)
    fake = evaluate(offer(price=2290.0, reference=3999.0), market_price(MARKET), STATS)
    assert any("fausse promotion" in f for f in fake.flags) and fake.score <= 40


def test_unavailable_offer_scores_zero_and_market_needs_three_reliable_offers():
    assert evaluate(offer(available=False), market_price(MARKET), STATS).score == 0
    assert market_price(MARKET[:2]) is None
    assert market_price(MARKET + [offer("Inconnu", 100.0, rating=0.3, url="https://x.fr")]) == 2410.0


# --- Interprétation ---------------------------------------------------------------------------------------

def test_mission_examples_are_understood_without_llm():
    raids = by_rules("Préviens-moi quand les raids Rayquaza arrivent sur Pokémon GO.")
    assert raids.condition == {"kind": "event", "keywords": ("raids", "rayquaza"), "game": "pokemon_go",
                               "require": "confirmed"}
    deal = by_rules("Surveille les prix des RTX 5090 et préviens-moi lorsqu'une bonne affaire apparaît.")
    assert deal.condition["kind"] == "price" and deal.condition["min_score"] == 70
    assert deal.condition["max_price"] is None
    capped = by_rules("Préviens-moi si une RTX 5090 passe à moins de 2 000 €")
    assert capped.condition["max_price"] == 2000.0
    stock = by_rules("Dis-moi quand la RTX 5080 est de nouveau disponible")
    assert stock.condition["kind"] == "availability" and stock.condition["product"] == "rtx 5080"
    release = by_rules("Surveille les annonces officielles de la sortie de Blender 5")
    assert release.condition["kind"] == "announcement" and "blender" in release.condition["keywords"]


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def chat_json(self, messages, schema):
        return self.answer


def test_unclear_requests_use_the_llm_but_only_validated_conditions():
    good = StructuredLLM(FakeLLM({"name": "Switch 2 dispo", "condition": {"kind": "announcement",
                                                                           "keywords": ["switch 2"]}}))
    assert interpret("Dis-moi quand ce que j'attends arrive", good).by == "llm"
    bad = StructuredLLM(FakeLLM({"name": "x", "condition": {"kind": "price", "code": "__import__('os')"}}))
    with pytest.raises(WatchError):
        interpret("Dis-moi quand ce que j'attends arrive", bad)
    with pytest.raises(WatchError):
        interpret("Dis-moi quand ce que j'attends arrive", None)


def test_condition_validation():
    with pytest.raises(WatchError):
        validate_condition({"kind": "price"})
    with pytest.raises(WatchError):
        validate_condition({"kind": "event", "keywords": []})
    with pytest.raises(WatchError):
        validate_condition({"kind": "price", "product": "rtx 5090", "max_price": -3})


# --- Événements -------------------------------------------------------------------------------------------

def item(title, summary="", reliability="official", published=NOW, url="https://pokemongolive.com/fr/news/x"):
    return Item("pogo", title, summary, url, published, reliability)


def test_event_classification():
    assert classify(item("Raids de Rayquaza du 12 au 15 novembre")).status == CONFIRMED
    assert classify(item("Rayquaza pourrait revenir en raids", reliability="media")).status == RUMOR
    assert classify(item("Des dataminers ont trouvé Rayquaza Méga", reliability="official")).status == LEAK
    assert classify(item("Niantic annonce les raids Rayquaza", reliability="media")).status == UNVERIFIED


def test_dates_are_read_in_french_english_and_iso():
    ref = datetime(2026, 11, 2)
    assert [d.isoformat() for d in dates_in("du 12 au 15 novembre", ref)] == ["2026-11-12", "2026-11-15"]
    assert dates_in("le 3 janvier", datetime(2026, 12, 20))[0].isoformat() == "2027-01-03"
    assert dates_in("November 12 to 15, event", ref)[1].isoformat() == "2026-11-15"
    assert dates_in("2026-11-20 au 2026-11-22", ref)[1].isoformat() == "2026-11-22"


# --- Connecteurs ------------------------------------------------------------------------------------------

def test_offers_json_keeps_unknown_values_unknown():
    o = offer_from("api", {"seller": "LDLC", "title": "RTX 5090", "url": "https://ldlc.fr/x", "price": 1999})
    assert o.shipping is None and o.available is None and o.total is None and o.condition is None
    assert offer_from("api", {"seller": "X", "title": "RTX 5090", "url": "javascript:alert(1)"}) is None
    registry = build_connectors({"boutique": {"type": "offers_json", "url": "https://api.test/o"}},
                                fetcher=lambda url: json.dumps({"offers": [{"seller": "A", "title": "RTX 5090",
                                                                            "url": "https://a.fr"}]}).encode())
    assert registry.get("boutique").fetch({})[0].seller == "A"
    with pytest.raises(ValueError):
        build_connectors({"x": {"type": "scraper", "url": "https://a"}})


# --- Moteur -----------------------------------------------------------------------------------------------

class Alerts:
    def __init__(self):
        self.sent = []

    def deliver(self, alert):
        self.sent.append(alert)


class World:
    def __init__(self, offers=None, items=None, store=None, clock=None, settings=None):
        self.clock = clock or Clock()
        self.store = store or Store()
        self.offers = list(offers if offers is not None else MARKET)
        self.items = list(items or [])
        registry = ConnectorRegistry()
        registry.register(StaticConnector("boutiques", lambda: self.offers, OFFERS))
        registry.register(StaticConnector("pogo", lambda: self.items))
        self.alerts, self.events = Alerts(), EventBus()
        self.found = []
        self.events.subscribe(WATCH_FOUND, self.found.append)
        self.scheduler = Scheduler(self.clock)
        self.engine = WatchEngine(self.store, registry, alerts=self.alerts, scheduler=self.scheduler,
                                  events=self.events, settings=settings or WatchSettings(failure_limit=3),
                                  clock=self.clock)
        self.engine.start()

    def later(self, seconds=3600):
        self.clock.t += seconds
        self.engine._cache.clear()  # noqa: SLF001 - nouvelle lecture des sources simulées

    def price_watch(self, **extra):
        return self.engine.create({"name": "RTX 5090", "condition": {"kind": "price", "product": "rtx 5090",
                                                                      "min_score": 80}, **extra})


def test_bargain_triggers_once_with_details_and_completes():
    w = World(MARKET + [offer("LDLC", 1899.0)])
    watch = w.price_watch()
    result = w.engine.check(watch.id)
    assert result.ok and len(result.findings) == 1
    alert = w.alerts.sent[0]
    assert "LDLC" in alert.message and "1899 € livraison comprise" in alert.message and "score" in alert.message
    assert w.found[0].payload["total"] == 1899.0 and w.found[0].payload["vendeur"] == "LDLC"
    assert w.engine.get(watch.id).state == "COMPLETED"
    assert w.engine.check(watch.id) is None  # terminée : plus vérifiée


def test_repeat_mode_reports_a_new_price_but_never_the_same_offer_twice():
    w = World(MARKET + [offer("LDLC", 1899.0)])
    watch = w.price_watch(mode="repeat")
    w.engine.check(watch.id)
    w.later()
    assert w.engine.check(watch.id).findings == []  # même offre, même prix : rien
    w.offers[-1] = offer("LDLC", 1849.0)
    w.later()
    assert len(w.engine.check(watch.id).findings) == 1 and w.engine.get(watch.id).state == "ACTIVE"


def test_unknown_availability_unknown_shipping_and_suspicious_offers_never_trigger():
    w = World(MARKET + [offer("A", 1899.0, available=None, url="https://a.fr"),
                        offer("B", 1899.0, shipping=None, url="https://b.fr"),
                        offer("C", 900.0, url="https://c.fr")])
    watch = w.engine.create({"name": "RTX", "condition": {"kind": "price", "product": "rtx 5090",
                                                           "max_price": 2000}})
    result = w.engine.check(watch.id)
    assert result.findings == [] and not w.alerts.sent
    assert result.summary["écartées"] == {"disponibilité inconnue": 1, "prix final inconnu": 1, "offre suspecte": 1,
                                          "au-dessus du prix maximum": 4}


def test_back_in_stock_is_reported_again_after_a_stock_out():
    w = World([offer("LDLC", 2399.0, available=False)])
    watch = w.engine.create({"name": "dispo", "condition": {"kind": "availability", "product": "rtx 5090"},
                             "mode": "repeat"})
    assert w.engine.check(watch.id).findings == []
    w.offers = [offer("LDLC", 2399.0, available=True)]
    w.later()
    assert len(w.engine.check(watch.id).findings) == 1
    w.later()
    assert w.engine.check(watch.id).findings == []
    w.offers = [offer("LDLC", 2399.0, available=False)]
    w.later()
    w.engine.check(watch.id)
    w.offers = [offer("LDLC", 2399.0, available=True)]
    w.later()
    assert len(w.engine.check(watch.id).findings) == 1


def test_rumors_never_trigger_and_confirmed_events_and_calendar_changes_do():
    w = World(items=[item("Rayquaza pourrait revenir en raids", reliability="media"),
                     item("Fuite : raids Rayquaza dans les fichiers du jeu", reliability="community")])
    watch = w.engine.create({"name": "Rayquaza", "condition": {"kind": "event", "game": "pokemon_go",
                                                                "keywords": ["raids", "rayquaza"]}, "mode": "repeat"})
    result = w.engine.check(watch.id)
    assert result.findings == [] and result.summary["rumeurs_ou_fuites"] == 2
    w.items.append(item("Raids de Rayquaza du 12 au 15 novembre"))
    w.later()
    found = w.engine.check(watch.id).findings
    assert len(found) == 1 and "Événement confirmé" in found[0].title and "du 12/11 au 15/11/2026" in found[0].message
    w.later()
    assert w.engine.check(watch.id).findings == []
    w.items[-1] = item("Raids de Rayquaza du 13 au 16 novembre")
    w.later()
    changed = w.engine.check(watch.id).findings
    assert len(changed) == 1 and changed[0].title.startswith("Calendrier modifié")


def test_sources_down_lead_to_failed_and_resume_restarts():
    w = World()
    watch = w.price_watch()
    w.offers = SourceError("HTTP 503")  # type: ignore[assignment]
    w.engine.connectors.get("boutiques")._observations = lambda: SourceError("HTTP 503")  # noqa: SLF001
    for _ in range(3):
        assert not w.engine.check(watch.id).ok
        w.later()
    assert w.engine.get(watch.id).state == "FAILED" and "Surveillance en échec" in w.alerts.sent[-1].title
    assert w.engine.check(watch.id) is None
    w.engine.connectors.get("boutiques")._observations = lambda: MARKET  # noqa: SLF001
    assert w.engine.resume(watch.id).state == "ACTIVE" and w.engine.check(watch.id).ok


def test_expired_watch_stops_and_paused_watch_is_not_checked():
    w = World()
    watch = w.price_watch(expires_at=NOW + 3600)
    w.later(7200)
    assert w.engine.check(watch.id) is None and w.engine.get(watch.id).state == "EXPIRED"
    other = w.price_watch()
    w.engine.pause(other.id)
    assert w.engine.check(other.id) is None
    with pytest.raises(ValueError):
        w.engine.pause(watch.id)  # expirée : définitive


def test_watches_survive_a_restart_and_are_rescheduled(tmp_path):
    store = Store(tmp_path / "v3.db")
    w = World(MARKET + [offer("LDLC", 1899.0)], store=store)
    watch = w.price_watch(interval_minutes=30)
    restarted = World(MARKET + [offer("LDLC", 1899.0)], store=Store(tmp_path / "v3.db"), clock=w.clock)
    assert restarted.scheduler.has(f"watch:{watch.id}")
    assert restarted.scheduler.tick() == [f"watch:{watch.id}"] and len(restarted.alerts.sent) == 1


def test_requests_per_source_are_limited_and_cached():
    calls = []
    w = World(settings=WatchSettings(requests_per_source_per_hour=2))
    w.engine.connectors.get("boutiques")._observations = lambda: calls.append(1) or MARKET  # noqa: SLF001
    first, second = w.price_watch(), w.price_watch()
    w.engine.check(first.id)
    w.engine.check(second.id)
    assert len(calls) == 1  # cache : une seule requête pour les deux surveillances
    for _ in range(3):
        w.later(400)
        w.engine.check(first.id)
    assert len(calls) == 2  # limite : 2 par heure
    assert "limite de requêtes" in w.engine.checks(first.id)[0]["errors"]["boutiques"]


def test_creation_refuses_unknown_or_unsuitable_sources():
    w = World()
    with pytest.raises(WatchError, match="source inconnue"):
        w.engine.create({"name": "x", "condition": {"kind": "price", "product": "rtx 5090"}, "sources": ["ebay"]})
    with pytest.raises(WatchError, match="ne fournit pas"):
        w.engine.create({"name": "x", "condition": {"kind": "price", "product": "rtx 5090"}, "sources": ["pogo"]})
    watch = w.engine.create({"request": "Préviens-moi quand les raids Rayquaza arrivent sur Pokémon GO"})
    assert watch.sources == ("pogo",) and watch.interval_s == 15 * 60  # événement : 15 min (fréquence automatique)


def test_written_only_channel_is_honoured():
    w = World(MARKET + [offer("LDLC", 1899.0)])
    watch = w.price_watch(channel="written")
    w.engine.check(watch.id)
    assert w.alerts.sent[0].written_only


# --- ORION Control et voix --------------------------------------------------------------------------------

def test_admin_routes():
    w = World(MARKET + [offer("LDLC", 1899.0)])
    status, created = w.engine.admin("POST", [], {}, {"request": "Surveille les prix des RTX 5090, bonne affaire"})
    assert status == 201 and created["condition"]["min_score"] == 70
    assert w.engine.admin("POST", ["interpret"], {}, {"request": "raids Rayquaza Pokémon GO"})[1]["by"] == "règles"
    assert w.engine.admin("POST", [created["id"], "check"], {}, None)[1]["nouveaux"] == 1
    detail = w.engine.admin("GET", [created["id"]], {}, None)[1]
    assert detail["checks"] and detail["found"] and detail["history"]
    assert w.engine.admin("GET", ["prices"], {"product": ["rtx 5090"]}, None)[1]["stats"]["count"] == 5
    assert w.engine.admin("POST", [created["id"], "pause"], {}, None)[0] == 400  # terminée
    assert w.engine.admin("DELETE", [created["id"]], {}, None)[0] == 200
    assert w.engine.admin("GET", [created["id"]], {}, None)[0] == 404
    assert w.engine.admin("POST", [], {}, {"name": "x", "condition": {"kind": "nimporte"}})[0] == 400


def test_voice_tools():
    w = World()
    tools = {t.name: t for t in watch_tools(w.engine)}
    create = tools["create_watch"]
    assert create.requires_confirmation and tools["delete_watch"].requires_confirmation
    assert "Pokémon GO" in create.question({"request": "Préviens-moi des raids Rayquaza sur Pokémon GO"})
    result = create.run(request="Préviens-moi des raids Rayquaza sur Pokémon GO")
    assert result["position"] == 1
    assert "1, Pokémon GO : raids rayquaza, active" in tools["list_watches"].say(tools["list_watches"].run())
    assert tools["pause_watch"].run(position=1)["state"] == "en pause"
    assert tools["resume_watch"].run(position=1)["state"] == "active"
    assert tools["delete_watch"].run(position=1)["state"] == "supprimée"


def test_suspicious_or_unrated_offers_never_enter_the_price_history():
    w = World(MARKET + [offer("Vendeur X", 990.0, url="https://x.fr"), offer("Inconnu", 2300.0, rating=None,
                                                                          url="https://i.fr")])
    watch = w.price_watch()
    w.engine.check(watch.id)
    stats = w.engine.prices.stats("rtx 5090")
    assert stats.count == 4 and stats.minimum == 2350.0  # ni l'offre à 990 €, ni le vendeur sans note


def test_reasons_say_below_or_above_the_market():
    assert "15 % sous le prix du marché (2410 €)" in evaluate(offer(price=2050.0), 2410.0, None).reasons[0]
    assert "4 % au-dessus du prix du marché" in evaluate(offer(price=2500.0), 2410.0, None).reasons[0]
