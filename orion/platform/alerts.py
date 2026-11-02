"""Alertes des modules V3 vers l'utilisateur, selon sa présence (WATCH, NEWS, PURCHASE).

Règles (déterministes, jamais décidées par le LLM) :
- présence confirmée à la maison : voix sur les enceintes ; pendant les heures calmes, message écrit à la place ;
- absence confirmée : message écrit, et appel téléphonique pour une alerte de niveau HIGH ou plus, si le budget
  d'appels le permet (au plus N par jour, écart minimal) et hors heures calmes ;
- présence inconnue : message écrit seulement (jamais d'appel : on ne suppose pas l'absence) ;
- niveau INFO : rien n'est envoyé, l'alerte est seulement gardée (historique d'ORION Control) ;
- même clé (``key``) déjà envoyée dans la fenêtre de dédoublonnage : gardée comme « dédoublonnée », rien n'est envoyé.

Moteur central des permissions (CORE FOUNDATION F1, ``permit``) : chaque alerte est soumise (NOTIFY) au nom de son
origine (``origin`` : ``delegated:…`` pour ce que vous avez programmé, sinon ``module:<source>``) ; refusée, elle est
seulement gardée. Un appel demande en plus la capacité CALL.

Chaque alerte est enregistrée avec ses canaux, ce qui a été refusé et pourquoi : ORION Control en montre l'historique.
Les heures calmes ne sont jamais contournées, sauf réglage explicite ``critical_ignores_quiet`` (niveau CRITICAL).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable

from orion.platform.limits import RateLimiter
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE alerts (
            id TEXT PRIMARY KEY,
            at REAL NOT NULL,
            source TEXT NOT NULL,
            key TEXT NOT NULL,
            level INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            presence TEXT NOT NULL,
            channels TEXT NOT NULL,
            skipped TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE INDEX alerts_key ON alerts (source, key, at)
    """),
)

HOME, AWAY, UNKNOWN = "home", "away", "unknown"
VOICE, CALL, WRITTEN = "voix", "appel", "écrit"
MAX_TITLE, MAX_MESSAGE = 120, 900


class Level(IntEnum):
    INFO = 0  # gardée seulement
    NORMAL = 1  # voix à la maison, sinon écrit
    HIGH = 2  # + appel si absent
    CRITICAL = 3  # + appel si absent ; heures calmes contournées seulement si réglé ainsi

    @property
    def label(self) -> str:
        return {0: "info", 1: "normale", 2: "importante", 3: "critique"}[int(self)]


@dataclass(frozen=True)
class Alert:
    source: str
    title: str
    message: str
    level: Level = Level.NORMAL
    key: str = ""
    call_allowed: bool = True  # le module peut l'interdire (bulletin, budget propre au module épuisé)
    private: bool = False  # texte privé : seul le titre est dit ou écrit
    written_only: bool = False  # choix de l'utilisateur pour cette source : jamais de voix ni d'appel
    details: dict = field(default_factory=dict)
    origin: str = ""  # principal (CORE FOUNDATION) ; vide : initiative du module « module:<source> »

    @property
    def principal(self) -> str:
        return self.origin or f"module:{self.source}"

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.message.strip():
            raise ValueError("Alerte : titre et message obligatoires")
        object.__setattr__(self, "title", self.title.strip()[:MAX_TITLE])
        object.__setattr__(self, "message", self.message.strip()[:MAX_MESSAGE])

    @property
    def spoken(self) -> str:
        return self.title if self.private else self.message


@dataclass(frozen=True)
class AlertSettings:
    quiet_start: str = "22:30"
    quiet_end: str = "08:00"
    max_calls_per_day: int = 3
    min_call_interval_minutes: float = 15.0
    dedup_hours: float = 12.0
    critical_ignores_quiet: bool = False


@dataclass(frozen=True)
class Delivery:
    id: str
    channels: tuple[str, ...]
    skipped: dict[str, str]
    presence: str
    deduplicated: bool = False

    @property
    def delivered(self) -> bool:
        return bool(self.channels)


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """« 22:30 » à « 08:00 » traverse minuit ; début == fin : jamais calme."""
    def minutes(text: str) -> int:
        hours, _, mins = text.partition(":")
        return int(hours) * 60 + int(mins or 0)

    begin, finish, current = minutes(start), minutes(end), now.hour * 60 + now.minute
    if begin == finish:
        return False
    return begin <= current < finish if begin < finish else current >= begin or current < finish


