"""Module GOOGLE CALENDAR (V3, docs/v3/google_calendar.md) : le calendrier Google « ORION », synchronisé en lecture
seule par son adresse secrète iCal.

- **Secret** : l'adresse iCal donne accès au calendrier ; elle vient du ``.env`` de la V3 (``ORION_CALENDAR_ICS``,
  saisie sur le Core par ``deploy/v3_calendar_setup.sh``), n'est jamais journalisée, affichée, enregistrée ailleurs
  ni donnée au LLM. Seule une adresse https de calendar.google.com au format iCal privé est acceptée.
- **Données** : les événements sont personnels. Copie de travail en mémoire ; copie de reprise **scellée** (lot F4)
  dans la base opérationnelle. Base personnelle verrouillée (clé absente) : le module ne synchronise pas (« verrouillé »)
  et ne contourne jamais le verrouillage.
- **Synchronisation** (``google_calendar:sync``, au démarrage puis toutes les 5 minutes) : une seule à la fois ;
  téléchargement borné (délai, taille, contenu iCalendar vérifié) ; occurrences de la fenêtre (30 jours passés,
  180 à venir) développées par ``orion.agenda.expand`` (répétitions, exceptions, occurrences modifiées ou annulées,
  fuseaux) ; empreinte par occurrence : nouvelles, modifiées et supprimées détectées, sans doublon. **Un échec ne
  supprime jamais rien** : la dernière copie reste, signalée comme possiblement périmée.
- **Annonces F3** (texte privé, une fois par changement) : nouvel événement, modifié, annulé ; échec prolongé ;
  reprise. Un nouvel événement est accompagné d'une **proposition** de rappel, jamais d'un rappel créé : ORION ne crée
  aucune tâche (F2) ni aucun rappel sans votre demande.
- **Lecture seule** : rien n'est jamais écrit dans Google Agenda.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from orion.agenda import CalendarEvent, expand, parse_ics
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

SYNC_JOB = "google_calendar:sync"
URL = re.compile(r"^https://calendar\.google\.com/calendar/ical/[A-Za-z0-9%._@-]{3,200}/private-[0-9a-f]{16,64}/"
                 r"basic\.ics$")
MIGRATIONS = [Migration(1, """
CREATE TABLE google_calendar (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
""")]
ANNOUNCE_AT_ONCE = 5  # au-delà, un seul résumé (« 12 changements dans votre agenda »)


class IcalError(RuntimeError):
    """Échec de téléchargement ou flux invalide ; le message ne contient jamais l'adresse."""


@dataclass(frozen=True)
class GoogleCalendarSettings:
    name: str = "ORION"
    interval_seconds: float = 300.0
    past_days: int = 30
    future_days: int = 180
    timeout: float = 15.0
    max_bytes: int = 5_000_000
    failure_alert_minutes: float = 30.0
    stale_after_seconds: float = 900.0  # au-delà sans synchronisation réussie : réponses avec réserve


def fetch_ical(url: str, timeout: float, max_bytes: int) -> str:
    """Texte iCalendar de l'adresse ; IcalError (sans l'adresse) sur toute erreur."""
    if not URL.match(url or ""):
        raise IcalError("adresse refusée (seule l'adresse secrète iCal de calendar.google.com est acceptée)")
    request = urllib.request.Request(url, headers={"User-Agent": "ORION-Calendar/1.0", "Accept": "text/calendar"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https vérifié ci-dessus
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise IcalError(f"réponse HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IcalError(f"réseau ({type(exc).__name__})") from None
    if len(data) > max_bytes:
        raise IcalError("flux trop volumineux")
    return check_ical(data.decode("utf-8", "replace"))


def check_ical(text: str) -> str:
    """Un calendrier iCalendar complet, sinon IcalError : une page d'erreur ou un flux tronqué n'est jamais lu comme
    un agenda vide (ce qui supprimerait tous les événements)."""
    if not isinstance(text, str) or "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise IcalError("réponse qui n'est pas un calendrier iCalendar complet")
    return text


def _fingerprint(event: CalendarEvent) -> str:
    raw = dumps([event.title, event.start.isoformat(), event.end.isoformat(), event.all_day, event.location])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _occurrence(event: CalendarEvent) -> dict:
    return {"id": event.id, "title": event.title, "start": event.start.isoformat(), "end": event.end.isoformat(),
            "all_day": event.all_day, "location": event.location, "fp": _fingerprint(event)}


def _event(item: dict) -> CalendarEvent:
    return CalendarEvent(item["id"], item["title"], datetime.fromisoformat(item["start"]),
                         datetime.fromisoformat(item["end"]), bool(item["all_day"]), item.get("location", ""), "google")


class GoogleCalendarModule(BaseModule):
    name = "google_calendar"
    title = "Agenda Google"

    def __init__(self, store: Store, sealer, secret: Callable[[], str], *, events=None, scheduler=None,
                 settings: GoogleCalendarSettings | None = None, fetch: Callable[[str], str] | None = None,
                 clock: Callable[[], datetime] = datetime.now, monotonic: Callable[[], float] = time.time):
        self._store, self._sealer, self._secret = store, sealer, secret
        self._scheduler = scheduler
        self.settings = settings or GoogleCalendarSettings()
        self._fetch = fetch or (lambda url: fetch_ical(url, self.settings.timeout, self.settings.max_bytes))
        self._clock, self._time = clock, monotonic
        self._gate = events.gate("google_calendar", "delegated:google_calendar:sync") if events is not None else None
        self._sync_lock = threading.Lock()
        self._items: dict[str, dict] | None = None  # copie de travail (None : jamais chargée ni synchronisée)
        store.migrate("google_calendar", MIGRATIONS)
        self._load()

    # --- État ------------------------------------------------------------------------------------------------

    def _get(self, key: str, default: Any = None) -> Any:
        row = self._store.one("SELECT v FROM google_calendar WHERE k = ?", (key,))
        return loads(row["v"], default) if row else default

    def _set(self, **values: Any) -> None:
        with self._store.transaction():
            for key, value in values.items():
                self._store.write("INSERT INTO google_calendar (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET "
                                  "v = excluded.v", (key, dumps(value)))

    @property
    def locked(self) -> bool:
        from orion.platform.vault import WITHHELD

        return self._sealer.mode == WITHHELD

    @property
    def enabled(self) -> bool:
        return bool(self._get("enabled", True))

    def _load(self) -> None:
        """Copie de reprise scellée -> copie de travail (au démarrage) ; illisible ou verrouillée : rien."""
        sealed = self._get("snapshot")
        if not sealed or self.locked:
            return
        text = self._sealer.open(sealed, "google_calendar:snapshot")
        if text is not None:
            self._items = loads(text, None)

    def configured(self) -> bool:
        try:
            return bool(URL.match(self._secret() or ""))
        except Exception:  # noqa: BLE001 - secret illisible : non configuré, sans détail
            return False

    def health(self) -> str:
        if not self.enabled:
            return "désactivé"
        if self.locked:
            return "verrouillé"
        if not self.configured():
            return "non configuré"
        if self._get("failures", 0):
            return "en échec"
        return "à jour" if self._get("last_success") else "en attente"

    def status(self) -> dict:
        """Vue d'ORION Control : jamais l'adresse, jamais le contenu des événements."""
        return {"calendar": self.settings.name, "state": self.health(), "enabled": self.enabled,
                "locked": self.locked, "configured": self.configured(),
                "connected": self.health() == "à jour", "last_success": self._get("last_success"),
                "last_attempt": self._get("last_attempt"), "failures": self._get("failures", 0),
                "last_error": self._get("last_error", ""), "count": len(self._items or {}),
                "interval_seconds": self.settings.interval_seconds,
                "window_days": [self.settings.past_days, self.settings.future_days]}

    # --- Cycle de vie -------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._scheduler is not None:
            self._scheduler.add(SYNC_JOB, self.settings.interval_seconds, self.sync, first_at=time.time())

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.remove(SYNC_JOB)

    # --- Synchronisation ----------------------------------------------------------------------------------

    def sync(self) -> dict:
        if not self._sync_lock.acquire(blocking=False):
            return {"state": "en cours"}  # une synchronisation à la fois
        try:
            return self._sync()
        finally:
            self._sync_lock.release()

    def _sync(self) -> dict:
        state = self.health()
        if state in ("désactivé", "verrouillé", "non configuré"):
            return {"state": state}
        now = self._time()
        self._set(last_attempt=now)
        try:
            text = check_ical(self._fetch(self._secret()))  # vérifié ici aussi, quel que soit le moyen de lecture
            today = self._clock()
            window = (today - timedelta(days=self.settings.past_days), today + timedelta(days=self.settings.future_days))
            found = expand(parse_ics(text, "google"), *window)
        except IcalError as exc:
            return self._failed(str(exc), now)
        except Exception as exc:  # noqa: BLE001 - erreur d'analyse : échec, jamais une suppression
            return self._failed(f"flux illisible ({type(exc).__name__})", now)
        items = {e.id: _occurrence(e) for e in found}
        previous = self._items
        changes = self._diff(previous, items, window) if previous is not None else []
        sealed = self._sealer.seal(dumps(items), "google_calendar:snapshot")
        if not sealed:
            return {"state": "verrouillé"}
        recovered = bool(self._get("alerted", False))
        self._items = items
        self._set(snapshot=sealed, last_success=now, failures=0, failing_since=None, alerted=False, last_error="")
        announced = self._announce(changes)
        if recovered:
            self._publish("google_calendar.recovered", "Agenda Google de nouveau à jour",
                          "La synchronisation de votre agenda Google fonctionne de nouveau.", f"reprise:{int(now)}")
        log.info("Agenda Google : %d occurrence(s), %d changement(s)", len(items), len(changes))
        return {"state": "à jour", "count": len(items), "changes": len(changes), "announced": announced}

    def _failed(self, reason: str, now: float) -> dict:
        failures = int(self._get("failures", 0)) + 1
        since = self._get("failing_since") or now
        self._set(failures=failures, failing_since=since, last_error=reason)
        log.warning("Agenda Google : synchronisation impossible (%s), essai n° %d ; dernière copie gardée", reason,
                    failures)
        if not self._get("alerted", False) and now - since >= self.settings.failure_alert_minutes * 60:
            self._set(alerted=True)
            self._publish("google_calendar.failing", "Agenda Google non synchronisé",
                          f"Votre agenda Google n'a pas pu être mis à jour depuis "
                          f"{datetime.fromtimestamp(since):%H:%M} ({reason}). Je garde la dernière version connue.",
                          f"echec:{int(since)}")
        return {"state": "en échec", "error": reason, "failures": failures}

    def _diff(self, before: dict, after: dict, window: tuple[datetime, datetime]) -> list[tuple[str, dict]]:
        now = self._clock()
        changes = []
        for key, item in after.items():
            if key not in before:
                changes.append(("created", item))
            elif before[key]["fp"] != item["fp"]:
                changes.append(("changed", item))
        for key, item in before.items():
            # supprimée : absente d'un flux valide et encore dans la fenêtre (une occurrence sortie de la fenêtre,
            # passée depuis plus de 30 jours, n'est pas « supprimée »)
            if key not in after and datetime.fromisoformat(item["end"]) > window[0]:
                changes.append(("cancelled", item))
        # seuls les changements à venir sont annoncés (le passé ne vous concerne plus)
        return [(kind, item) for kind, item in changes if datetime.fromisoformat(item["end"]) > now]

    def _announce(self, changes: list[tuple[str, dict]]) -> int:
        if not changes:
            return 0
        if len(changes) > ANNOUNCE_AT_ONCE:
            digest = hashlib.sha256(dumps(sorted((k, i["id"], i["fp"]) for k, i in changes)).encode()).hexdigest()[:16]
            self._publish("google_calendar.changed", "Agenda Google mis à jour",
                          f"{len(changes)} changements dans votre agenda Google.", f"lot:{digest}")
            return 1
        today = self._clock().date()
        for kind, item in changes:
            from orion.agenda import _said_event  # phrase du calendrier existant

            said = _said_event(_event(item), today, with_day=True)
            title, body = {
                "created": ("Nouveau rendez-vous", f"Nouveau dans votre agenda : {said}. Voulez-vous un rappel ? "
                                                   f"Dites par exemple « rappelle-moi ce rendez-vous une heure avant »."),
                "changed": ("Rendez-vous modifié", f"Modifié dans votre agenda : {said}."),
                "cancelled": ("Rendez-vous annulé", f"Retiré de votre agenda : {said}."),
            }[kind]
            self._publish(f"google_calendar.{kind}", title, body, f"{kind}:{item['id']}:{item['fp']}")
        return len(changes)

    def _publish(self, kind: str, title: str, body: str, key: str) -> None:
        if self._gate is None:
            return
        from orion.platform.eventhub import VOICE, WRITTEN

        try:
            self._gate.publish(kind, title, body, dedup_key=f"google_calendar:{key}", private=True,
                               channels=(VOICE, WRITTEN))
        except Exception:  # noqa: BLE001 - l'annonce manquée ne bloque jamais la synchronisation
            log.exception("Agenda Google : annonce impossible")

    # --- Lecture (fournisseur du calendrier d'ORION) -------------------------------------------------------

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        items = self._items or {}
        found = [_event(i) for i in items.values()]
        return [e for e in found if e.end > start and e.start < end]

    def caveat(self) -> str:
        """Réserve dite avec une réponse de l'agenda quand la copie peut être périmée ; rien si tout va bien."""
        state = self.health()
        if state in ("désactivé", "non configuré"):
            return ""
        if state == "verrouillé":
            return "Votre agenda Google n'est pas accessible : la base personnelle est verrouillée."
        last = self._get("last_success")
        if not last:
            return "Votre agenda Google n'a pas encore été synchronisé : je ne peux pas en tenir compte."
        if state == "en échec" or self._time() - last > self.settings.stale_after_seconds:
            return (f"Attention : votre agenda Google n'a pas pu être mis à jour depuis "
                    f"{datetime.fromtimestamp(last):%H:%M} ; il peut ne pas être à jour.")
        return ""

    # --- ORION Control -----------------------------------------------------------------------------------

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        if method == "GET" and not parts:
            return 200, self.status()
        if method == "POST" and parts == ["sync"]:
            return 200, self.sync()
        if method == "POST" and parts in (["enable"], ["disable"]):
            self._set(enabled=parts == ["enable"])
            log.info("Agenda Google %s depuis ORION Control", "activé" if parts == ["enable"] else "désactivé")
            return 200, self.status()
        return NOT_FOUND


class GoogleCalendarProvider:
    """Fournisseur ``orion.agenda.Calendar`` (lecture seule) adossé au module : les outils existants (aujourd'hui,
    demain, prochains rendez-vous, créneaux libres, recherche) lisent l'agenda Google sans nouvel outil."""

    name, writable = "google", False

    def __init__(self, module: GoogleCalendarModule):
        self._module = module

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return self._module.events(start, end)

    def caveat(self) -> str:
        return self._module.caveat()
