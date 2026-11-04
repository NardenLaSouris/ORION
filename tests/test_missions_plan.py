"""MISSIONS, noyau de planification : validation (cycles, dépendances, natures), étapes prêtes, propagation de l'état
des tâches F2, étapes sensibles à valider, étapes humaines, blocages, révision sans refaire ce qui est fait, bilan."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orion.missions import Plan, PlanError, Step  # noqa: E402

T0 = 1_793_750_000.0


def trip() -> Plan:
    """Mission fictive : organiser un week-end à Rennes."""
    return Plan("Organiser un week-end à Rennes", [
        Step("meteo", "Regarder la météo", "check", task_kind="read.weather"),
        Step("train", "Chercher les trains", "check", task_kind="read.web"),
        Step("choix", "Choisir l'horaire", "human", after=["train"]),
        Step("achat", "Acheter les billets", "action", after=["choix", "meteo"], task_kind="purchase.order",
             sensitive=True),
        Step("rappel", "Programmer un rappel la veille", "action", after=["achat"], task_kind="reminder.create"),
    ])


def test_validation_refuses_cycles_unknown_dependencies_and_actions_without_task_type():
    trip().validate()
    with pytest.raises(PlanError, match="cycle"):
        Plan("x", [Step("a", "A", "check", after=["b"], task_kind="t"),
                   Step("b", "B", "check", after=["a"], task_kind="t")]).validate()
    with pytest.raises(PlanError, match="inconnue"):
        Plan("x", [Step("a", "A", "check", after=["z"], task_kind="t")]).validate()
    with pytest.raises(PlanError, match="type de tâche"):
        Plan("x", [Step("a", "A", "action")]).validate()
    with pytest.raises(PlanError):
        Plan("", [Step("a", "A", "human")]).validate()
    with pytest.raises(PlanError, match="double"):
        Plan("x", [Step("a", "A", "human"), Step("a", "B", "human")]).validate()


def test_progress_follows_tasks_and_waits_for_you_where_needed():
    plan = trip()
    assert {s.id for s in plan.refresh(now=T0)} == {"meteo", "train"}
    plan.step("meteo").task_id, plan.step("train").task_id = "t1", "t2"
    plan.refresh({"t1": "done", "t2": "done"}, now=T0)
    assert plan.step("choix").state == "waiting_human"
    assert plan.step("achat").state == "pending"
    plan.complete_human("choix", "train de 9 h 12")
    plan.refresh({"t1": "done", "t2": "done"}, now=T0)
    assert plan.step("achat").state == "waiting_approval"  # étape sensible : jamais sans votre validation
    assert any(b.step == "achat" and b.needs_you for b in plan.blockers(now=T0))
    plan.approve("achat")
    assert [s.id for s in plan.refresh({"t1": "done", "t2": "done"}, now=T0)] == ["achat"]
    plan.step("achat").task_id = "t3"
    plan.refresh({"t1": "done", "t2": "done", "t3": "held"}, now=T0)  # F2 demande une confirmation
    assert plan.step("achat").state == "waiting_human"
    plan.refresh({"t1": "done", "t2": "done", "t3": "done"}, now=T0)
    plan.step("rappel").task_id = "t4"
    plan.refresh({"t1": "done", "t2": "done", "t3": "done", "t4": "done"}, now=T0)
    assert plan.summary()["succeeded"] and plan.report().endswith("Objectif atteint.")


def test_a_failed_step_blocks_what_depends_on_it_and_is_reported():
    plan = trip()
    plan.step("train").task_id = "t2"
    plan.refresh({"t2": "failed"}, now=T0)
    plan.step("train").note = "site injoignable"
    plan.refresh({"t2": "failed"}, now=T0)
    assert plan.step("choix").state == "blocked" and plan.step("achat").state == "blocked"
    blockers = plan.blockers(now=T0)
    assert blockers[0].step == "train" and "site injoignable" in blockers[0].reason
    assert "Bloquée : Chercher les trains" in plan.report()


def test_long_waits_are_flagged():
    plan = trip()
    plan.step("train").task_id = "t2"
    plan.refresh({"t2": "running"}, now=T0)
    assert plan.blockers(now=T0 + 3600) == []
    assert plan.blockers(now=T0 + 25 * 3600)[0].reason == "en cours depuis plus de 24 h"


def test_revision_keeps_finished_steps_and_reasks_a_changed_sensitive_step():
    plan = trip()
    plan.step("meteo").state = "done"
    plan.step("achat").approved = True
    revised = plan.revise([
        Step("meteo", "Regarder la météo (refaite ?)", "check", task_kind="read.weather"),
        Step("achat", "Acheter les billets en 1re", "action", after=["meteo"], task_kind="purchase.order",
             params={"classe": 1}, sensitive=True),
    ])
    assert revised.revision == 2
    assert revised.step("meteo").state == "done" and revised.step("meteo").title == "Regarder la météo"
    assert revised.step("achat").approved is False  # contenu changé : nouvelle validation
    same = plan.revise([Step("achat", "Acheter", "action", task_kind="purchase.order", sensitive=True)])
    assert same.step("achat").approved is True  # même action, mêmes paramètres : la validation reste


def test_round_trip_and_size_limit():
    plan = trip()
    assert Plan.from_dict(plan.as_dict()).as_dict() == plan.as_dict()
    with pytest.raises(PlanError):
        Plan("x", [Step(f"s{i}", "S", "human") for i in range(41)]).validate()