class AlertRouter:
    def __init__(self, store: Store, settings: AlertSettings | None = None, *,
                 presence: Callable[[], bool | None] = lambda: None,
                 speak: Callable[[str], bool] | None = None,
                 call: Callable[[str], bool] | None = None,
                 write: Callable[[str], bool] | None = None,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] = datetime.now,
                 permit: Callable[..., Any] | None = None):
        self._store = store
        self._permit = permit  # PermissionEngine.decide, s'il existe
        self.settings = settings = settings or AlertSettings()
        self._presence, self._speak, self._call, self._write = presence, speak, call, write
        self._clock, self._now = clock, now
        self._calls = RateLimiter(max(1, settings.max_calls_per_day), 86400.0, settings.min_call_interval_minutes * 60,
                                  clock=clock)
        self._lock = threading.Lock()
        store.migrate("alerts", MIGRATIONS)
        self._restore_call_budget()

    def _restore_call_budget(self) -> None:
        """Appels des dernières 24 h, relus de la base : un redémarrage ne remet jamais le budget à zéro."""
        since = self._clock() - 86400
        for row in self._store.query("SELECT at, channels FROM alerts WHERE at >= ? ORDER BY at", (since,)):
            if CALL in loads(row["channels"], []):
                self._calls.use("", row["at"])

    def presence(self) -> str:
        try:
            home = self._presence()
        except Exception:  # noqa: BLE001 - présence illisible : inconnue
            log.exception("Alertes : présence illisible")
            home = None
        return UNKNOWN if home is None else HOME if home else AWAY

    def deliver(self, alert: Alert) -> Delivery:
        with self._lock:
            return self._deliver(alert)

    def _deliver(self, alert: Alert) -> Delivery:
        now = self._clock()
        presence = self.presence()
        delivery_id = uuid.uuid4().hex[:12]
        if alert.key and self._recent(alert, now):
            return self._save(delivery_id, alert, presence, (), {"tout": "déjà envoyée récemment"}, "dédoublonnée",
                              deduplicated=True)
        if alert.level == Level.INFO:
            return self._save(delivery_id, alert, presence, (), {"tout": "niveau info : gardée seulement"}, "gardée")
        if refusal := self._refused(alert, "NOTIFY"):
            return self._save(delivery_id, alert, presence, (), {"tout": refusal}, "bloquée")
        quiet = in_quiet_hours(self._now(), self.settings.quiet_start, self.settings.quiet_end)
        bypass = quiet and alert.level == Level.CRITICAL and self.settings.critical_ignores_quiet
        channels: list[str] = []
        skipped: dict[str, str] = {}
        if alert.written_only:
            skipped[VOICE] = skipped[CALL] = "message écrit seulement (choix de l'utilisateur)"
            self._send(WRITTEN, self._write, alert, channels, skipped)
        elif presence == HOME:
            if quiet and not bypass:
                skipped[VOICE] = "heures calmes : message écrit à la place"
                self._send(WRITTEN, self._write, alert, channels, skipped)
            else:
                self._send(VOICE, self._speak, alert, channels, skipped)
                if VOICE not in channels:  # enceintes indisponibles : l'écrit prend le relais
                    self._send(WRITTEN, self._write, alert, channels, skipped)
        else:
            self._send(WRITTEN, self._write, alert, channels, skipped)
            if presence == UNKNOWN:
                skipped[CALL] = "présence inconnue : jamais d'appel"
            elif alert.level < Level.HIGH:
                skipped[CALL] = "niveau insuffisant pour un appel"
            elif not alert.call_allowed:
                skipped[CALL] = "appel non autorisé par le module"
            elif quiet and not bypass:
                skipped[CALL] = "heures calmes"
            elif refusal := self._calls.refusal("", now):
                skipped[CALL] = refusal
            elif refusal := self._refused(alert, "CALL"):
                skipped[CALL] = refusal
            else:
                self._send(CALL, self._call, alert, channels, skipped)
                if CALL in channels:
                    self._calls.use("", now)
        status = "envoyée" if channels else "non envoyée"
        if not channels:
            log.warning("Alerte « %s » (%s) : aucun canal disponible (%s)", alert.title, alert.source, skipped)
        return self._save(delivery_id, alert, presence, tuple(channels), skipped, status)

    def _refused(self, alert: Alert, capability: str) -> str:
        """Raison du refus du moteur central, ou "" (permis, ou pas de moteur)."""
        if self._permit is None:
            return ""
        verdict = self._permit(alert.principal, capability, {"level": int(alert.level)},
                               action=f"alert:{alert.source}")
        return "" if verdict.allowed else f"permission : {verdict.reason}"

    @staticmethod
    def _send(name: str, send: Callable[[str], bool] | None, alert: Alert, channels: list[str],
              skipped: dict[str, str]) -> None:
        if send is None:
            skipped[name] = "canal non configuré"
            return
        try:
            ok = send(alert.spoken if name != CALL else f"{alert.title}. {alert.spoken}" if not alert.private
                      else alert.title)
        except Exception as exc:  # noqa: BLE001 - un canal en panne n'empêche jamais les autres
            log.exception("Alerte : canal %s en échec", name)
            skipped[name] = f"échec ({type(exc).__name__})"
            return
        if ok is False:
            skipped[name] = "refusé par le canal"
        else:
            channels.append(name)

    def _recent(self, alert: Alert, now: float) -> bool:
        since = now - self.settings.dedup_hours * 3600
        row = self._store.one("SELECT id FROM alerts WHERE source = ? AND key = ? AND at >= ? "
                              "AND status IN ('envoyée', 'gardée') LIMIT 1", (alert.source, alert.key, since))
        return row is not None

    def _save(self, delivery_id: str, alert: Alert, presence: str, channels: tuple[str, ...], skipped: dict,
              status: str, deduplicated: bool = False) -> Delivery:
        self._store.write("INSERT INTO alerts (id, at, source, key, level, title, message, presence, channels, "
                            "skipped, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (delivery_id, self._clock(), alert.source, alert.key, int(alert.level), alert.title,
                             alert.title if alert.private else alert.message, presence, dumps(list(channels)),
                             dumps(skipped), status))
        return Delivery(delivery_id, channels, skipped, presence, deduplicated)

    def history(self, limit: int = 100, source: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM alerts", []
        if source:
            sql, params = sql + " WHERE source = ?", [source]
        rows = self._store.query(sql + " ORDER BY at DESC, rowid DESC LIMIT ?", (*params, max(1, min(int(limit), 500))))
        return [{**row, "level": Level(row["level"]).label, "channels": loads(row["channels"], []),
                 "skipped": loads(row["skipped"], {})} for row in rows]

    def status(self) -> dict:
        return {"presence": self.presence(), "calls_last_24h": self._calls.count(),
                "max_calls_per_day": self.settings.max_calls_per_day,
                "quiet_hours": f"{self.settings.quiet_start}–{self.settings.quiet_end}"}
