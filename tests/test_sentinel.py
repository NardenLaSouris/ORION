"""SENTINEL : panne du Katana détectée, annoncée après le délai, levée sans intervention ; micro-coupure silencieuse ;
ressources non configurées ignorées ; disponibilité sur 7 jours ; purge ; résumé vocal ; sondage permis pendant la
suspension de l'autonomie (lecture), alertes soumises à l'autonomie. Horloge et ressources simulées."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform import AlertRouter, AlertSettings, Store  # noqa: E402
from orion.platform.build import classify_job  # noqa: E402
from orion.platform.resources import LLM, NAS, ResourceService  # noqa: E402
from orion.sentinel import SentinelEngine, SentinelSettings  # noqa: E402
from orion.sentinel.tools import sentinel_tools  # noqa: E402

START = datetime(2026, 11, 3, 12, 0).timestamp()


class Clock:
    def __init__(self):
        self.t = START

    def __call__(self):
        return self.t


def world(permit=None):
    clock, katana = Clock(), {"up": True}
    resources = ResourceService(clock=clock, load=lambda: {"cpu": 0.1, "memory_available": 0.6})
    resources.register(LLM, lambda: (katana["up"], "ONLINE" if katana["up"] else "OFFLINE"), ttl=0)
    resources.register(NAS, lambda: (False, "non configuré"), ttl=0)
    resources.declare("NEWS", {LLM: "synthèses simples"})
    said = []
    store = Store()
    alerts = AlertRouter(store, AlertSettings(quiet_start="00:00", quiet_end="00:00"), presence=lambda: True,
                         speak=lambda text: said.append(text) or True, write=lambda text: True, clock=clock,
                         now=lambda: datetime.fromtimestamp(clock.t), permit=permit)
    engine = SentinelEngine(store, resources, alerts=alerts, settings=SentinelSettings(notify_after_seconds=120),
                            clock=clock)
    return engine, clock, katana, said, alerts


def test_a_katana_outage_is_detected_announced_after_the_delay_and_lifted_without_intervention():
    engine, clock, katana, said, alerts = world()
    engine.probe()
    katana["up"] = False
    clock.t += 60
    assert engine.probe()["opened"] == [LLM] and said == []  # détectée, pas encore annoncée
    clock.t += 60
    assert engine.probe()["notified"] == [] and said == []  # 1 min : rien
    clock.t += 60
    assert engine.probe()["notified"] == [LLM]
    assert "ne répond plus depuis 2 min" in said[-1] and "synthèses simples" in said[-1]
    assert engine.summary().startswith("Problème en cours : LLM principal (Katana) indisponible")
    clock.t += 300
    katana["up"] = True
    assert engine.probe()["closed"] == [LLM]
    assert "revenu après 7 min, sans intervention" in said[-1]
    incident = engine.incidents()[0]
    assert incident["recovery"] == "levé sans intervention" and not incident["open"] and incident["notified"] == 1
    assert engine.summary() == "Tout fonctionne normalement. 1 incident sur 7 jours, tous levés."


def test_a_short_blip_is_recorded_but_never_announced():
    engine, clock, katana, said, _ = world()
    engine.probe()
    katana["up"] = False
    clock.t += 30
    engine.probe()
    katana["up"] = True
    clock.t += 30
    assert engine.probe()["closed"] == [LLM] and said == []
    assert engine.incidents()[0]["notified"] == 0


def test_unconfigured_resources_never_open_an_incident():
    engine, clock, _, _, _ = world()
    for _ in range(3):
        clock.t += 600
        engine.probe()
    assert all(i["resource"] != NAS for i in engine.incidents())
    assert engine.availability(NAS) is None


def test_availability_counts_time_spent_in_each_state_and_old_samples_are_purged():
    engine, clock, katana, _, _ = world()
    engine.probe()
    clock.t += 3600
    katana["up"] = False
    engine.probe()
    clock.t += 3600
    assert engine.availability(LLM, days=7) == 50.0
    clock.t += 8 * 86400
    katana["up"] = True
    assert engine.availability(LLM) == 0.0  # la panne couvre toute la fenêtre de 7 jours
    engine.probe()  # retour, et purge de ce qui a plus de 7 jours
    old = engine._store.query("SELECT resource FROM sentinel_samples WHERE at < ?", (clock.t - 7 * 86400,))  # noqa
    assert [r["resource"] for r in old] == [LLM]  # seul le dernier état d'avant la fenêtre reste


def test_heartbeat_samples_without_change():
    engine, clock, _, _, _ = world()
    engine.probe()
    clock.t += 60
    engine.probe()
    clock.t += 900
    engine.probe()
    count = engine._store.one("SELECT COUNT(*) AS n FROM sentinel_samples WHERE resource = ?", (LLM,))["n"]  # noqa
    assert count == 2  # premier état, puis battement après 15 min ; rien entre les deux


def test_probing_is_a_read_allowed_while_suspended_and_alerts_obey_autonomy():
    from orion.platform.foundation import Foundation

    assert classify_job("sentinel:probe") == ("module:sentinel", "READ")
    foundation = Foundation(Store())  # autonomie suspendue par défaut
    engine, clock, katana, said, alerts = world(permit=foundation.permissions.decide)
    engine.probe()
    katana["up"] = False
    clock.t += 60
    engine.probe()
    clock.t += 200
    engine.probe()
    assert said == []  # initiative du module : refusée pendant la suspension...
    blocked = [a for a in alerts.history(10, "sentinel")]
    assert blocked and blocked[0]["status"] == "bloquée"  # ... mais gardée dans l'historique
    assert engine.incidents()[0]["open"]


def test_voice_tool_and_routes():
    engine, clock, katana, _, _ = world()
    tool = sentinel_tools(engine)[0]
    assert tool.run()["text"].startswith("Tout fonctionne normalement")
    status, body = engine.admin("GET", [], {}, None)
    assert status == 200 and {r["name"] for r in body["resources"]} == {LLM, NAS}
    assert engine.admin("POST", ["probe"], {}, None)[0] == 200
    assert engine.admin("GET", ["history"], {"resource": [LLM]}, None)[1]


def test_build_needs_the_foundation(tmp_path):
    from orion.config import load_config
    from orion.platform.build import build_v3
    from orion.platform.foundation import Foundation

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, sentinel=replace(cfg.sentinel, enabled=True),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    alone = build_v3(cfg)
    try:
        assert alone.registry.get("sentinel") is None  # sans service de ressources : non démarré
    finally:
        alone.stop()
    cfg = replace(cfg, modules=replace(cfg.modules, database=tmp_path / "v3b.db"))
    modules = build_v3(cfg, foundation=Foundation(Store()))
    try:
        assert modules.registry.get("sentinel") is not None
        assert "sentinel_status" in {t.name for t in modules.tools}
    finally:
        modules.stop()
