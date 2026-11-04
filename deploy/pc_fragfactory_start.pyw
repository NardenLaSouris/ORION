"""Lancement supervisé de Frag Factory (raccourci « Frag Factory » du dossier Démarrage, sans fenêtre).

Installation persistante : C:\\Users\\theju\\FragFactory (code, config.toml, data, .venv). Le jeton de liaison avec l'agent
ORION (FRAGFACTORY_LINK_TOKEN de C:\\Users\\theju\\.orion\\data\\.env) est passé par l'environnement, jamais écrit
ailleurs ni journalisé. Relance après un arrêt inattendu (attente croissante jusqu'à 5 minutes si l'arrêt se répète).
    arrêt propre : créer le fichier C:\\Users\\theju\\.orion\\fragfactory\\stop (le superviseur arrête Frag Factory et
    s'arrête lui-même ; le fichier est retiré au lancement suivant).
Un seul superviseur à la fois ; si le port 8742 est déjà pris (autre instance), il ne lance rien.
"""

from __future__ import annotations

import msvcrt
import os
import socket
import subprocess
import time
from pathlib import Path

FF = Path.home() / "FragFactory"
PYTHON = FF / ".venv" / "Scripts" / "python.exe"
ENV_FILE = Path.home() / ".orion" / "data" / ".env"
HOME = Path.home() / ".orion" / "fragfactory"
STOP = HOME / "stop"
PORT = 8742
NO_WINDOW = 0x08000000


def log(text: str) -> None:
    with open(HOME / "supervise.log", "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def token() -> str | None:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("FRAGFACTORY_LINK_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def stop_tree(proc: subprocess.Popen) -> None:
    # python.exe d'un venv Windows relance l'interpréteur de base : arrêt de toute l'arborescence.
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)


def main() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    lock = open(HOME / "supervise.lock", "w")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # un superviseur tourne déjà
    STOP.unlink(missing_ok=True)
    if listening():
        log(f"port {PORT} déjà occupé : rien n'est lancé (une autre instance tourne)")
        return
    delay = 5.0
    while not STOP.exists():
        secret = token()
        if not secret:
            log("FRAGFACTORY_LINK_TOKEN absent de .orion\\data\\.env : lancement sans liaison ORION")
        env = dict(os.environ, FRAGFACTORY_CONFIG=str(FF / "config.toml"), PYTHONIOENCODING="utf-8")
        if secret:
            env["ORION_LINK_TOKEN"] = secret
        started = time.monotonic()
        with open(HOME / "serve.log", "ab") as err:
            proc = subprocess.Popen([str(PYTHON), "-m", "fragfactory", "serve"], cwd=FF, env=env,
                                    stdout=subprocess.DEVNULL, stderr=err, creationflags=NO_WINDOW)
        log(f"Frag Factory lancé (pid {proc.pid})")
        while proc.poll() is None:
            if STOP.exists():
                stop_tree(proc)
                log("arrêt demandé : Frag Factory arrêté")
                return
            time.sleep(2)
        ran = time.monotonic() - started
        delay = 5.0 if ran > 120 else min(delay * 2, 300.0)
        log(f"Frag Factory s'est arrêté (code {proc.returncode}, après {ran:.0f} s) : relance dans {delay:.0f} s")
        for _ in range(int(delay)):
            if STOP.exists():
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
