"""Construction des modules V3 (orion/platform/build.py) : rien sans activation (comportement V2), WATCH activé avec
ses outils, alertes reliées à la présence, aux notifications vocales et aux appels et messages Linphone."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.call import CallRefused  # noqa: E402
from orion.config import load_config  # noqa: E402
from orion.platform.alerts import Alert, Level  # noqa: E402
from orion.platform.build import build_v3  # noqa: E402


def config(tmp_path, **sections):
    cfg = load_config(ROOT / "config.toml", local=False)
    cfg = replace(cfg, modules=replace(cfg.modules, database=tmp_path / "v3.db", quiet_start="00:00",
                                       quiet_end="00:00"))
    for name, values in sections.items():
        cfg = replace(cfg, **{name: replace(getattr(cfg, name), **values)})
    return cfg


def test_nothing_is_built_without_an_enabled_module(tmp_path):
    assert build_v3(config(tmp_path)) is None
    assert not (tmp_path / "v3.db").exists()


class Presence:
    def __init__(self, home):
        self.engine = self
        self.home = home

    def is_home(self, user):
        return self.home


class Notifications:
    def __init__(self):
        self.sent = []

    def notify(self, notification):
        self.sent.append(notification.message)
        return ["voice"]


class Calls:
    def __init__(self, refuse=False):
        self.requests, self.replies, self.refuse = [], [], refuse

    def request(self, reason, origin):
        if self.refuse:
            raise CallRefused("limite")
        self.requests.append((reason, origin))
        return type("Call", (), {"final": False})()

    def reply(self, text):
        self.replies.append(text)
        return True


def test_watch_module_is_built_with_its_tools_and_alerts_are_connected(tmp_path):
    cfg = config(tmp_path, watch={"enabled": True, "sources": {"pogo": {"url": "https://exemple.fr/rss",
                                                                         "reliability": "official"}}})
    notifications, presence = Notifications(), Presence(True)
    v3 = build_v3(cfg, presence=presence, notifications=notifications)
    try:
        assert {t.name for t in v3.tools} >= {"create_watch", "list_watches", "delete_watch"}
        calls = Calls()
        v3.connect(calls)
        assert [s["state"] for s in v3.registry.status()] == ["actif"]
        v3.alerts.deliver(Alert("watch", "Test", "Message à la maison.", Level.HIGH, key="a"))
        assert notifications.sent == ["Message à la maison."]
        presence.home = False
        v3.alerts.deliver(Alert("watch", "Test", "Message absent.", Level.HIGH, key="b"))
        assert calls.replies == ["Message absent."] and calls.requests[0][1] == "alert"
    finally:
        v3.stop()


def test_refused_call_is_not_counted_as_delivered(tmp_path):
    cfg = config(tmp_path, watch={"enabled": True})
    v3 = build_v3(cfg, presence=Presence(False), notifications=Notifications())
    try:
        v3.connect(Calls(refuse=True))
        delivery = v3.alerts.deliver(Alert("watch", "T", "M.", Level.HIGH, key="c"))
        assert delivery.channels == ("écrit",) and delivery.skipped["appel"] == "refusé par le canal"
        v3.connect(None)
        assert v3.alerts.deliver(Alert("watch", "T2", "M.", Level.HIGH, key="d")).channels == ()
    finally:
        v3.stop()
