"""Suites d'une liste de rappels : « supprime le premier », « supprime aussi 4 », « 3 et 4 », « supprime-les tous »,
« tous » en réponse à « Lequel ? » ; pluriels (« les noms de domaines »). Cas tirés des messages Linphone réels du
2 novembre 2026 (« Supprime le premier » visait un fichier, « Tous » mettait ORION en veille). Rappels factices."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.scheduling import TimerManager  # noqa: E402
from orion.tools import ToolRegistry  # noqa: E402
from orion.tools.base import ToolError  # noqa: E402
from orion.tools.quick import QuickPlanner  # noqa: E402
from orion.tools.timers import shown_reminders, timer_tools  # noqa: E402


@pytest.fixture
def world():
    manager = TimerManager(max_seconds=30 * 86400)
    tools = {t.name: t for t in timer_tools(manager)}
    registry = ToolRegistry()
    for tool in tools.values():
        registry.register(tool)
    registry.context["reminders.shown"] = lambda: shown_reminders(manager)
    ids = [manager.create_reminder(3600 * (i + 1), text).id for i, text in
           enumerate(["scanner le QR code", "renouveler mes noms de domaine", "renouveler mes noms de domaine"])]
    return manager, tools, QuickPlanner(registry), ids


def plan(planner, text):
    call = planner.one(text)
    return (call["tool"], call["parameters"]) if call else None


def test_after_a_listing_ordinals_numbers_and_all_target_the_reminders_never_a_file(world):
    manager, tools, planner, ids = world
    assert plan(planner, "Supprime le premier") is None  # aucune liste dite : rien n'est deviné
    tools["list_reminders"].execute({})
    assert plan(planner, "Supprime le premier") == ("cancel_reminder", {"reminder_id": ids[0]})
    assert plan(planner, "supprime le dernier") == ("cancel_reminder", {"reminder_id": ids[2]})
    assert plan(planner, "Supprime aussi 3") == ("cancel_reminder", {"reminder_id": ids[2]})
    assert plan(planner, "supprime le 2 et le 3") == ("cancel_all_reminders",
                                                       {"reminder_ids": f"{ids[1]},{ids[2]}"})
    assert plan(planner, "Supprime-les tous") == ("cancel_all_reminders", {"reminder_ids": ",".join(ids)})
    assert plan(planner, "Supprime-les") == ("cancel_all_reminders", {"reminder_ids": ",".join(ids)})
    assert plan(planner, "supprime") is None  # rien de désigné
    assert plan(planner, "Tous") is None  # sans verbe, seulement en réponse à « lequel ? »
    assert plan(planner, "supprime le fichier rapport") is None  # autre chose : décidé ailleurs


def test_answers_to_which_one_target_the_candidates(world):
    manager, tools, planner, ids = world
    with pytest.raises(ToolError):
        tools["cancel_reminder"].execute({"message": "les noms de domaines"})  # deux candidats (pluriel compris)
    assert plan(planner, "Tous") == ("cancel_all_reminders", {"reminder_ids": f"{ids[1]},{ids[2]}"})
    assert plan(planner, "3") == ("cancel_reminder", {"reminder_id": ids[2]})  # numéro de rappel dit par ORION
    assert plan(planner, "3 et 2") == ("cancel_all_reminders", {"reminder_ids": f"{ids[2]},{ids[1]}"})
    assert plan(planner, "les deux") == ("cancel_all_reminders", {"reminder_ids": f"{ids[1]},{ids[2]}"})


def test_cancelling_several_by_number_asks_first_then_cancels_only_those(world):
    manager, tools, _, ids = world
    tool = tools["cancel_all_reminders"]
    params = {"reminder_ids": f"{ids[1]},{ids[2]}"}
    assert "les 2 rappels" in tool.question(params) and "QR" not in tool.question(params)
    assert tool.execute(params)["count"] == 2
    assert [r.id for r in manager.reminders()] == [ids[0]]


def test_plural_words_find_the_reminder(world):
    manager, tools, _, ids = world
    manager.cancel_reminder(ids[2])
    done = tools["cancel_reminder"].execute({"message": "les noms de domaines"})
    assert done["reminder_id"] == ids[1]


def test_a_cancelled_reminder_drops_out_of_the_shown_list(world):
    manager, tools, planner, ids = world
    tools["list_reminders"].execute({})
    manager.cancel_reminder(ids[0])
    assert plan(planner, "supprime le premier") == ("cancel_reminder", {"reminder_id": ids[1]})


def test_allume_le_follows_the_last_action_never_a_guess():
    """« Ouvre Prime Video sur le PC » puis « Allume-le » (3 novembre 2026) allumait les lumières."""
    import time as clock

    from orion.tools import Param, Risk, Tool

    registry = ToolRegistry()
    for name in ("wake_pc", "light_on", "open_application"):
        registry.register(Tool(name, name, {"device": Param(str, "appareil", required=False),
                                             "room": Param(str, "pièce", required=False)}, {}, Risk.SAFE,
                               lambda **kw: kw))
    planner = QuickPlanner(registry)
    assert plan(planner, "Allume-le") is None  # rien de récent : rien n'est deviné
    registry.context["last.action"] = {"at": clock.monotonic(), "tool": "open_application",
                                       "parameters": {"name": "Prime Video", "device": "pc"}, "success": False}
    assert plan(planner, "Allume-le") == ("wake_pc", {"device": "pc"})
    registry.context["last.action"] = {"at": clock.monotonic(), "tool": "light_off", "parameters": {"room": "chambre"},
                                       "success": True}
    assert plan(planner, "rallume la") == ("light_on", {"room": "chambre"})
    registry.context["last.action"]["at"] -= 600
    assert plan(planner, "rallume la") is None  # trop ancien