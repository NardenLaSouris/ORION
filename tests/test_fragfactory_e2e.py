"""Chaîne réelle Frag Factory → agent Windows → module ORION (aucune simulation).

- Frag Factory : **vrai serveur** (``python -m fragfactory serve``), dans son propre environnement, sur une base de
  test. Il a d'abord produit un vrai clip (``selftest`` : REC synthétique analysé puis rendu en 9:16 par FFmpeg).
- Agent : **vrai serveur** ``AgentServer``, réglé comme l'agent Windows V3 (sans audio ni outils du PC), avec l'action
  ``fragfactory`` et un vrai jeton de liaison.
- Core : module ORION avec le **vrai** transport du Core (``agent_transport`` : agent V3, jeton
  ``ORION_V3_AGENT_TOKEN``, jamais celui de la production) et le vrai ``ToolCore``.

Ce qui n'est pas couvert : le mini-PC de production et le réseau local (tout tourne sur 127.0.0.1 de ce PC).

Lancement (ignoré sans ces variables) :
    FRAGFACTORY_ROOT=<dossier de Frag Factory> FRAGFACTORY_PYTHON=<python de Frag Factory> pytest tests/test_fragfactory_e2e.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

ROOT = os.environ.get("FRAGFACTORY_ROOT")
PYTHON = os.environ.get("FRAGFACTORY_PYTHON")
pytestmark = pytest.mark.skipif(not (ROOT and PYTHON), reason="FRAGFACTORY_ROOT et FRAGFACTORY_PYTHON non définis")

LINK_TOKEN = "lien-" + "k" * 40
AGENT_TOKEN = "agent-" + "a" * 40


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FragFactoryProcess:
    def __init__(self, tmp: Path):
        self.port = _free_port()
        self.config = tmp / "ff.toml"
        data = (tmp / "data").as_posix()
        self.config.write_text(
            f'[paths]\ndata_dir = "{data}"\nwatch_dirs = []\n\n[server]\nport = {self.port}\n\n'
            '[analysis]\nstt_enabled = false\n\n[ai]\nprovider = "heuristic"\n\n'
            '[render]\nauto_render_tiers = []\n\n[orion]\nenabled = true\n', encoding="utf-8")
        self.env = {**os.environ, "FRAGFACTORY_CONFIG": str(self.config), "ORION_LINK_TOKEN": LINK_TOKEN,
                    "PYTHONIOENCODING": "utf-8"}
        self.proc: subprocess.Popen | None = None

    def selftest(self) -> str:
        out = subprocess.run([PYTHON, "-m", "fragfactory", "selftest"], cwd=ROOT, env=self.env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=600)
        assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
        return out.stdout

    def start(self) -> None:
        self.proc = subprocess.Popen([PYTHON, "-m", "fragfactory", "serve"], cwd=ROOT, env=self.env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1)
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("Frag Factory n'a pas démarré")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=30)
            self.proc = None


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    from orion.fragfactory.agent_action import fragfactory_action
    from orion.fragfactory.engine import FragFactoryModule
    from orion.fragfactory.link import LinkClient
    from orion.fragfactory.tools import fragfactory_tools
    from orion.platform.alerts import AlertRouter, AlertSettings
    from orion.platform.scheduler import Scheduler
    from orion.platform.store import Store
    from orion.tools import ToolRegistry
    from orion.tools.core import ToolCore
    from orion.tools.permissions import PermissionManager
    from orion.winagent.config import AgentConfig
    from orion.winagent.server import AgentServer

    ff = FragFactoryProcess(tmp_path_factory.mktemp("ff"))
    selftest = ff.selftest()  # vrai clip produit par la vraie chaîne d'analyse et de rendu
    ff.start()
    actions = ToolRegistry()
    actions.register(fragfactory_action(LinkClient(f"http://127.0.0.1:{ff.port}", token=LINK_TOKEN, timeout=60)))
    agent = AgentServer(AgentConfig("127.0.0.1", 0, frozenset({"127.0.0.1"}), AGENT_TOKEN, audio=False,
                                    pc_tools=False), actions)
    agent.start()
    host, port = agent.address
    from dataclasses import replace

    from orion.config import load_config
    from orion.fragfactory import agent_transport

    os.environ["ORION_V3_AGENT_TOKEN"] = AGENT_TOKEN
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.toml")
    cfg = replace(cfg, fragfactory=replace(cfg.fragfactory, enabled=True, agent_url=f"http://{host}:{port}",
                                           timeout=60.0))
    transport = agent_transport(cfg)

    spoken, written = [], []
    store = Store()
    alerts = AlertRouter(store, AlertSettings(quiet_start="00:00", quiet_end="00:00"), presence=lambda: True,
                         speak=lambda t: spoken.append(t) or True, write=lambda t: written.append(t) or True)
    module = FragFactoryModule(store, transport, alerts=alerts, scheduler=Scheduler())
    registry = ToolRegistry()
    for tool in fragfactory_tools(module):
        registry.register(tool)
    core = ToolCore(registry, PermissionManager())

    class Chain:
        pass

    c = Chain()
    c.ff, c.agent, c.module, c.core, c.registry, c.spoken, c.written = ff, agent, module, core, registry, spoken, written
    c.transport, c.selftest = transport, selftest
    yield c
    agent.stop()
    ff.stop()


def ask(chain, tool, text, **params):
    data = {"tool": tool, "parameters": dict(params)}
    for key, p in chain.registry.get(tool).parameters.items():
        if p.hidden and p.resolve is not None and p.resolve(text) is not None:
            data["parameters"][key] = p.resolve(text)
    return chain.core.submit(data)


def test_real_chain_end_to_end(chain):
    # 1. connexion réelle et état
    status = chain.transport("status", {})
    assert status["ok"] is True and status["clips"] >= 1

    # 2. synchronisation : le clip rendu par Frag Factory est annoncé avec ses vraies données
    summary = chain.module.sync()
    assert summary["received"] >= 1 and chain.spoken, "aucune annonce"
    announcement = chain.spoken[0]
    clips = chain.transport("clips", {"status": "PENDING_REVIEW", "limit": 10})["items"]
    rendered = [c for c in clips if chain.transport("clip", {"clip_id": c["id"]})["rendered"]]
    assert rendered and rendered[0]["title"][:30] in announcement
    assert chain.transport("status", {})["outbox_pending"] == 0  # tout a été acquitté
    assert chain.module.sync()["received"] == 0  # rien n'est relu ni réannoncé

    # 3. analyse du style d'un vrai rendu (mesure du fichier MP4 produit)
    target = rendered[0]["id"]
    style = ask(chain, "fragfactory_analyze_clip_style", f"quel est le style de montage du clip numéro {target} ?")
    assert style.status == "done", style.result
    assert "Version 1" in style.result.result["text"] and "coupes par minute" in style.result.result["text"]

    # 4. validation explicite, confirmée, par la vraie chaîne
    out = ask(chain, "fragfactory_approve_clip", f"valide le clip numéro {target}")
    assert out.status == "confirm" and f"clip {target}" in out.question
    done = chain.core.answer("oui")
    assert done.status == "done", done.result
    assert chain.transport("clip", {"clip_id": target})["status"] == "APPROVED"
    again = ask(chain, "fragfactory_approve_clip", f"valide le clip numéro {target}")
    assert again.result is not None and again.result.success is False  # plus en attente : refusé avant confirmation

    # 5. proposition de montage puis nouvelle version, qui revient en validation sans écraser la première
    rec = ask(chain, "fragfactory_recommend_editing_style", f"propose un montage nerveux pour le clip numéro {target}")
    assert "Version nerveuse" in rec.result.result["text"]
    out = ask(chain, "fragfactory_request_render", f"produis la version 1 du clip numéro {target}", proposition=1)
    assert out.status == "confirm"
    assert chain.core.answer("oui").status == "done"
    deadline = time.time() + 300
    while time.time() < deadline:
        chain.module.sync()
        if any("version 2" in t for t in chain.spoken):
            break
        time.sleep(3)
    assert any("version 2" in t for t in chain.spoken), "la nouvelle version n'a pas été annoncée"
    versions = chain.transport("clip", {"clip_id": target})["versions"]
    assert [v["version"] for v in versions][:2] == [1, 2] and versions[0]["review_status"] == "APPROVED"

    # 6. rapport du matin composé par Frag Factory, livré une seule fois
    day = datetime.now().strftime("%Y-%m-%d")
    report = chain.module.deliver_report(day)
    assert report["report_id"] and any(t.startswith("Bonjour Monsieur. Voici le bilan") for t in chain.spoken)
    assert chain.module.report_due() is None  # déjà fait aujourd'hui

    # 7. jeton de liaison refusé : erreur claire, jamais un succès
    from orion.fragfactory.link import LinkClient, LinkError
    with pytest.raises(LinkError) as exc:
        LinkClient(f"http://127.0.0.1:{chain.ff.port}", token="z" * 40).call("status")
    assert exc.value.http == 401

    # 8. Frag Factory arrêté puis relancé : indisponibilité signalée, événements conservés et relus
    chain.ff.stop()
    from orion.fragfactory.engine import Unavailable
    with pytest.raises(Unavailable):
        chain.module.sync()
    assert chain.module.status()["connected"] is False
    chain.ff.start()
    assert chain.module.sync() is not None and chain.module.status()["connected"] is True
    if os.environ.get("FRAGFACTORY_E2E_VERBOSE"):
        print("\n--- Ce qu'ORION a dit ---\n" + "\n\n".join(chain.spoken))
