"""Environnement V3 isolé (docs/v3/environnements.md) : audio absent, configuration du modèle d'installation (aucun
secret ni service de la production, ports distincts), ORION Control V3 sans repli sur le jeton de la production,
page de conversation écrite, script d'installation."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.agent import Agent, AgentSettings  # noqa: E402
from orion.audio.endpointing import EndpointerSettings, UtteranceRecorder  # noqa: E402
from orion.audio.files import ArraySource, RecordingSink  # noqa: E402
from orion.audio.silent import NullSink, SilentSource  # noqa: E402
from orion.capabilities import CapabilityRegistry  # noqa: E402
from orion.config import load_config  # noqa: E402
from orion.control.settings import SettingsStore  # noqa: E402
from orion.personality import load_personality  # noqa: E402
from orion.router import IntentRouter  # noqa: E402

PROD_PORTS = {8765, 8766, 8767, 8770}
PC, KATANA = "192.168.1.128", "192.168.1.73"


def v3_config(tmp_path):
    shutil.copy(ROOT / "config.toml", tmp_path / "config.toml")
    template = (ROOT / "deploy" / "v3_env.toml").read_text(encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(template.replace("__PC__", PC).replace("__KATANA__", KATANA),
                                                encoding="utf-8")
    return load_config(tmp_path / "config.toml")


def test_silent_source_paces_real_time_and_null_sink_plays_nothing():
    slept = []
    source = SilentSource(16000, 1280, sleep=slept.append)
    frame = source.read()
    assert frame.shape == (1280,) and not frame.any() and slept == [0.08] and source.silent
    NullSink().play(np.ones(10, np.int16), 16000)


def test_audio_mode_none_builds_no_device(tmp_path):
    from orion.factory import build_sink, build_source

    cfg = v3_config(tmp_path)
    assert isinstance(build_source(cfg), SilentSource) and isinstance(build_sink(cfg), NullSink)


def test_unknown_audio_mode_is_refused(tmp_path):
    from dataclasses import replace

    from orion.factory import build_source

    cfg = v3_config(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        build_source(replace(cfg, audio=replace(cfg.audio, mode="micro")))


def test_v3_template_isolates_from_production(tmp_path):
    cfg = v3_config(tmp_path)
    assert cfg.audio.mode == "none" and not cfg.audio.remote and not cfg.stt.remote
    assert not (cfg.mail.enabled or cfg.call.enabled or cfg.presence.enabled or cfg.lights.enabled
                or cfg.spotify.enabled)
    assert not cfg.tools.devices  # aucun agent de PC
    from orion.tools.builtin import PC_TOOLS, builtin_tools

    names = {tool.name for tool in builtin_tools(cfg.tools.settings())}
    assert names & set(PC_TOOLS) <= {"system_info"}  # rien n'agirait sur le mini-PC lui-même
    assert cfg.llm.keep_alive == "-1m" and cfg.llm.fallback_model  # Ollama partagé : rien n'est déchargé
    ports = {cfg.face.port, cfg.api.port, cfg.wallpaper.port}
    assert ports == {8865, 8866, 8867} and not ports & PROD_PORTS
    assert {str(ip) for ip in cfg.api.allowed_ips} == {PC, KATANA}
    assert cfg.wallpaper.chat and not cfg.wallpaper.actions and not cfg.face.open_browser
    assert cfg.platform.enabled and cfg.news.enabled and cfg.watch.enabled
    assert cfg.purchase.sandbox
    assert cfg.platform.personal and cfg.platform.data_key == "orion-v3.data_key"  # jamais la clé de la production


def test_wake_word_is_never_computed_on_a_silent_source():
    calls = []

    class Silent(ArraySource):
        silent = True

    class Wake:
        def process(self, frame):
            calls.append(frame)
            return 1.0

        def reset(self):
            pass

    class Tts:
        def synthesize(self, text):
            return np.zeros(10, np.int16), 16000

    source = Silent(np.full(1280 * 5, 30000, np.int16), 16000, 1280)
    router = IntentRouter(load_personality(ROOT / "personality.toml"), CapabilityRegistry())
    settings = AgentSettings("ORION", "Orion", 0.5, ("Oui ?",), 2.0, 1.0, 4)
    Agent(settings, source, RecordingSink(), Wake(), UtteranceRecorder(source, EndpointerSettings()), None, None,
          Tts(), router, lambda kind, text: None).run()
    assert calls == []


def test_control_v3_profile_never_falls_back_on_the_production_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ORION_AGENT_TOKEN=" + "p" * 40 + "\n", encoding="utf-8")
    v3 = SettingsStore(tmp_path / "v3", env_file=None, core_url="http://192.168.1.91:8866")
    assert v3.token() == "" and v3.load().core_url == "http://192.168.1.91:8866"
    v3.set_token("v" * 40)
    assert v3.token() == "v" * 40
    prod = SettingsStore(tmp_path / "prod", env_file=env)
    assert prod.token() == "p" * 40 and prod.load().core_url.endswith(":8766")


def test_control_profile_folder_is_separate(monkeypatch, tmp_path):
    from orion.control.settings import app_dir

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert app_dir("v3").name == "ORION Control V3" and app_dir().name == "ORION Control"


def test_conversation_page_is_served():
    from orion.wallpaper.server import FILES

    page, kind = FILES["/discuter"]
    text = page.read_text(encoding="utf-8")
    assert kind.startswith("text/html") and '"/say"' in text and "X-Orion-Token" in text


def test_install_script_guards():
    script = (ROOT / "deploy" / "v3_env.sh").read_text(encoding="utf-8")
    assert "InaccessiblePaths=-$PROD" in script and "existe déjà : rien n'est modifié" in script
    assert "visudo -cqf" in script and "peut lire la production" in script
    assert "ImportCredential=orion-v3.data_key" in script and "data_key\" >" not in script  # aucune clé créée
    # La production n'est que lue (environnement Python, modèles) : jamais son .env ni ses données.
    assert "$PROD/.env\" ]" not in script and "cp -a \"$PROD/data" not in script
    assert "$PROD/.env" in script.split("Contrôle de l'isolation")[1]  # seulement pour vérifier qu'il est illisible
    bash = shutil.which("bash")
    if bash:
        assert subprocess.run([bash, "-n", str(ROOT / "deploy" / "v3_env.sh")]).returncode == 0
