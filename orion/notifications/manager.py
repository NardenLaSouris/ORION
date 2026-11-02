"""Gestionnaire de notifications : remet chaque notification aux canaux qui l'acceptent.

Il ignore d'où viennent les notifications (minuteur, rappel...) ; un canal en erreur est journalisé
sans empêcher les autres canaux ni ORION de continuer.

Moteur central (CORE FOUNDATION F2, ``permit``, posé seulement si la plateforme est activée) : chaque notification
est d'abord soumise (``NOTIFY``) au nom de son origine. Ce que vous avez programmé ou réglé (rappel, minuteur,
agenda, routine, annonce des mails) part sous ``delegated:<source>`` et continue pendant la suspension ; une
initiative (échéance trouvée dans un mail) sous ``module:<source>`` ; tout s'arrête sur arrêt d'urgence. Les alertes
des modules V3 (source ``alerts``) ont déjà été décidées par le routeur d'alertes. Le principal retenu est ajouté aux
métadonnées (``principal``) : le canal vocal le revérifie juste avant de parler.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Callable

from orion.events import EventBus
from orion.notifications.models import NOTIFICATION_CREATED, NOTIFICATION_FAILED, Notification, Priority, publish

log = logging.getLogger(__name__)

# Source -> principal ; "" : déjà décidé ailleurs. Source absente : initiative du module « module:<source> ».
SOURCE_PRINCIPALS = {"timer": "delegated:timer", "reminder": "delegated:reminder", "calendar": "delegated:calendar",
                     "routines": "delegated:routine", "flex": "delegated:flex", "mail": "delegated:mail",
                     "alerts": ""}
AUTONOMOUS = re.compile(r"^(module|delegated):[a-z][a-z0-9_]{0,30}(:[\w.@\-]{1,120})?$")


class NotificationChannel:
    """Destination d'une notification (voix, bureau, téléphone...). ``send`` ne doit pas bloquer longtemps."""

    name = "channel"

    def __init__(self, min_priority: Priority = Priority.LOW):
        self.min_priority = min_priority

    def accepts(self, notification: Notification) -> bool:
        return notification.priority >= self.min_priority

    def send(self, notification: Notification) -> None:
        raise NotImplementedError

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class NotificationManager:
    def __init__(self, events: EventBus | None = None):
        self._events = events
        self._channels: dict[str, NotificationChannel] = {}
        self._disabled: set[str] = set()
        self.permit: Callable[..., Any] | None = None  # PermissionEngine.decide (CORE FOUNDATION), s'il existe

    @staticmethod
    def principal_of(notification: Notification) -> str:
        """Origine pour le moteur central : celle posée par le code d'ORION qui crée la notification (jamais
        « user » ni « admin »), sinon d'après la source."""
        declared = notification.metadata.get("principal")
        if isinstance(declared, str) and AUTONOMOUS.match(declared):
            return declared
        return SOURCE_PRINCIPALS.get(notification.source, f"module:{notification.source}")

    def _refusal(self, notification: Notification) -> tuple[str, Notification]:
        if self.permit is None:
            return "", notification
        principal = self.principal_of(notification)
        if not principal:
            return "", notification
        try:
            verdict = self.permit(principal, "NOTIFY", {"priority": notification.priority.label},
                                  action=f"notify:{notification.source}")
            allowed, reason = verdict.allowed, verdict.reason
        except Exception:  # noqa: BLE001 - moteur en panne : échec sûr
            log.exception("Notification « %s » : moteur de permissions en erreur", notification.title)
            allowed, reason = False, "moteur de permissions en erreur"
        if not allowed:
            return f"permission : {reason}", notification
        return "", replace(notification, metadata={**notification.metadata, "principal": principal})

    def register(self, channel: NotificationChannel) -> None:
        if channel.name in self._channels:
            raise ValueError(f"Canal de notification déjà enregistré : {channel.name}")
        self._channels[channel.name] = channel

    def unregister(self, name: str) -> bool:
        channel = self._channels.pop(name, None)
        self._disabled.discard(name)
        if channel is not None:
            self._stop(channel)
        return channel is not None

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        if name in self._channels:
            self._disabled.add(name)

    def channels(self) -> dict[str, bool]:
        """Canaux enregistrés -> actif ou non."""
        return {name: name not in self._disabled for name in self._channels}

    def notify(self, notification: Notification) -> list[str]:
        """Remet la notification aux canaux actifs qui l'acceptent ; rend leurs noms (aucun si le moteur central la
        refuse)."""
        refusal, notification = self._refusal(notification)
        if refusal:
            log.info("Notification « %s » (%s) retenue : %s", notification.title, notification.source, refusal)
            publish(self._events, NOTIFICATION_FAILED, notification, None, refusal)
            return []
        publish(self._events, NOTIFICATION_CREATED, notification)
        used = []
        for name, channel in list(self._channels.items()):
            if name in self._disabled or not channel.accepts(notification):
                continue
            try:
                channel.send(notification)
                used.append(name)
            except Exception as exc:
                log.exception("Canal de notification %s en échec", name)
                publish(self._events, NOTIFICATION_FAILED, notification, name, str(exc)[:200] or type(exc).__name__)
        if not used:
            log.info("Notification « %s » : aucun canal actif", notification.title)
        return used

    def start(self) -> None:
        for channel in self._channels.values():
            channel.start()

    def stop(self) -> None:
        for channel in self._channels.values():
            self._stop(channel)

    @staticmethod
    def _stop(channel: NotificationChannel) -> None:
        try:
            channel.stop()
        except Exception:
            log.exception("Arrêt du canal de notification %s impossible", channel.name)
