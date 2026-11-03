"""Superviseur de l'agent Windows V3 : une seule instance, relance automatique, journal.

    pythonw -m orion.winagent.supervise --config deploy\\v3_agent.toml --env <...>\\.env --log-file <...>\\agent.log

- **Une seule instance** : verrou exclusif sur ``supervise.lock`` (dossier du journal), tenu tant que le superviseur
  vit ; un second lancement (raccourci de démarrage + lancement à la main) s'arrête aussitôt, sans rien toucher.
  Le port de l'agent est lui aussi exclusif (``ExclusiveServer``) : deux agents ne peuvent pas écouter ensemble.
- **Relance** : l'agent (processus enfant, mêmes arguments) qui s'arrête est relancé après 2 s, puis 4, 8… jusqu'à
  60 s ; le délai repart de 2 s après 5 minutes de fonctionnement normal. Un code 2 (configuration invalide) n'est pas
  relancé en boucle serrée : délai maximal d'emblée, et message clair au journal.
- **Arrêt** : fichier ``supervise.stop`` dans le même dossier (``deploy\\v3_agent.ps1 -Stop`` le crée), ou fin du
  processus. L'agent enfant est alors arrêté proprement.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("orion.winagent.supervise")

MIN_DELAY, MAX_DELAY, STABLE_AFTER = 2.0, 60.0, 300.0
CONFIG_ERROR = 2


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """Verrou exclusif d'un fichier, libéré par le système si le processus meurt (aucun verrou périmé)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - gardé ouvert pour tenir le verrou
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AlreadyRunning(str(self.path)) from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class Supervisor:
    def __init__(self, command: list[str], folder: Path, *, spawn: Callable[[list[str]], subprocess.Popen] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.command, self.folder = command, Path(folder)
        self.stop_file = self.folder / "supervise.stop"
        self._spawn = spawn or (lambda cmd: subprocess.Popen(cmd, cwd=os.getcwd()))
        self._clock, self._sleep = clock, sleep
        self.starts = 0

    def stopping(self) -> bool:
        return self.stop_file.exists()

    def run(self, max_starts: int | None = None) -> int:
        self.stop_file.unlink(missing_ok=True)
        delay = MIN_DELAY
        while not self.stopping() and (max_starts is None or self.starts < max_starts):
            started = self._clock()
            self.starts += 1
            child = self._spawn(self.command)
            log.info("Agent V3 lancé (processus %s, lancement n° %d)", child.pid, self.starts)
            code = self._wait(child)
            if self.stopping():
                break
            lived = self._clock() - started
            if lived >= STABLE_AFTER:
                delay = MIN_DELAY
            if code == CONFIG_ERROR:
                delay = MAX_DELAY
                log.error("Agent V3 : configuration invalide (code 2) ; nouvel essai dans %.0f s", delay)
            else:
                log.warning("Agent V3 arrêté (code %s après %.0f s) ; relance dans %.0f s", code, lived, delay)
            self._pause(delay)
            delay = min(MAX_DELAY, delay * 2)
        log.info("Superviseur arrêté")
        return 0

    def _wait(self, child) -> int | None:
        while True:
            code = child.poll()
            if code is not None:
                return code
            if self.stopping():
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                return child.poll()
            self._sleep(1.0)

    def _pause(self, seconds: float) -> None:
        end = self._clock() + seconds
        while self._clock() < end and not self.stopping():
            self._sleep(min(1.0, max(0.0, end - self._clock())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orion.winagent.supervise", description="Superviseur de l'agent V3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args(argv)
    log_file = Path(args.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=str(log_file.with_name("supervise.log")), encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = InstanceLock(log_file.parent / "supervise.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        log.info("Superviseur déjà lancé : rien à faire")
        return 0
    try:
        command = [sys.executable, "-m", "orion.winagent", "--config", args.config, "--env", args.env,
                   "--log-file", args.log_file]
        return Supervisor(command, log_file.parent).run()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
