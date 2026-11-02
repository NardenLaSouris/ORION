"""Limites de fréquence : requêtes vers une source, appels téléphoniques spontanés, notifications répétées.

``RateLimiter`` compte les usages sur une fenêtre glissante (au plus ``limit`` par ``period`` secondes) et impose un
écart minimal entre deux usages. Il ne consomme rien quand il refuse ; ``use`` n'est appelé qu'après un usage réel.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable


class RateLimiter:
    def __init__(self, limit: int, period: float, min_interval: float = 0.0,
                 clock: Callable[[], float] = time.time):
        if limit < 1 or period <= 0 or min_interval < 0:
            raise ValueError("limite, période et intervalle invalides")
        self.limit, self.period, self.min_interval = int(limit), float(period), float(min_interval)
        self._clock = clock
        self._uses: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _purge(self, key: str, now: float) -> deque[float]:
        uses = self._uses[key]
        while uses and now - uses[0] >= self.period:
            uses.popleft()
        return uses

    def refusal(self, key: str = "", now: float | None = None) -> str:
        """Raison du refus, ou "" si un usage est permis maintenant."""
        now = self._clock() if now is None else now
        with self._lock:
            uses = self._purge(key, now)
            if len(uses) >= self.limit:
                return f"limite atteinte ({self.limit} par {_duration(self.period)})"
            if uses and now - uses[-1] < self.min_interval:
                return f"moins de {_duration(self.min_interval)} depuis le précédent"
            return ""

    def allowed(self, key: str = "", now: float | None = None) -> bool:
        return not self.refusal(key, now)

    def use(self, key: str = "", now: float | None = None) -> None:
        now = self._clock() if now is None else now
        with self._lock:
            self._purge(key, now).append(now)

    def take(self, key: str = "", now: float | None = None) -> bool:
        """Permis ? Si oui, compte l'usage tout de suite (vérification et comptage d'un bloc)."""
        now = self._clock() if now is None else now
        with self._lock:
            uses = self._purge(key, now)
            if len(uses) >= self.limit or (uses and now - uses[-1] < self.min_interval):
                return False
            uses.append(now)
            return True

    def count(self, key: str = "", now: float | None = None) -> int:
        now = self._clock() if now is None else now
        with self._lock:
            return len(self._purge(key, now))


def _duration(seconds: float) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        days = int(seconds // 86400)
        return "jour" if days == 1 else f"{days} jours"
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = int(seconds // 3600)
        return "heure" if hours == 1 else f"{hours} heures"
    if seconds >= 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds:g} s"
