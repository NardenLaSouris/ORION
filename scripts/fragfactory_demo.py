"""Pile complète Frag Factory × ORION en local, pour vérifier l'interface et la chaîne sans toucher au mini-PC.

Lance, tout sur 127.0.0.1 et dans un dossier temporaire :
1. **Frag Factory réel** (son environnement, une base de test) après un auto-test qui produit un vrai clip ;
2. **l'agent Windows** avec l'action « fragfactory » ;
3. **le module ORION** relié par le vrai AgentClient, puis une synchronisation et un bilan ;
4. **l'API du Core** et **le pont d'ORION Control** : l'adresse affichée ouvre la vraie interface.

    python scripts/fragfactory_demo.py --ff-root <dossier Frag Factory> --ff-python <python de Frag Factory>

Aucune donnée réelle n'est lue ni modifiée : base de test, jetons générés pour l'occasion, Ctrl+C pour arrêter.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ff-root", required=True)
    parser.add_argument("--ff-python", required=True)
    parser.add_argument("--keep", action="store_true", help="garder le dossier temporaire à l'arrêt")
    args = parser.parse_args()

    from orion.api.server import CoreApi
    from orion.api.status import CoreStatus
    from orion.control.bridge import Bridge
    from orion.control.settings import SettingsStore
    from orion.fragfactory.agent_action import fragfactory_action
    from orion.fragfactory.engine import FragFactoryModule
    from orion.fragfactory.link import LinkClient
    from orion.platform.alerts import AlertRouter, AlertSettings
    from orion.platform.modules import ModuleRegistry
    from orion.platform.scheduler import Scheduler
    from orion.platform.store import Store
    from orion.tools import ToolRegistry
    from orion.tools.devices import AgentClient, Device
    from orion.winagent.config import AgentConfig
    from orion.winagent.server import AgentServer

    tmp = Path(tempfile.mkdtemp(prefix="ff-orion-demo-"))
    link_token, agent_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    port = free_port()
    config = tmp / "ff.toml"
    config.write_text(f'[paths]\ndata_dir = "{(tmp / "data").as_posix()}"\nwatch_dirs = []\n\n[server]\nport = {port}\n\n'
                      '[analysis]\nstt_enabled = false\n\n[ai]\nprovider = "heuristic"\n\n[render]\n'
                      'auto_render_tiers = []\n\n[orion]\nenabled = true\n', encoding="utf-8")
    env = {**os.environ, "FRAGFACTORY_CONFIG": str(config), "ORION_LINK_TOKEN": link_token, "PYTHONIOENCODING": "utf-8"}
    print("Frag Factory : auto-test (vrai clip produit sur un REC synthétique)…", flush=True)
    subprocess.run([args.ff_python, "-m", "fragfactory", "selftest"], cwd=args.ff_root, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    ff = subprocess.Popen([args.ff_python, "-m", "fragfactory", "serve"], cwd=args.ff_root, env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    actions = ToolRegistry()
    actions.register(fragfactory_action(LinkClient(f"http://127.0.0.1:{port}", token=link_token, timeout=60)))
    agent = AgentServer(AgentConfig("127.0.0.1", 0, frozenset({"127.0.0.1"}), agent_token), actions)
    agent.start()
    device = Device("pc", "le PC", "http://%s:%s" % agent.address, (), True)
    client = AgentClient(agent_token, timeout=60)
    store = Store(tmp / "orion_v3.db")
    alerts = AlertRouter(store, AlertSettings(quiet_start="00:00", quiet_end="00:00"), presence=lambda: True,
                         speak=lambda t: print(f"\n[ORION, à voix haute] {t}\n", flush=True) or True,
                         write=lambda t: print(f"\n[ORION, par écrit] {t}\n", flush=True) or True)
    module = FragFactoryModule(store, lambda op, a: client.call(device, "fragfactory", {"op": op, "args": a})["data"],
                               alerts=alerts, scheduler=Scheduler())
    module.sync()
    module.deliver_report(datetime.now().strftime("%Y-%m-%d"))
    registry = ModuleRegistry()
    registry.register(module)
    registry.start()
    api = CoreApi("127.0.0.1", 0, frozenset({"127.0.0.1"}), agent_token, tools=None, routines=None, status=CoreStatus())
    api.modules = registry
    api.start()
    host, api_port = api.address
    control = tmp / "control"
    control.mkdir()
    settings = SettingsStore(control, env_file=tmp / "absent.env", core_url=f"http://{host}:{api_port}")
    settings.set_token(agent_token)
    bridge = Bridge(settings)
    url = bridge.start()
    print(f"Frag Factory : http://127.0.0.1:{port}\nORION Control : {url.replace('/#', '/#view=fragfactory&')}"
          f"\n(Ctrl+C pour arrêter ; dossier {tmp})", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        api.stop() if hasattr(api, "stop") else None
        registry.stop()
        agent.stop()
        ff.terminate()
        ff.wait(timeout=30)
        if not args.keep:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
