"""MISSIONS, lot M1 : module raccordé aux fondations (clé et données fictives uniquement).

Couvre : plan scellé (rien en clair sur disque), base verrouillée, brouillon puis validation par vous, lancement sur
votre action seulement (jamais par le suivi), exécution par de vraies tâches F2, étape sensible et étape humaine,
blocage annoncé une fois, fin annoncée, annulation des tâches, décisions réservées à l'utilisateur et inscrites au
registre, routes d'ORION Control, construction depuis la configuration."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.missions import MissionError, MissionsLocked, MissionsModule, PlanError  # noqa: E402
from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.permissions import Capability  # noqa: E402
from orion.platform.personal import PersonalStore  # noqa: E402
from orion.platform.store import PERSONAL, Store  # noqa: E402
from orion.platform.vault import DataKeyring  # noqa: E402

KEY = b"\x22" * 32  # clé factice


def world(tmp_path, key=True):
    foundation = Foundation(Store(tmp_path / "ops.db"))
    personal = PersonalStore(Store(tmp_path / "personal.db", PERSONAL),
                             DataKeyring({"k1": KEY}, "k1") if key else None)
    foundation.attach_personal(personal, DataKeyring({"k1": KEY}, "k1") if key else None)
    ran = []
    foundation.tasks.register("essai.lire", lambda run: ran.append(run.task_id) or {"lu": True}, Capability.READ)
    foundation.tasks.register("essai.noter", lambda run: ran.append(run.task_id) or {"noté": True},
                              Capability.WRITE_DATA)
    module = MissionsModule(foundation.store, foundation.tasks, events=foundation.events, ledger=foundation.ledger)
    return foundation, module, ran


STEPS = [
    {"id": "meteo", "title": "Regarder la météo à Rennes", "kind": "check", "task_kind": "essai.lire"},
    {"id": "liste", "title": "Noter la liste des affaires", "task_kind": "essai.noter", "after": ["meteo"],
     "params": {"texte": "parapluie"}},
    {"id": "train", "title": "Réserver le train", "task_kind": "essai.noter", "after": ["meteo"], "sensitive": True},
    {"id": "valise", "title": "Faire la valise", "kind": "human", "after": ["liste", "train"]},
]


def run_tasks(foundation):
    foundation.tasks.run_due(now=foundation.tasks._clock() + 1)  # noqa: SLF001


def test_a_mission_is_sealed_and_waits_for_your_validation(tmp_path):
    foundation, module, ran = world(tmp_path)
    mission = module.create("Week-end à Rennes", STEPS, by="control")
    assert mission["state"] == "draft" and mission["objective"] == "Week-end à Rennes"
    raw = sqlite3.connect(tmp_path / "ops.db").execute("SELECT plan FROM missions").fetchone()[0]
    assert "Rennes" not in raw and "parapluie" not in raw  # plan scellé : rien en clair sur disque
    module.refresh()
    with pytest.raises(MissionError):
        module.launch(mission["id"], by="control")  # brouillon : rien ne part
    assert foundation.tasks.list() == [] and ran == []


def test_steps_run_only_on_your_launch_through_real_f2_tasks(tmp_path):
    foundation, module, ran = world(tmp_path)
    mid = module.create("Week-end à Rennes", STEPS, by="control")["id"]
    module.approve_plan(mid, by="control")
    for _ in range(3):
        module.refresh()
    assert foundation.tasks.list() == []  # le suivi ne lance jamais rien
    first = module.launch(mid, by="control")
    assert first["launched"] == ["meteo"]
    task = foundation.tasks.get(next(s["task_id"] for s in first["steps"] if s["id"] == "meteo"))
    assert task.principal.startswith("delegated:") and task.created_by == "control"
    run_tasks(foundation)
    module.refresh()
    states = {s["id"]: s["state"] for s in module.view(mid)["steps"]}
    assert states == {"meteo": "done", "liste": "ready", "train": "waiting_approval", "valise": "pending"}
    assert module.launch(mid, by="control")["launched"] == ["liste"]  # l'étape sensible attend votre validation
    module.approve_step(mid, "train", by="control")
    assert module.launch(mid, by="control")["launched"] == ["train"]
    run_tasks(foundation)
    module.refresh()
    assert {s["id"]: s["state"] for s in module.view(mid)["steps"]}["valise"] == "waiting_human"
    done = module.complete_human(mid, "valise", "faite", by="control")
    assert done["state"] == "finished" and done["summary"]["succeeded"] and len(ran) == 3
    with pytest.raises(MissionError):
        module.launch(mid, by="control")  # terminée : plus rien à lancer


def test_a_blocker_that_needs_you_is_announced_once(tmp_path):
    foundation, module, _ = world(tmp_path)
    mid = module.create("Week-end à Rennes", STEPS, by="control")["id"]
    module.approve_plan(mid, by="control")
    module.launch(mid, by="control")
    run_tasks(foundation)
    assert module.refresh()["announced"] == 1  # « Réserver le train » attend votre validation
    assert module.refresh()["announced"] == 0  # pas deux fois
    published = [e for e in foundation.events.recent() if e.get("type") == "missions.blocked"]
    assert len(published) == 1 and published[0]["source"] == "missions"


def test_only_you_create_and_decide_and_every_decision_is_in_the_ledger(tmp_path):
    foundation, module, _ = world(tmp_path)
    with pytest.raises(PermissionError):
        module.create("x", STEPS, by="module:proactive")
    mid = module.create("Week-end à Rennes", STEPS, by="voix")["id"]
    with pytest.raises(PermissionError):
        module.approve_plan(mid, by="module:missions")
    module.approve_plan(mid, by="control")
    actions = [(e["action"], e["decision"]) for e in foundation.ledger.list(200)]
    assert ("missions.create:-", "deny") in actions and (f"missions.approve:{mid}", "deny") in actions
    assert (f"missions.create:{mid}", "allow") in actions and (f"missions.approve:{mid}", "allow") in actions
    assert not any("Rennes" in str(e) for e in foundation.ledger.list(200))  # jamais de texte au registre


def test_plans_are_checked_before_anything_is_stored(tmp_path):
    _, module, _ = world(tmp_path)
    with pytest.raises(PlanError):
        module.create("x", [{"id": "a", "title": "A", "task_kind": "inconnu.type"}], by="control")
    with pytest.raises(PlanError):
        module.create("x", [{"id": "a", "title": "A", "kind": "check"}], by="control")  # vérification sans tâche
    with pytest.raises(PlanError):
        module.create("x", [{"id": "a", "title": "A", "task_kind": "essai.lire", "after": ["a"]}], by="control")
    with pytest.raises(PlanError):
        module.create("x", [{"id": "a", "title": "A", "task_kind": "essai.lire", "commande": "rm"}], by="control")
    assert module.missions() == []


def test_cancelling_a_mission_cancels_its_tasks(tmp_path):
    foundation, module, ran = world(tmp_path)
    mid = module.create("Week-end à Rennes", STEPS, by="control")["id"]
    module.approve_plan(mid, by="control")
    task_id = next(s["task_id"] for s in module.launch(mid, by="control")["steps"] if s["id"] == "meteo")
    cancelled = module.cancel(mid, by="control")
    assert cancelled["state"] == "cancelled" and foundation.tasks.get(task_id).state == "cancelled"
    run_tasks(foundation)
    assert ran == []


def test_a_locked_personal_base_keeps_missions_out(tmp_path):
    _, module, _ = world(tmp_path, key=False)
    assert module.status()["locked"] is True
    with pytest.raises(MissionsLocked):
        module.create("x", STEPS, by="control")
    assert module.admin("GET", [], {}, None) == (200, {"locked": True, "missions": []})
    assert module.admin("POST", [], {}, {"objective": "x", "steps": STEPS})[0] == 423
    assert module.refresh() == {"locked": True}


def test_orion_control_routes(tmp_path):
    _, module, _ = world(tmp_path)
    code, created = module.admin("POST", [], {}, {"objective": "Week-end à Rennes", "steps": STEPS})
    assert code == 200 and created["state"] == "draft"
    mid = created["id"]
    assert module.admin("POST", [mid, "approve"], {}, None)[1]["state"] == "active"
    assert module.admin("POST", [mid, "launch"], {}, None)[1]["launched"] == ["meteo"]
    assert module.admin("POST", [mid, "steps", "train", "approve"], {}, None)[0] == 400  # pas encore en attente
    assert module.admin("GET", ["inconnue"], {}, None)[0] == 404
    listed = module.admin("GET", [], {}, None)[1]["missions"]
    assert listed[0]["objective"] == "Week-end à Rennes" and listed[0]["steps"] == 4
    assert module.admin("POST", [mid, "cancel"], {}, None)[1]["state"] == "cancelled"


def test_build_wires_missions_on_the_foundation(tmp_path):
    from orion.config import load_config
    from orion.platform.build import build_v3, classify_job

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, missions=replace(cfg.missions, enabled=True),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    modules = build_v3(cfg, foundation=Foundation(Store()))
    try:
        assert isinstance(modules.registry.get("missions"), MissionsModule)
    finally:
        modules.stop()
    assert classify_job("missions:refresh") == ("delegated:missions:suivi", Capability.NOTIFY.value)
