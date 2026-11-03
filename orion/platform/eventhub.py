"""Événements normalisés et notifications (CORE FOUNDATION F3).

Trois étapes séparées, chacune gardée en base (``orion_ops.db``) :
1. **Événement** (``events``) : ce qui s'est passé, dans un format commun.
   - Contenu : type (« reminder.due », « mail.new »…), source, principal, titre, texte, priorité (low, normal, high,
     critical), clé de dédoublonnage, échéance de validité, données.
   - Provenance : la source et le principal sont fixés par le code d'ORION qui crée l'événement. Un module ne publie
     que sous sa propre identité, à travers un ``EventGate``.
2. **Décision** (``event_decisions``) : que faire de l'événement, selon une règle unique (``InterruptionPolicy`` et
   ``EventHub._decide``).
   - Permission du moteur central (``NOTIFY``), échéance, priorité, heures calmes, présence.
   - Issues possibles : ``announce``, ``deferred`` (après les heures calmes), ``kept`` (gardé sans annonce),
     ``denied``, ``missed`` (arrêt d'urgence), ``expired``, ``duplicate``.
3. **Remise** (``event_deliveries``) : l'annonce effective, canal par canal.
   - Une remise qui échoue (canal indisponible, ORION redémarré) reste en attente et est retentée jusqu'à
     l'échéance de l'événement : distribution persistante et reprise après indisponibilité.
   - La permission est revérifiée juste avant chaque remise.

Notifications manquées : un événement refusé pendant l'arrêt d'urgence est gardé ``missed``.
- **Jamais rejoué tout seul.**
- À la reprise, un récapitulatif dit combien et de quoi.
- Vous les consultez et relancez un par un (``relaunch``), sauf s'il est devenu obsolète (échéance passée).

Les alertes des modules V3 gardent leur routeur (orion/platform/alerts.py), qui suit la même règle de canaux
(``InterruptionPolicy.route``) et inscrit ses événements et décisions ici (``record``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from orion.platform.store import Migration, Store, dumps, loads
from orion.platform.vault import UNAVAILABLE, WITHHELD, Sealer

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            source TEXT NOT NULL,
            principal TEXT NOT NULL,
            priority TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            private INTEGER NOT NULL DEFAULT 0,
            dedup_key TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            expires_at REAL,
            request_id TEXT NOT NULL DEFAULT '',
            data TEXT NOT NULL DEFAULT '{}',
            missed TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX events_dedup ON events (source, dedup_key, created_at);
        CREATE INDEX events_missed ON events (missed, created_at);
        CREATE TABLE event_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            at REAL NOT NULL,
            outcome TEXT NOT NULL,
            channels TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX event_decisions_event ON event_decisions (event_id, id);
        CREATE TABLE event_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_at REAL NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            sent_at REAL
        );
        CREATE INDEX event_deliveries_due ON event_deliveries (status, next_at)
    """),
)

PRIORITIES = ("low", "normal", "high", "critical")
ANNOUNCE, DEFERRED, KEPT, DENIED, MISSED, EXPIRED, DUPLICATE = (
    "announce", "deferred", "kept", "denied", "missed", "expired", "duplicate")
PENDING, SENT, FAILED, CANCELLED = "pending", "sent", "failed", "cancelled"
WAITING = "waiting_return"  # remise retenue : maison vide, présentée à votre retour
AWAITING = "awaiting_return"  # décision : gardé pour votre retour
# Rappels et rendez-vous : jamais dits dans une maison vide ; gardés, présentés au retour s'ils sont encore valables
# (décision du 3 novembre 2026). Les autres annonces suivent leurs propres règles (mails : présence exigée par la V2).
RETURN_SOURCES = frozenset({"reminder", "calendar"})
MISSED_OPEN, RELAUNCHED, DISMISSED = "missed", "relaunched", "dismissed"
VOICE, WRITTEN, CALL = "voix", "écrit", "appel"
HOME, AWAY, UNKNOWN = "home", "away", "unknown"
TYPE = re.compile(r"^[a-z][a-z0-9_]{0,30}\.[a-z][a-z0-9_.]{0,40}$")
SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
STOPPED = ("arrêt d'urgence", "indéterminé")  # refus qui font une notification « manquée » (et non refusée)
USERS = frozenset({"user", "voix", "control"})
MAX_BACKOFF = 3600.0
DEFAULT_TTL = 6 * 3600.0


