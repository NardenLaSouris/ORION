"""Tâches périodiques des modules (vérifier une surveillance, collecter les actualités, analyser les journaux…).

- Une tâche = un identifiant, un intervalle, une action. ``tick(now)`` exécute celles qui sont dues, l'une
  après l'autre ; un fil de fond appelle ``tick`` chaque seconde (``start``), les tests l'appellent directement.
- Une action qui échoue est journalisée ; la tâche est reprogrammée plus tard (attente doublée à chaque échec,
  plafonnée) et retrouve son rythme au premier succès. Une tâche ne s'exécute jamais deux fois en même temps.
- Arrêt d'urgence (``halted``, CORE FOUNDATION F1) : vérifié avant chaque tâche, pas seulement au début du tour ;
  tant qu'il dure, plus rien ne part, et une tâche due attend la reprise. Un état illisible vaut arrêt.
- Rien n'est persisté ici : chaque module recrée ses tâches au démarrage à partir de sa base (et de la date de sa
  dernière exécution), ce qui suffit à survivre aux redémarrages.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MAX_BACKOFF = 6 * 3600.0


@dataclass
class Job:
    id: str
    interval: float
    action: Callable[[], None]
    next_at: float
    failures: int = 0
    last_run: float | None = None
    last_error: str = ""
    running: bool = False


class Scheduler:
    def __init__(self, clock: Callable[[], float] = time.time, max_backoff: float = MAX_BACKOFF,
                 halted: Callable[[], bool] | None = None):
        self._clock = clock
        self.halted = halted
        self._max_backoff = max_backoff
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, job_id: str, interval: float, action: Callable[[], None], first_at: float | None = None) -> Job:
        """Ajoute ou remplace la tâche ``job_id`` ; première exécution à ``first_at`` (maintenant par défaut)."""
        if interval <= 0:
            raise ValueError("intervalle strictement positif attendu")
        job = Job(job_id, float(interval), action, self._clock() if first_at is None else float(first_at))
        with self._lock:
            self._jobs[job_id] = job
        return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    def has(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def run_soon(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.next_at = self._clock()
            return True

    def tick(self, now: float | None = None) -> list[str]:
        """Exécute les tâches dues ; rend leurs identifiants (dans l'ordre d'échéance)."""
        now = self._clock() if now is None else now
        with self._lock:
            due = sorted((j for j in self._jobs.values() if j.next_at <= now and not j.running),
                         key=lambda j: j.next_at)
            for job in due:
                job.running = True
        ran = []
        for index, job in enumerate(due):
            if self._halted():
                for waiting in due[index:]:
                    waiting.running = False  # reste due : partira à la reprise
                log.warning("Planificateur : arrêt d'urgence, %d tâche(s) retenue(s)", len(due) - index)
                break
            try:
                job.action()
                job.failures, job.last_error = 0, ""
                job.next_at = now + job.interval
            except Exception as exc:  # noqa: BLE001 - une tâche en échec ne bloque jamais les autres
                job.failures += 1
                job.last_error = f"{type(exc).__name__}: {exc}"[:300]
                job.next_at = now + min(job.interval * 2 ** job.failures, max(self._max_backoff, job.interval))
                log.exception("Tâche %s en échec (%d de suite)", job.id, job.failures)
            finally:
                job.last_run = now
                job.running = False
                ran.append(job.id)
        return ran

    def _halted(self) -> bool:
        if self.halted is None:
            return False
        try:
            return bool(self.halted())
        except Exception:  # noqa: BLE001 - état illisible : on s'arrête
            log.exception("Planificateur : état d'arrêt illisible")
            return True

    def status(self) -> list[dict]:
        with self._lock:
            return [{"id": j.id, "interval_s": j.interval, "next_at": j.next_at, "last_run": j.last_run,
                     "failures": j.failures, "last_error": j.last_error} for j in self._jobs.values()]

    def start(self, poll: float = 1.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(poll):
                self.tick()

        self._thread = threading.Thread(target=loop, name="modules-v3", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)
