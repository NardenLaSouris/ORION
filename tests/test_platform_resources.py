"""Lot F5 : service de ressources (disponibilité, délais, transitions, charge), tâches lourdes différées, zones
d'attente des comptes séparés, tests « ressource absente » par module, scripts des comptes (non installés)."""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orion.platform import resources as rs  # noqa: E402
from orion.platform.autonomy import VOICE  # noqa: E402
from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.staging import Kind, NotNow, Rejected, StagingInbox, batch, deposit, digest  # noqa: E402
from orion.platform.store import Store  # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --- Service de ressources ------------------------------------------------------------------------------------

def test_availability_is_cached_failures_and_unknowns_are_unavailable_and_changes_are_dated():
    clock, state, calls = Clock(), {"up": True}, []

    def probe():
        calls.append(1)
        return state["up"], "katana"

    service = rs.ResourceService(clock=clock, load=lambda: {})
    service.register(rs.LLM, probe, ttl=30)
    service.register(rs.STT, lambda: 1 / 0)
    assert service.available(rs.LLM) and service.available(rs.LLM) and len(calls) == 1  # gardé 30 s
    assert not service.available(rs.STT) and not service.available("inconnue")
    state["up"] = False
    clock.t += 31
    assert not service.available(rs.LLM)
    assert service.changes[-1] == {"at": clock.t, "resource": rs.LLM, "available": False, "detail": "katana"}
    row = next(r for r in service.status(refresh=False)["resources"] if r["name"] == rs.LLM)
    assert row["since"] == clock.t and row["available"] is False


def test_a_hanging_probe_never_blocks_the_caller():
    release = threading.Event()
    service = rs.ResourceService(load=lambda: {}, timeout=0.2)
    service.register(rs.NAS, lambda: (release.wait(5), "jamais"))
    assert not service.available(rs.NAS)
    assert "pas de réponse" in service.status(refresh=False)["resources"][0]["detail"]
    release.set()


def test_busy_when_talking_loaded_or_short_of_memory():
    load = {"cpu": 0.2, "memory_available": 0.5}
    talking = {"on": False}
    service = rs.ResourceService(load=lambda: dict(load), in_conversation=lambda: talking["on"])
    assert service.busy() == ""
    talking["on"] = True
    assert service.busy() == "conversation en cours"
    talking["on"] = False
    load["cpu"] = 0.95
    assert service.busy().startswith("processeur chargé")
    load.update(cpu=0.1, memory_available=0.05)
    assert service.busy().startswith("mémoire presque pleine")


def test_nas_probe_needs_a_writable_mounted_folder(tmp_path):
    assert rs.directory_probe("")() == (False, "non configuré")
    assert rs.directory_probe(tmp_path / "absent")()[0] is False
    assert rs.directory_probe(tmp_path)() == (True, str(tmp_path))
    assert not list(tmp_path.iterdir())  # la sonde ne laisse rien


def test_heavy_tasks_wait_for_a_free_mini_pc_without_failing():
    foundation = Foundation(Store())
    ran = []
    foundation.tasks.register("evolve.analyse", lambda run: ran.append(1), "READ", heavy=True)
    busy = {"reason": "conversation en cours"}
    foundation.tasks.busy = lambda: busy["reason"]
    task = foundation.tasks.schedule_for_user("evolve.analyse", by=VOICE)
    foundation.tasks.run_due()
    assert ran == [] and foundation.tasks.get(task.id).state == "pending" and foundation.tasks.get(task.id).attempts == 0
    busy["reason"] = ""
    foundation.tasks.run_due()
    assert ran == [1] and foundation.tasks.get(task.id).state == "done"


def test_resources_route_shows_state_and_what_works_without(tmp_path):
    from orion.config import load_config
    from orion.factory import configure_resources

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, platform=replace(cfg.platform, nas_path=str(tmp_path / "nas-absent")),
                  stt=replace(cfg.stt, remote=""), web=replace(cfg.web, enabled=False),
                  news=replace(cfg.news, enabled=True))

    class DegradedLLM:
        degraded, state = True, "OFFLINE"

    foundation = Foundation(Store())
    configure_resources(cfg, foundation.resources, DegradedLLM(), None)
    status, body = foundation.admin("GET", ["resources"], {}, None, "s")
    rows = {r["name"]: r for r in body["resources"]}
    assert status == 200 and rows[rs.LLM]["available"] is False and rows[rs.NAS]["available"] is False
    assert rows[rs.STT]["detail"] == "non configuré"
    assert any(w["module"] == "NEWS" for w in rows[rs.LLM]["without"])
    assert all(r["without"] for r in rows.values())  # chaque ressource dit ce qui se passe sans elle


# --- « Ressource absente », module par module -------------------------------------------------------------------

def test_news_without_llm_still_composes_a_journal_from_the_sources():
    from test_news import GRAVE, TWIN, World

    w = World(home=True)  # aucun LLM : synthèses par règles
    w.publish("https://afp.test/rss", *GRAVE)
    w.publish("https://lemonde.test/rss", *TWIN)
    w.engine.collect()
    journal = w.engine.latest_journal() or w.engine._journal_row(  # noqa: SLF001
        w.store.one("SELECT * FROM news_journals WHERE id = ?", (w.engine.journal(),)))
    assert journal["items"] and all(i["summary_by"] in ("source", "règles") for i in journal["items"])


