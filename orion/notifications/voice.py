"""Canal vocal : les notifications attendent dans une file (FIFO) et sont prononcées une à une par le TTS
existant d'ORION, au moment où l'agent est libre (jamais par-dessus une réponse ou une autre notification).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable

from orion.events import EventBus
from orion.notifications.manager import NotificationChannel
from orion.notifications.models import (
    NOTIFICATION_FAILED, NOTIFICATION_FINISHED, NOTIFICATION_SENT, NOTIFICATION_STARTED, Notification, Priority, publish,
)

log = logging.getLogger(__name__)


class VoiceNotificationChannel(NotificationChannel):
    name = "voice"

    def __init__(self, events: EventBus | None = None, max_pending: int = 20, min_priority: Priority = Priority.LOW):
        super().__init__(min_priority)
        self._events = events
        self._max_pending = max_pending
        self._queue: deque[Notification] = deque()
        self._lock = threading.Lock()
        self._open = True
        self._follow_up: dict | None = None  # suite proposée par la dernière annonce dite (« lis-le »)
        # CORE FOUNDATION : raison de ne plus dire une annonce en file (arrêt d'urgence survenu entre-temps), ou "".
        self.still_allowed: Callable[[Notification], str] | None = None

    def send(self, notification: Notification) -> None:
        with self._lock:
            if not self._open:
                raise RuntimeError("canal vocal arrêté")
            if len(self._queue) >= self._max_pending:
                raise RuntimeError("file vocale pleine")
            self._queue.append(notification)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def deliver(self, speak: Callable[[str], None]) -> int:
        """Prononce les notifications en attente, dans l'ordre, avec ``speak`` (le TTS de l'agent)."""
        delivered = 0
        while (notification := self._next()) is not None:
            if why := self._withdrawn(notification):
                log.info("Notification vocale « %s » retirée : %s", notification.title, why)
                publish(self._events, NOTIFICATION_FAILED, notification, self.name, why)
                continue
            publish(self._events, NOTIFICATION_STARTED, notification, self.name)
            try:
                speak(notification.message)
            except Exception as exc:
                log.exception("Notification vocale « %s » non prononcée", notification.title)
                publish(self._events, NOTIFICATION_FAILED, notification, self.name, str(exc)[:200] or type(exc).__name__)
            else:
                publish(self._events, NOTIFICATION_SENT, notification, self.name)
                offer = notification.metadata.get("follow_up")
                self._follow_up = dict(offer) if isinstance(offer, dict) else None
            finally:
                publish(self._events, NOTIFICATION_FINISHED, notification, self.name)
            delivered += 1
        return delivered

    def _withdrawn(self, notification: Notification) -> str:
        """L'arrêt d'urgence passe avant la file : une annonce autonome en attente est revérifiée avant d'être dite."""
        if self.still_allowed is None or not notification.metadata.get("principal"):
            return ""
        try:
            why = self.still_allowed(notification)
        except Exception:  # noqa: BLE001 - état illisible : on se tait
            log.exception("Notification vocale : vérification impossible")
            return "état de l'autonomie illisible"
        return f"permission : {why}" if why else ""

    def take_follow_up(self) -> dict | None:
        """Suite proposée par la dernière annonce (outil à lancer si l'utilisateur acquiesce), une seule fois."""
        offer, self._follow_up = self._follow_up, None
        return offer

    def start(self) -> None:
        with self._lock:
            self._open = True

    def stop(self) -> None:
        with self._lock:
            self._open = False
            dropped = list(self._queue)
            self._queue.clear()
        for notification in dropped:
            publish(self._events, NOTIFICATION_FAILED, notification, self.name, "arrêt d'ORION")
        if dropped:
            log.info("%d notification(s) vocale(s) abandonnée(s) à l'arrêt", len(dropped))

    def _next(self) -> Notification | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None
