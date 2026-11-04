"""Autorisations bornées accordées depuis ORION Control (POST /api/grants) : échéance et plafond exigés pour une
capacité sensible, CODE_CHANGE pour EVOLVE seul, puis effet sur la décision du moteur (hors suspension)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.store import Store  # noqa: E402

EVOLVE = {"principal": "module:evolve", "capability": "CODE_CHANGE", "limits": {"per_day": 3}, "days": 30}


def test_orion_control_grants_evolve_a_bounded_code_change():
    f = Foundation(Store())
    status, grant = f.admin("POST", ["grants"], {}, EVOLVE, session="control@test")
    assert status == 201 and grant["principal"] == "module:evolve" and grant["expires_at"]
    assert any(g["id"] == grant["id"] for g in f.admin("GET", ["grants"], {}, None, session="control@test")[1])
    gate = f.permissions.gate("module:evolve")
    assert gate.check("CODE_CHANGE", {"sandbox": True}, action="evolve:auto").reason == "autonomie suspendue"


def test_unbounded_or_foreign_grants_are_refused():
    f = Foundation(Store())
    for body in ({**EVOLVE, "days": 0}, {**EVOLVE, "days": 365}, {**EVOLVE, "limits": {}},
                 {**EVOLVE, "principal": "module:news"}, {**EVOLVE, "capability": "INCONNUE"}):
        assert f.admin("POST", ["grants"], {}, body, session="control@test")[0] == 400
    assert f.admin("GET", ["grants"], {}, None, session="control@test")[1] == []
