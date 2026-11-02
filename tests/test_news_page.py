"""Bulletin NEWS mis en page (HTML) et remise : navigateur du PC, sinon mail ; à l'allumage du PC, une seule fois.
Flux simulés, aucune requête réseau."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.news.delivery import BulletinDelivery  # noqa: E402
from orion.news.page import render_html  # noqa: E402
from orion.news.synthesis import distinct  # noqa: E402
from tests.test_news import GRAVE, TWIN, World  # noqa: E402


def world_with_news():
    w = World(home=True)
    w.publish("https://afp.test/rss", *GRAVE)
    w.publish("https://lemonde.test/rss", *TWIN)
    w.publish("https://echos.test/rss", "La BCE maintient ses taux", "Taux directeurs inchangés.")
    w.engine.collect()
    return w


def test_page_has_sections_cards_status_and_source_links():
    w = world_with_news()
    w.engine.bulletin(deliver=False)
    bulletin, html = w.engine.page(fresh=False)
    assert bulletin["entries"] and html.startswith("<!doctype html>")
    assert "Géopolitique" in html and "confirmé" in html and 'href="https://afp.test/' in html
    assert "<script" not in html and "http://fonts" not in html  # aucune ressource externe


def test_text_from_articles_is_escaped():
    html = render_html({"at": 0, "slot": "08:00", "entries": [
        {"title": "<script>alert(1)</script>", "status": "confirmé", "summary": "a & b", "confirmed": [],
         "uncertain": [], "category": "france", "links": [{"source": "x", "url": "javascript:alert(1)"}]}]})
    assert "<script>alert" not in html and "&lt;script&gt;" in html and "javascript:" not in html


def test_confirmed_facts_do_not_repeat_the_summary():
    summary = "Des cyberattaques ont ciblé les systèmes frigorifiques de supermarchés en Europe."
    facts = ["Des cyberattaques ont ciblé les systèmes frigorifiques de plusieurs supermarchés en Europe.",
             "Les pirates ont exploité des failles non corrigées."]
    assert distinct(summary, facts) == [facts[1]]


def test_a_stale_bulletin_is_refreshed_as_a_preview():
    w = world_with_news()
    first = w.engine.bulletin(deliver=True, slot="08:00")
    w.later(7 * 3600)
    bulletin, _ = w.engine.page()
    assert bulletin["id"] != first and bulletin["delivered"] == "aperçu"
    assert w.engine.latest_bulletin(delivered_only=True)["id"] == first


class Pc:
    def __init__(self, on=True):
        self.on, self.opened = on, []

    def __call__(self, url):
        if not self.on:
            raise ConnectionError("PC éteint")
        self.opened.append(url)
        return True


def delivery(w, tmp_path, pc, mails):
    return BulletinDelivery(w.engine, lambda: "http://192.168.1.91:8767/bulletin", pc,
                            lambda subject, text, html: mails.append((subject, html)) or True,
                            tmp_path / "news_delivery.json", clock=w.clock)


def test_wake_routine_opens_on_the_pc_or_mails_when_it_is_off(tmp_path):
    w = world_with_news()
    w.engine.bulletin(deliver=True, slot="08:00")
    mails, pc = [], Pc(on=True)
    d = delivery(w, tmp_path, pc, mails)
    assert d.deliver()["channel"] == "navigateur" and pc.opened == ["http://192.168.1.91:8767/bulletin"]
    pc.on = False
    result = d.deliver()
    assert result["channel"] == "mail" and mails[0][0].startswith("ORION — Bulletin de 08:00")
    assert "<!doctype html>" in mails[0][1]


def test_pc_startup_opens_an_unseen_bulletin_once(tmp_path):
    w = world_with_news()
    mails, pc = [], Pc(on=True)
    d = delivery(w, tmp_path, pc, mails)
    assert d.pc_started() is False  # aucun bulletin envoyé
    w.engine.bulletin(deliver=False)  # un aperçu ne s'ouvre jamais tout seul
    assert d.pc_started() is False
    w.engine.bulletin(deliver=True, slot="08:00")
    assert d.pc_started() is True and len(pc.opened) == 1
    assert d.pc_started() is False  # déjà vu
    w.later(19 * 3600)
    w.engine.bulletin(deliver=True, slot="20:00")
    w.later(19 * 3600)
    assert d.pc_started() is False  # trop ancien


def test_seen_by_the_routine_is_not_reopened_at_startup(tmp_path):
    w = world_with_news()
    w.engine.bulletin(deliver=True, slot="08:00")
    mails, pc = [], Pc(on=False)
    d = delivery(w, tmp_path, pc, mails)
    assert d.deliver()["channel"] == "mail"
    pc.on = True
    assert d.pc_started() is False and pc.opened == []


def test_delivery_tool_is_a_notification():
    w = world_with_news()
    tool = BulletinDelivery(w.engine, lambda: "u", lambda url: True, None, "/tmp/inutile.json").tool()
    assert tool.name == "deliver_news_bulletin" and tool.capability == "NOTIFY"


def test_wallpaper_serves_the_bulletin_page_to_allowed_addresses_only():
    import urllib.error
    import urllib.request

    from orion.wallpaper.collector import Collector
    from orion.wallpaper.server import WallpaperServer

    server = WallpaperServer(Collector([]), "127.0.0.1", 0, 8765, ())
    server.pages["/bulletin"] = lambda: "<!doctype html><p>bulletin</p>"
    url = server.start()
    try:
        base = url.rstrip("/")
        with urllib.request.urlopen(base + "/bulletin", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/html") and b"bulletin" in response.read()
        try:
            urllib.request.urlopen(base + "/absent", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()
