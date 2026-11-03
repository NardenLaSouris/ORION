"""NEWS P1 : journal quotidien sans données personnelles. Composition (rubriques, quotas, « à ne pas manquer »
toujours repris, diversité, pas de remplissage au-delà de 5), doublons et évolutions, titres trompeurs écartés, fiche
complète (lien, fiabilité, date, pourquoi), aucune invention, programmation à 07:30, outils vocaux, page et routes.
Flux et horloge simulés."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orion.news import briefing as br  # noqa: E402
from orion.news import pipeline as pl  # noqa: E402
from orion.news.page import render_journal  # noqa: E402
from orion.news.tools import news_tools  # noqa: E402
from orion.tools.base import ToolError  # noqa: E402
from test_news import GRAVE, TWIN, World  # noqa: E402

NOW = datetime(2026, 11, 3, 7, 0).timestamp()


def story(sid, category, score, tier=pl.BULLETIN, domains=None, title=None):
    s = pl.Story(sid, category, title or f"Sujet {sid}", set(), NOW, NOW, articles=[sid])
    s.domains = domains or {f"{sid}.test": "media", f"{sid}2.test": "media"}
    s.status = "confirmé" if len([r for r in s.domains.values() if r != "blog"]) >= 2 else "non confirmé"
    s.score, s.tier, s.facts = score, tier, {f"f{sid}"}
    return s


def article(s, title=None, summary="Résumé factice de l'article.", reliability="media", url=None):
    return pl.Article(f"a{s.id}", "Source", s.category, reliability, title or s.title, summary,
                      url or f"https://{s.id}.test/a", f"{s.id}.test", NOW - 600, NOW - 300)


def articles_of(s):
    return [article(s)]


# --- Composition ---------------------------------------------------------------------------------------------

def test_must_not_miss_always_enters_and_quotas_hold():
    stories = [story(f"e{i}", "geopolitique", 90, pl.ALERT) for i in range(4)]
    stories += [story(f"t{i}", "tech_ia", 60 - i) for i in range(6)]
    stories += [story(f"g{i}", "geopolitique", 55 - i) for i in range(6)]
    stories += [story("eco", "economie", 31)]
    picks = br.plan(stories, articles_of, floor=30).picks
    assert len(picks) <= br.MAX_ITEMS
    assert sum(p.rubric == "essentiel" for p in picks) == 4  # au-delà de tout quota
    for rubric in ("tech_sciences", "international"):
        assert sum(p.rubric == rubric for p in picks) <= br.PER_RUBRIC
    assert any(p.story.id == "eco" for p in picks)  # diversité : l'économie entre malgré un score faible
    assert [p.rubric for p in picks][:4] == ["essentiel"] * 4  # ordre des rubriques


def test_sciences_share_the_technology_rubric():
    picks = br.plan([story("s", "sciences", 50), story("t", "tech_ia", 40)], articles_of, floor=30).picks
    assert {p.rubric for p in picks} == {"tech_sciences"}


def test_thin_news_is_completed_up_to_five_never_beyond():
    stories = [story("a", "geopolitique", 40)] + [story(f"f{i}", "france", 20 - i) for i in range(8)]
    stories += [story("z", "france", 5)]  # sous la moitié du seuil : jamais
    picks = br.plan(stories, articles_of, floor=30).picks
    assert len(picks) == br.MIN_ITEMS - 2 or len(picks) <= br.MIN_ITEMS
    assert all(p.story.id != "z" for p in picks)
    assert [p.why for p in picks if p.story.id.startswith("f")] == [br.WHY["filler"]] * (len(picks) - 1)


def test_unchanged_story_from_the_previous_journal_is_a_duplicate_and_new_facts_make_an_evolution():
    old, moved = story("old", "geopolitique", 60), story("moved", "geopolitique", 60)
    old.journal_facts = set(old.facts)
    moved.journal_facts = set()
    result = br.plan([old, moved], articles_of, floor=30, previous={"old", "moved"})
    assert "old" in result.excluded and [p.rubric for p in result.picks] == ["evolutions"]
    assert result.picks[0].evolution and result.picks[0].why == br.WHY["evolutions"]


# --- Titres trompeurs ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["INCROYABLE : ce que personne ne vous dit sur l'euro", "Choc à l'Assemblée !!",
                                   "Voici pourquoi tout va changer pour vous"])
def test_sensational_unconfirmed_titles_are_excluded(title):
    s = story("x", "france", 60, domains={"x.test": "blog"}, title=title)
    assert br.misleading(s, [article(s)]).startswith("titre à sensation")
    assert "x" in br.plan([s], lambda st: [article(st)], floor=30).excluded


def test_a_confirmed_story_keeps_its_title_and_a_number_absent_from_the_text_is_flagged():
    loud = story("y", "france", 60, title="Choc : la réforme adoptée")
    assert br.misleading(loud, [article(loud)]) == ""  # confirmée par deux sources : gardée
    lone = story("n", "economie", 60, domains={"n.test": "media"}, title="Le chômage grimpe à 12 %")
    assert "12" in br.misleading(lone, [article(lone, summary="Le chômage progresse selon l'Insee.")])
    assert br.misleading(lone, [article(lone, summary="Le chômage atteint 12 % selon l'Insee.")]) == ""
    dated = story("d", "france", 60, domains={"d.test": "media"}, title="Budget 2026 : le vote reporté au 17 novembre")
    assert br.misleading(dated, [article(dated, summary="Le gouvernement reporte le vote.")]) == ""  # date, année


def test_one_place_per_subject_even_when_clustering_kept_two_events():
    a = story("m1", "geopolitique", 51, title="Midterms 2026 : les Américains appelés aux urnes pour un vote")
    b = story("m2", "geopolitique", 50, title="🔴 Midterms : plus de 58 millions d'électeurs ont voté, le vote continue")
    c = story("ir", "geopolitique", 49, title="Iran : nouvelles frappes sur Qechm")
    for s in (a, b, c):
        s.tokens = pl.tokens(s.title)
    result = br.plan([a, b, c], articles_of, floor=30)
    assert [p.story.id for p in result.picks] == ["m1", "ir"] and "même sujet" in result.excluded["m2"]


# --- Fiche --------------------------------------------------------------------------------------------------

def test_card_takes_the_most_reliable_title_link_date_and_reliability_without_invention():
    s = story("c", "geopolitique", 60, domains={"c.test": "blog", "gouv.test": "official"})
    blog = article(s, title="Rumeur sur le sommet", reliability="blog", url="https://c.test/blog")
    gov = pl.Article("g", "Élysée", "geopolitique", "official", "Le sommet aura lieu jeudi", "Communiqué officiel.",
                     "https://gouv.test/communique", "gouv.test", NOW - 7200, NOW - 300)
    item = br.card(1, br.Pick(s, [blog, gov], "international", br.WHY["general"]), {"summary": "", "by": "règles"})
    assert item["title"] == "Le sommet aura lieu jeudi" and item["url"] == "https://gouv.test/communique"
    assert item["reliability"] == "officielle" and item["summary"] == "Communiqué officiel."
    assert item["published_at"] == NOW - 7200 and item["why"] == br.WHY["general"]
    assert [link["url"] for link in item["links"]][0] == "https://gouv.test/communique"
    lone = story("l", "france", 50, domains={"l.test": "media"})
    assert br.reliability(lone) == ("non confirmée", "Source unique, non confirmée")
    assert br.reliability(story("m", "france", 50))[0] == "confirmée"


def test_spoken_version_reads_five_titles_and_offers_details():
    items = [{"number": i, "title": f"Titre {i}"} for i in range(1, 8)]
    text = br.spoken(items, "du 03/11")
    assert "7 informations" in text and "5. Titre 5." in text and "Titre 6" not in text
    assert text.endswith("Voulez-vous des détails sur l'une d'elles ?")


# --- Moteur, programmation, outils, page ---------------------------------------------------------------------

def collected_world():
    w = World(home=True)
    w.publish("https://afp.test/rss", *GRAVE)
    w.publish("https://lemonde.test/rss", *TWIN)
    w.publish("https://echos.test/rss", "La BCE relève ses taux de 0,25 point face à l'inflation",
              "La Banque centrale européenne relève ses taux directeurs de 0,25 point ; crise de l'inflation.")
    w.engine.collect()
    return w


def test_journal_at_its_time_once_caught_up_never_twice_and_preview_not_counted():
    w = collected_world()
    w.clock.t = datetime(2026, 11, 2, 7, 20).timestamp()
    assert w.engine.journal_due() is None  # avant 07:30
    preview = w.engine.journal(deliver=False)
    assert w.engine.latest_journal(delivered_only=True) is None and preview
    w.clock.t = datetime(2026, 11, 2, 8, 10).timestamp()  # redémarrage : rattrapé moins de 2 h après
    journal_id = w.engine.journal_due()
    assert journal_id and w.engine.journal_due() is None
    sent = w.engine.latest_journal(delivered_only=True)
    assert sent["id"] == journal_id and sent["items"][0]["rubric"] == "essentiel"
    assert any("Journal du 02/11" in text for _, text in w.channels.sent)
    assert all(i["url"].startswith("https://") for i in sent["items"])


def test_tools_give_the_journal_and_details_by_number_or_subject():
    w = collected_world()
    tools = {t.name: t for t in news_tools(w.engine)}
    said = tools["news_briefing"].run()
    assert said["count"] >= 2 and "1. " in said["text"] and said["text"].endswith("l'une d'elles ?")
    second = tools["news_more"].run(numero=2)
    assert second["number"] == 2 and "Sources :" in second["text"]
    by_subject = tools["news_more"].run(sujet="les taux de la BCE")
    assert "BCE" in by_subject["text"]
    with pytest.raises(ToolError):
        tools["news_more"].run(numero=9)


def test_journal_page_and_routes():
    w = collected_world()
    status, body = w.engine.admin("POST", ["journals"], {}, None)
    assert status == 201 and body["delivered"] == "aperçu"
    status, page = w.engine.admin("GET", ["journals", "page"], {}, None)
    assert status == 200 and "Journal du" in page["html"] and "Lire l’article" in page["html"]
    assert "À ne pas manquer" in page["html"] and "aperçu" in page["html"]
    html = render_journal({"at": NOW, "items": [], "spoken": "Peu d'actualité.", "excluded": {"a": "x"}, "day": ""})
    assert "Peu d&#x27;actualité." in html and "1 écartée" in html
    assert w.engine.update_settings({"journal_time": "6:45", "journal_enabled": False})["journal_time"] == "06:45"
    with pytest.raises(ValueError):
        w.engine.update_settings({"journal_time": "25:00"})
    assert "sciences" in w.engine.settings.categories


def test_journal_reads_no_personal_data():
    import ast

    tree = ast.parse((ROOT / "orion" / "news" / "briefing.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "re", "dataclasses", "orion.news", "orion.watch.products"}


@pytest.mark.parametrize("text, number", [("Approfondis la deuxième", 2), ("parle-moi de la 3", 3),
                                          ("détaille l'information numéro 4", 4), ("et la dernière ?", -1),
                                          ("la 1re", 1), ("parle-moi de la BCE", None), ("il y a 12 morts", None),
                                          ("Approfondis la deuxième la dernière ?", -1)])  # relance composée
def test_the_item_number_comes_from_the_words_never_from_the_llm(text, number):
    from orion.news.tools import ordinal

    assert ordinal(text) == number


def test_last_item_by_ordinal():
    w = collected_world()
    more = {t.name: t for t in news_tools(w.engine)}["news_more"]
    count = len(w.engine.current_journal()["items"])
    assert more.run(numero=-1)["number"] == count
    assert more.parameters["numero"].hidden  # le LLM ne le propose jamais


def test_a_follow_up_takes_the_number_from_its_own_words():
    from types import SimpleNamespace

    from orion.agent import Agent
    from orion.tools.registry import ToolRegistry

    w = collected_world()
    registry = ToolRegistry()
    for tool in news_tools(w.engine):
        registry.register(tool)
    agent = Agent.__new__(Agent)
    agent._tools = SimpleNamespace(registry=registry)
    carried = {"type": "tool_call", "tool": "news_more", "parameters": {"numero": 2}}
    assert agent._with_hidden(carried, "Et la dernière ?")["parameters"]["numero"] == -1
    assert agent._with_hidden(carried, "Et encore ?")["parameters"]["numero"] == 2  # rien de nouveau : gardé