@dataclass(frozen=True)
class Event:
    type: str
    source: str
    principal: str
    title: str
    body: str = ""
    priority: str = "normal"
    dedup_key: str = ""
    expires_at: float | None = None
    private: bool = False  # texte privé : seul le titre est dit ou montré
    data: dict = field(default_factory=dict)
    request_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: float = 0.0

    def check(self) -> None:
        from orion.platform.permissions import PRINCIPAL

        if not TYPE.match(self.type or ""):
            raise ValueError(f"type d'événement invalide : {self.type!r} (« domaine.nature » attendu)")
        if not SOURCE.match(self.source or ""):
            raise ValueError(f"source invalide : {self.source!r}")
        if not PRINCIPAL.match(self.principal or ""):
            raise ValueError(f"principal invalide : {self.principal!r}")
        if self.priority not in PRIORITIES:
            raise ValueError(f"priorité parmi {', '.join(PRIORITIES)}")
        if not str(self.title).strip():
            raise ValueError("titre obligatoire")


@dataclass(frozen=True)
class Plan:
    """Canaux à essayer, dans l'ordre ; ``voice_fallback`` : l'écrit prend le relais si la voix échoue."""

    sequence: tuple[str, ...]
    voice_fallback: bool = False
    call: bool = False  # appel envisageable (budget et permission CALL vérifiés ensuite)
    skipped: dict = field(default_factory=dict)


class InterruptionPolicy:
    """La règle d'interruption, unique : qui peut interrompre, par quel canal, à quel moment."""

    LEVELS = {"low": 0, "normal": 1, "high": 2, "critical": 3}

    def __init__(self, quiet_start: str = "22:30", quiet_end: str = "08:00", critical_ignores_quiet: bool = False,
                 quiet_exempt: frozenset[str] = frozenset()):
        self.quiet_start, self.quiet_end = quiet_start, quiet_end
        self.critical_ignores_quiet = critical_ignores_quiet
        self.quiet_exempt = quiet_exempt  # sources dont vous avez fixé l'heure vous-même (rappel, réveil…)

    def quiet(self, now: datetime) -> bool:
        from orion.platform.alerts import in_quiet_hours

        return in_quiet_hours(now, self.quiet_start, self.quiet_end)

    def quiet_ends(self, now: datetime) -> datetime:
        hours, _, minutes = self.quiet_end.partition(":")
        end = now.replace(hour=int(hours), minute=int(minutes or 0), second=0, microsecond=0)
        return end if end > now else end + timedelta(days=1)

    def bypass(self, priority: str, source: str = "") -> bool:
        return source in self.quiet_exempt or (priority == "critical" and self.critical_ignores_quiet)

    def route(self, priority: str, presence: str, quiet: bool, *, source: str = "", written_only: bool = False,
              call_allowed: bool = True) -> Plan:
        """Canaux selon la présence et les heures calmes (alertes des modules et notifications)."""
        bypass = quiet and self.bypass(priority, source)
        if written_only:
            reason = "message écrit seulement (choix de l'utilisateur)"
            return Plan((WRITTEN,), skipped={VOICE: reason, CALL: reason})
        if presence == HOME:
            if quiet and not bypass:
                return Plan((WRITTEN,), skipped={VOICE: "heures calmes : message écrit à la place"})
            return Plan((VOICE,), voice_fallback=True)
        skipped = {}
        if presence == UNKNOWN:
            skipped[CALL] = "présence inconnue : jamais d'appel"
        elif self.LEVELS[priority] < self.LEVELS["high"]:
            skipped[CALL] = "niveau insuffisant pour un appel"
        elif not call_allowed:
            skipped[CALL] = "appel non autorisé par le module"
        elif quiet and not bypass:
            skipped[CALL] = "heures calmes"
        return Plan((WRITTEN,), call=CALL not in skipped, skipped=skipped)


@dataclass(frozen=True)
class Outcome:
    event_id: str
    outcome: str
    reason: str = ""
    channels: tuple[str, ...] = ()


