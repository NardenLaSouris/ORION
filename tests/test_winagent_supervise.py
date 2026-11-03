"""Superviseur de l'agent Windows V3 : une seule instance, relance avec délai croissant, délai remis à zéro après un
fonctionnement stable, configuration invalide sans boucle serrée, arrêt propre de l'agent sur demande."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.winagent.supervise import AlreadyRunning, InstanceLock, Supervisor  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Child:
    def __init__(self, clock, lives, code):
        self.clock, self.until, self.code, self.pid = clock, clock.t + lives, code, 4242
        self.terminated = False

    def poll(self):
        return self.code if self.terminated or self.clock.t >= self.until else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.terminated = True


def test_a_second_instance_is_refused_and_the_lock_frees_on_release(tmp_path):
    first = InstanceLock(tmp_path / "supervise.lock")
    first.acquire()
    with pytest.raises(AlreadyRunning):
        InstanceLock(tmp_path / "supervise.lock").acquire()
    first.release()
    again = InstanceLock(tmp_path / "supervise.lock")
    again.acquire()
    again.release()


def test_crashes_are_restarted_with_growing_delays_reset_after_stable_running(tmp_path):
    clock = Clock()
    lives = iter([1, 1, 1, 400, 1])
    starts = []

    def spawn(cmd):
        starts.append(clock.t)
        return Child(clock, next(lives), 1)

    Supervisor(["agent"], tmp_path, spawn=spawn, clock=clock, sleep=clock.sleep).run(max_starts=5)
    gaps = [round(b - a) for a, b in zip(starts, starts[1:])]
    # vie de l'agent + délai : 2, 4, 8 s ; après 400 s de fonctionnement, le délai repart de 2 s
    assert gaps == [1 + 2, 1 + 4, 1 + 8, 400 + 2]


def test_invalid_configuration_waits_the_longest_delay(tmp_path):
    clock = Clock()
    starts = []

    def spawn(cmd):
        starts.append(clock.t)
        return Child(clock, 0, 2)

    Supervisor(["agent"], tmp_path, spawn=spawn, clock=clock, sleep=clock.sleep).run(max_starts=2)
    assert starts[1] - starts[0] >= 60


def test_stop_file_stops_the_agent_cleanly(tmp_path):
    clock = Clock()
    children = []

    def spawn(cmd):
        child = Child(clock, 10_000, 0)
        children.append(child)
        return child

    supervisor = Supervisor(["agent"], tmp_path, spawn=spawn, clock=clock, sleep=clock.sleep)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t > 30:
            supervisor.stop_file.write_text("stop")

    supervisor._sleep = sleep  # noqa: SLF001
    assert supervisor.run() == 0
    assert len(children) == 1 and children[0].terminated


def test_real_child_process_is_restarted(tmp_path):
    """Vrai processus enfant (Python) qui s'arrête tout de suite : relancé par le superviseur."""
    marker = tmp_path / "runs.txt"
    code = f"open(r'{marker}', 'a').write('x')"
    clock = Clock()
    supervisor = Supervisor([sys.executable, "-c", code], tmp_path,
                            spawn=lambda cmd: subprocess.Popen(cmd), sleep=lambda s: __import__("time").sleep(0.05),
                            clock=clock)
    supervisor._pause = lambda seconds: None  # noqa: SLF001 - pas d'attente réelle entre deux lancements
    supervisor.run(max_starts=3)
    assert marker.read_text() == "xxx"
