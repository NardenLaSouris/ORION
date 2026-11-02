"""Modules V3 dans l'API d'administration (/api/modules) et dans ORION Control (pont /bridge/modules) : une seule
route générique, protégée comme le reste (IP autorisée, jeton), relayée telle quelle par le pont."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from orion.platform import BaseModule, ModuleRegistry  # noqa: E402
from test_control import bridge, call, world  # noqa: E402,F401,F811


class Echo(BaseModule):
    name, title = "echo", "Écho"

    def status(self):
        return {"surveillances": 2}

    def admin(self, method, parts, query, data):
        return 200, {"method": method, "parts": parts, "query": query, "data": data}


def registry():
    r = ModuleRegistry()
    r.register(Echo())
    r.start()
    return r


def test_modules_route_needs_the_token_and_reports_disabled(world):
    assert world.call("GET", "/modules")[0] == 404  # aucun module V3 activé : comme V2
    world.api.modules = registry()
    assert world.call("GET", "/modules", token=None)[0] == 401
    status, data = world.call("GET", "/modules")
    assert status == 200 and data[0]["name"] == "echo" and data[0]["state"] == "actif"
    assert data[0]["details"] == {"surveillances": 2}


def test_module_routes_are_passed_through(world):
    world.api.modules = registry()
    status, data = world.call("POST", "/modules/echo/w1/pause?force=1", {"motif": "test"})
    assert status == 200 and data == {"method": "POST", "parts": ["w1", "pause"], "query": {"force": ["1"]},
                                      "data": {"motif": "test"}}
    assert world.call("GET", "/modules/inconnu")[0] == 404


def test_bridge_relays_module_routes(bridge, world):
    world.api.modules = registry()
    status, data = call(bridge, "GET", "/modules")
    assert status == 200 and data["data"][0]["title"] == "Écho"
    relayed = call(bridge, "PUT", "/modules/echo/r%C3%A9glages?vue=1", {"heure": "08:00"})[1]["data"]
    assert relayed == {"method": "PUT", "parts": ["réglages"], "query": {"vue": ["1"]}, "data": {"heure": "08:00"}}
    assert call(bridge, "DELETE", "/modules/echo/w1")[1]["data"]["method"] == "DELETE"