class EventHub:
    def __init__(self, store: Store, *, permit: Callable[..., Any] | None = None,
                 policy: InterruptionPolicy | None = None, clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] | None = None, dedup_seconds: float = 12 * 3600.0,
                 sealer: Sealer | None = None):
        self._store, self._permit, self._clock = store, permit, clock
        # Lot F4 : titre, texte et données d'un événement sont personnels (rappel, expéditeur d'un mail) : scellés
        # avec la clé des données personnelles, ou retenus en mémoire seulement si elle manque.
        self.sealer = sealer or Sealer()
        self._held: dict[str, tuple[str, str, dict]] = {}
        self.presence: Callable[[], bool | None] | None = None  # maison occupée ? None : inconnu
        self._now = now or (lambda: datetime.fromtimestamp(self._clock()))
        self.policy = policy or InterruptionPolicy()
        self._dedup = dedup_seconds
        self._channels: dict[str, Callable[[dict], bool]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        store.migrate("events", MIGRATIONS)

    # --- Canaux -----------------------------------------------------------------------------------------------

    def channel(self, name: str, send: Callable[[dict], bool]) -> None:
        """``send(événement)`` : vrai si l'annonce est partie ; une exception ou False : nouvel essai plus tard."""
        self._channels[name] = send

    # --- Publication ------------------------------------------------------------------------------------------

    def publish(self, event: Event, channels: tuple[str, ...] = (VOICE,)) -> Outcome:
        """Événement, décision, puis remise (immédiate ou différée) par ``channels``."""
        event = self._stamp(event)
        with self._lock:
            duplicate = self._duplicate(event)
            if duplicate is not None:
                return Outcome(duplicate, DUPLICATE, "déjà publié récemment")
            self._insert(event)
            outcome, reason, when = self._decide(event)
            planned = channels if outcome in (ANNOUNCE, DEFERRED) else ()
            status = PENDING
            if planned and self._empty_house(event.source):
                outcome, reason, status = AWAITING, "maison vide : présenté à votre retour", WAITING
            self._decision(event.id, outcome, planned, reason)
            for name in planned:
                self._store.write("INSERT INTO event_deliveries (event_id, channel, status, next_at) "
                                  "VALUES (?, ?, ?, ?)", (event.id, name, status, when))
        if planned and outcome == ANNOUNCE:
            self.dispatch(event_id=event.id)
        return Outcome(event.id, outcome, reason, tuple(planned))

    def record(self, event: Event, outcome: str, channels: tuple[str, ...] = (), reason: str = "") -> str:
        """Événement déjà traité par son propre routeur (alertes des modules) : gardé avec sa décision ; un refus pour
        arrêt d'urgence le rend « manqué »."""
        event = self._stamp(event)
        with self._lock:
            self._insert(event)
            if outcome == DENIED and any(word in reason for word in STOPPED):
                outcome = MISSED
                self._store.write("UPDATE events SET missed = ? WHERE id = ?", (MISSED_OPEN, event.id))
            self._decision(event.id, outcome, channels, reason)
            for name in channels:
                self._store.write("INSERT INTO event_deliveries (event_id, channel, status, attempts, next_at, "
                                  "sent_at) VALUES (?, ?, ?, 1, ?, ?)", (event.id, name, SENT, event.created_at,
                                                                        event.created_at))
        return event.id

    def gate(self, source: str, principal: str) -> "EventGate":
        """Accès d'un module : il publie sous son identité, rien d'autre."""
        return EventGate(self, source, principal)

    def _stamp(self, event: Event) -> Event:
        from dataclasses import replace

        event.check()
        now = self._clock()
        return replace(event, created_at=event.created_at or now,
                       expires_at=event.expires_at if event.expires_at is not None else now + DEFAULT_TTL)

    def _duplicate(self, event: Event) -> str | None:
        if not event.dedup_key:
            return None
        row = self._store.one("SELECT id FROM events WHERE source = ? AND dedup_key = ? AND created_at >= ? "
                              "ORDER BY created_at DESC LIMIT 1",
                              (event.source, self.sealer.tag(event.dedup_key), event.created_at - self._dedup))
        return row["id"] if row else None

    def _insert(self, event: Event) -> None:
        self._store.write("INSERT INTO events (id, type, source, principal, priority, title, body, private, dedup_key, "
                          "created_at, expires_at, request_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (event.id, event.type, event.source, event.principal, event.priority,
                           self.sealer.seal(event.title[:200], f"events:{event.id}:title"),
                           self.sealer.seal(event.body[:900], f"events:{event.id}:body"), int(event.private),
                           self.sealer.tag(event.dedup_key[:120]), event.created_at, event.expires_at,
                           event.request_id[:40], self.sealer.seal(dumps(event.data), f"events:{event.id}:data")))
        if self.sealer.mode == WITHHELD:
            self._held[event.id] = (event.title[:200], event.body[:900], dict(event.data))

    def _decision(self, event_id: str, outcome: str, channels, reason: str) -> None:
        self._store.write("INSERT INTO event_decisions (event_id, at, outcome, channels, reason) VALUES (?, ?, ?, ?, ?)",
                          (event_id, self._clock(), outcome, dumps(list(channels)), reason[:200]))

    def _verdict(self, principal: str, source: str) -> tuple[bool, str]:
        if self._permit is None:
            return True, ""
        try:
            verdict = self._permit(principal, "NOTIFY", {}, action=f"event:{source}")
            return bool(verdict.allowed), verdict.reason
        except Exception:  # noqa: BLE001 - moteur en panne : échec sûr, l'événement est gardé
            log.exception("Événements : moteur de permissions en erreur")
            return False, "état de l'autonomie indéterminé (moteur en erreur)"

    def _decide(self, event: Event) -> tuple[str, str, float]:
        """La règle, dans l'ordre : échéance, permission, priorité, heures calmes."""
        now = self._clock()
        if event.expires_at is not None and event.expires_at <= now:
            return EXPIRED, "échéance dépassée", now
        allowed, reason = self._verdict(event.principal, event.source)
        if not allowed:
            if any(word in reason for word in STOPPED):
                self._store.write("UPDATE events SET missed = ? WHERE id = ?", (MISSED_OPEN, event.id))
                return MISSED, reason, now
            return DENIED, reason, now
        if event.priority == "low":
            return KEPT, "priorité basse : gardé sans annonce", now
        today = self._now()
        if self.policy.quiet(today) and not self.policy.bypass(event.priority, event.source):
            later = self.policy.quiet_ends(today).timestamp()
            if event.expires_at is not None and later >= event.expires_at:
                return KEPT, "heures calmes jusqu'après son échéance : gardé sans annonce", now
            return DEFERRED, "heures calmes : annoncé à leur fin", later
        return ANNOUNCE, reason or "permis", now

    # --- Remise -------------------------------------------------------------------------------------------------

    def dispatch(self, now: float | None = None, event_id: str | None = None) -> int:
        """Remises dues : permission revérifiée, échéance respectée ; rend le nombre de remises faites."""
        now = self._clock() if now is None else now
        where, params = "d.status = ? AND d.next_at <= ?", [PENDING, now]
        if event_id:
            where += " AND d.event_id = ?"
            params.append(event_id)
        rows = self._store.query(f"SELECT d.id AS delivery, d.channel, d.attempts, e.* FROM event_deliveries d "
                                 f"JOIN events e ON e.id = d.event_id WHERE {where} ORDER BY d.next_at LIMIT 50",
                                 params)
        sent = 0
        for row in rows:
            if row["expires_at"] is not None and row["expires_at"] <= now:
                self._delivery(row["delivery"], FAILED, row["attempts"], now, "échéance dépassée avant la remise")
                self._decision(row["id"], EXPIRED, (), "échéance dépassée avant la remise")
                continue
            allowed, reason = self._verdict(row["principal"], row["source"])
            if not allowed:  # arrêt d'urgence survenu entre la décision et la remise : manqué, jamais rejoué
                self._delivery(row["delivery"], CANCELLED, row["attempts"], now, reason)
                missed = any(word in reason for word in STOPPED)
                if missed:
                    self._store.write("UPDATE events SET missed = ? WHERE id = ? AND missed = ''", (MISSED_OPEN,
                                                                                                 row["id"]))
                self._decision(row["id"], MISSED if missed else DENIED, (), reason)
                continue
            if self._empty_house(row["source"]):  # parti entre la décision et la remise (heures calmes finies…)
                self._delivery(row["delivery"], WAITING, row["attempts"], now, "")
                self._decision(row["id"], AWAITING, (row["channel"],), "maison vide : présenté à votre retour")
                continue
            send = self._channels.get(row["channel"])
            if send is None:
                continue  # canal pas encore prêt (démarrage) : la remise attend
            try:
                ok = send(self._view(row)) is not False
                error = "" if ok else "refusé par le canal"
            except Exception as exc:  # noqa: BLE001 - canal indisponible : nouvel essai plus tard
                ok, error = False, f"{type(exc).__name__}: {exc}"[:200]
            attempts = row["attempts"] + 1
            if ok:
                self._delivery(row["delivery"], SENT, attempts, now, "", sent_at=now)
                sent += 1
            else:
                self._delivery(row["delivery"], PENDING, attempts, now + min(30.0 * 2 ** (attempts - 1),
                                                                             MAX_BACKOFF), error)
        return sent

    def _delivery(self, delivery_id: int, status: str, attempts: int, next_at: float, error: str,
                  sent_at: float | None = None) -> None:
        self._store.write("UPDATE event_deliveries SET status = ?, attempts = ?, next_at = ?, last_error = ?, "
                          "sent_at = ? WHERE id = ?", (status, attempts, next_at, error, sent_at, delivery_id))

    def _view(self, row: dict) -> dict:
        eid = row["id"]
        title = self.sealer.open(row["title"], f"events:{eid}:title")
        body = self.sealer.open(row["body"], f"events:{eid}:body")
        data = self.sealer.open(row["data"], f"events:{eid}:data")
        if title is None:  # retenu (clé absente) : en mémoire tant que le processus vit, sinon indisponible
            title, body, held = self._held.get(eid, (UNAVAILABLE, "", {}))
            data = dumps(held)
        return {"id": eid, "type": row["type"], "source": row["source"], "principal": row["principal"],
                "priority": row["priority"], "title": title, "body": body or "",
                "private": bool(row["private"]), "data": loads(data, {}), "created_at": row["created_at"],
                "expires_at": row["expires_at"], "available": title != UNAVAILABLE}

    # --- Notifications manquées ---------------------------------------------------------------------------------

    def missed(self, include_obsolete: bool = False, since: float | None = None) -> list[dict]:
        now = self._clock()
        rows = self._store.query("SELECT * FROM events WHERE missed = ? AND created_at >= ? ORDER BY created_at",
                                 (MISSED_OPEN, since or 0))
        found = []
        for row in rows:
            obsolete = row["expires_at"] is not None and row["expires_at"] <= now
            if obsolete and not include_obsolete:
                continue
            view = self._view(row)
            view["obsolete"] = obsolete
            found.append(view)
        return found

    def recap(self, since: float | None = None) -> str:
        """« Pendant l'arrêt d'urgence : 2 rappels, 1 annonce de mail. » ; "" s'il n'y a rien."""
        items = self.missed(include_obsolete=True, since=since)
        if not items:
            return ""
        counts: dict[str, int] = {}
        for item in items:
            label = LABELS.get(item["source"], "autre notification")
            counts[label] = counts.get(label, 0) + 1
        parts = [f"{n} {label}{'s' if n > 1 and not label.endswith('s') else ''}" for label, n in counts.items()]
        return "Pendant l'arrêt d'urgence, vous avez manqué : " + ", ".join(parts) + "."

    def relaunch(self, event_id: str, *, by: str) -> str:
        """Vous seul : remet un événement manqué maintenant. Rend « relaunched », « obsolete » ou « unknown »."""
        if by not in USERS:
            raise PermissionError("relance : voix, conversation ou ORION Control")
        row = self._store.one("SELECT * FROM events WHERE id = ? AND missed = ?", (event_id, MISSED_OPEN))
        if row is None:
            return "unknown"
        now = self._clock()
        if row["expires_at"] is not None and row["expires_at"] <= now:
            return "obsolete"  # devenu sans objet : jamais rejoué
        with self._lock:
            self._store.write("UPDATE events SET missed = ? WHERE id = ?", (RELAUNCHED, event_id))
            self._decision(event_id, ANNOUNCE, (VOICE,), f"relancé ({by})")
            self._store.write("INSERT INTO event_deliveries (event_id, channel, status, next_at) VALUES (?, ?, ?, ?)",
                              (event_id, VOICE, PENDING, now))
        self.dispatch(event_id=event_id)
        return RELAUNCHED

    def _empty_house(self, source: str) -> bool:
        """Vrai seulement si la maison est sûrement vide (présence inconnue : on annonce, comme avant)."""
        if source not in RETURN_SOURCES or self.presence is None:
            return False
        try:
            return self.presence() is False
        except Exception:  # noqa: BLE001 - présence illisible : inconnue
            log.exception("Événements : présence illisible")
            return False

    def on_return(self, *_args) -> int:
        """Votre retour : ce qui vous attend est présenté, sauf ce qui est devenu obsolète (gardé parmi les
        notifications manquées, jamais rejoué). Rend le nombre de remises relancées."""
        now = self._clock()
        with self._lock:
            rows = self._store.query("SELECT d.id AS delivery, d.attempts, e.id, e.expires_at FROM event_deliveries d "
                                     "JOIN events e ON e.id = d.event_id WHERE d.status = ? ORDER BY e.created_at",
                                     (WAITING,))
            ready = 0
            for row in rows:
                if row["expires_at"] is not None and row["expires_at"] <= now:
                    self._delivery(row["delivery"], CANCELLED, row["attempts"], now, "obsolète au retour")
                    self._store.write("UPDATE events SET missed = ? WHERE id = ? AND missed = ''",
                                      (MISSED_OPEN, row["id"]))
                    self._decision(row["id"], EXPIRED, (), "obsolète à votre retour : jamais rejoué")
                    continue
                self._delivery(row["delivery"], PENDING, row["attempts"], now, "")
                self._decision(row["id"], ANNOUNCE, (VOICE,), "votre retour")
                ready += 1
        if ready:
            self.dispatch()
        return ready

    def dismiss(self, event_id: str, *, by: str) -> bool:
        if by not in USERS:
            raise PermissionError("voix, conversation ou ORION Control")
        return self._store.write("UPDATE events SET missed = ? WHERE id = ? AND missed = ?",
                                 (DISMISSED, event_id, MISSED_OPEN)) == 1

    # --- Lecture ------------------------------------------------------------------------------------------------

    def recent(self, limit: int = 100) -> list[dict]:
        rows = self._store.query("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),))
        out = []
        for row in rows:
            view = self._view(row)
            if view["private"]:
                view["body"] = ""
            decision = self._store.one("SELECT outcome, reason, channels FROM event_decisions WHERE event_id = ? "
                                       "ORDER BY id DESC LIMIT 1", (row["id"],))
            deliveries = self._store.query("SELECT channel, status, attempts, last_error FROM event_deliveries "
                                           "WHERE event_id = ? ORDER BY id", (row["id"],))
            out.append({**view, "missed": row["missed"], "decision": decision, "deliveries": deliveries})
        return out

    def pending(self) -> int:
        row = self._store.one("SELECT COUNT(*) AS n FROM event_deliveries WHERE status = ?", (PENDING,))
        return int(row["n"]) if row else 0

    # --- Fil de fond ------------------------------------------------------------------------------------------

    def start(self, poll: float = 5.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(poll):
                try:
                    self.dispatch()
                except Exception:  # noqa: BLE001 - le fil ne meurt jamais
                    log.exception("Événements : remise en échec")

        self._thread = threading.Thread(target=loop, name="evenements", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)


LABELS = {"reminder": "rappel", "timer": "minuteur", "mail": "annonce de mail", "calendar": "rendez-vous",
          "routines": "annonce de routine", "flex": "annonce", "news": "alerte d'actualité", "watch": "surveillance",
          "purchase": "achat", "evolve": "évolution"}


class EventGate:
    """Ce qu'un module reçoit : publier sous sa propre identité (source et principal fixés)."""

    __slots__ = ("_hub", "source", "principal")

    def __init__(self, hub: EventHub, source: str, principal: str):
        if not SOURCE.match(source or "") or not principal.startswith(("module:", "delegated:")):
            raise ValueError("identité de module invalide")
        self._hub, self.source, self.principal = hub, source, principal

    def publish(self, type: str, title: str, body: str = "", *, priority: str = "normal", dedup_key: str = "",
                expires_at: float | None = None, private: bool = False, data: dict | None = None,
                channels: tuple[str, ...] = (VOICE,)) -> Outcome:
        if not type.startswith(self.source + "."):
            raise ValueError(f"un module ne publie que ses propres types ({self.source}.…)")
        return self._hub.publish(Event(type, self.source, self.principal, title, body, priority, dedup_key,
                                       expires_at, private, dict(data or {})), channels)