def test_evolve_without_llm_still_diagnoses_by_rules():
    from orion.evolve.engine import EvolveEngine

    engine = EvolveEngine(Store(), None)
    report = engine.scan(lines=["2026-11-03 10:00:00 ERROR orion.agent : Erreur imprévue pendant la conversation"])
    assert isinstance(report, dict)


def test_watch_without_web_keeps_its_declared_sources(tmp_path):
    from orion.config import load_config
    from orion.platform.build import build_v3

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, watch=replace(cfg.watch, enabled=True), web=replace(cfg.web, enabled=False),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    modules = build_v3(cfg)
    try:
        assert "web" not in modules.registry.get("watch").connectors.names()
    finally:
        modules.stop()


# --- Zones d'attente --------------------------------------------------------------------------------------------

def facts_kind(received, locked=None):
    def check(item):
        if not isinstance(item, dict) or set(item) != {"topic", "weight"} or not isinstance(item["weight"], int):
            raise Rejected("fait hors schéma")

    def accept(lot):
        if locked and locked["on"]:
            raise NotNow("base personnelle verrouillée")
        received.append(lot)

    return Kind("interests", check, accept)


def test_a_verified_batch_is_accepted_once_and_archived(tmp_path):
    received = []
    inbox = StagingInbox(tmp_path, [facts_kind(received)])
    deposit(tmp_path, batch("interests", "takeout-factice", [{"topic": "astronomie", "weight": 3}]), "lot1")
    report = inbox.scan()
    assert report["accepted"] == ["lot1.json"] and received[0]["items"][0]["topic"] == "astronomie"
    assert inbox.scan()["accepted"] == [] and len(list((tmp_path / "done").iterdir())) == 1


@pytest.mark.parametrize("mutate, reason", [
    (lambda lot: lot.update(sha256="0" * 64), "empreinte fausse"),
    (lambda lot: lot.update(kind="shell"), "non déclaré"),
    (lambda lot: (lot["items"].append({"topic": "x", "weight": "beaucoup"}),
                  lot.update(sha256=digest(lot["items"]))), "élément 2"),  # empreinte juste, élément hors schéma
    (lambda lot: lot.update(extra=1), "format inconnu"),
])
def test_tampered_or_foreign_batches_go_to_quarantine_with_their_reason(tmp_path, mutate, reason):
    received = []
    inbox = StagingInbox(tmp_path, [facts_kind(received)])
    lot = batch("interests", "x", [{"topic": "a", "weight": 1}])
    mutate(lot)
    (tmp_path / "lot.json").write_text(json.dumps(lot), encoding="utf-8")
    report = inbox.scan()
    assert reason in report["rejected"]["lot.json"] and received == []
    kept = list((tmp_path / "quarantine").iterdir())
    assert any(p.suffix == ".json" for p in kept) and any(p.name.endswith(".raison.txt") for p in kept)  # rien supprimé


def test_a_batch_waits_intact_while_the_personal_base_is_locked(tmp_path):
    received, locked = [], {"on": True}
    inbox = StagingInbox(tmp_path, [facts_kind(received, locked)])
    deposit(tmp_path, batch("interests", "x", [{"topic": "a", "weight": 1}]), "lot")
    assert inbox.scan()["waiting"] == {"lot.json": "base personnelle verrouillée"} and (tmp_path / "lot.json").exists()
    locked["on"] = False
    assert inbox.scan()["accepted"] == ["lot.json"]


@pytest.mark.skipif(os.name != "posix", reason="liens symboliques et propriétaires : Linux")
def test_symlinks_and_foreign_owners_are_refused(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(batch("interests", "x", [])), encoding="utf-8")
    zone = tmp_path / "zone"
    zone.mkdir()
    (zone / "lien.json").symlink_to(secret)
    assert "lien symbolique" in StagingInbox(zone, [facts_kind([])]).scan()["rejected"]["lien.json"]
    deposit(zone, batch("interests", "x", []), "autre")
    assert "autre compte" in StagingInbox(zone, [facts_kind([])], owner_uid=os.getuid() + 1).scan()["rejected"][
        "autre.json"]


# --- Scripts des comptes (non installés) ----------------------------------------------------------------------

def test_accounts_script_never_grants_access_to_the_key_or_the_personal_base():
    script = (ROOT / "deploy" / "v3_accounts.sh").read_text(encoding="utf-8")
    assert "NON INSTALLÉ" in script and "orion-import" in script and "orion-evolve" in script
    for path in ('"$DATA/orion_personal.db"', "/opt/orion-v3/.env", "/etc/credstore.encrypted", "/opt/orion"):
        assert path in script  # chacun vérifié illisible à la fin
    assert "usermod -aG orion-v3" not in script and "-aG orion-import,orion-evolve orion-v3" in script
    assert "ReadWritePaths=%s/staging" in script and "systemctl restart orion\n" not in script
