"""Module V3 « fragfactory » : ORION supervise Frag Factory sans dupliquer ses moteurs.

- **Synchronisation** (``fragfactory:sync``, toutes les ``poll_seconds``) : tire les événements de Frag Factory
  après le dernier curseur, les garde (dédoublonnés par identifiant), annonce ce qui doit l'être, puis acquitte.
  Frag Factory arrêté : la tâche échoue, le planificateur double l'attente (reconnexion), rien n'est perdu
  puisque Frag Factory garde ses événements jusqu'à l'accusé.
- **Notifications** : par le routeur d'alertes V3 (présence, heures calmes, voix / écrit, dédoublonnage) ; aucun
  système parallèle. Plusieurs clips arrivés ensemble : une seule annonce qui les liste.
- **Rapport de 08 h** (``fragfactory:report``) : une fois par jour, rattrapé au redémarrage le jour même, jamais en
  double ; la période part de la fin du rapport précédent (pas de trou, pas de chevauchement).
- **Contexte** : les clips annoncés ou présentés (« le deuxième », « valide-le »), gardés 30 minutes.

ORION ne garde que des identifiants et des métadonnées courtes ; vidéos et analyses restent dans Frag Factory.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable

from orion.fragfactory import references
from orion.platform.alerts import Alert, AlertRouter, Level
from orion.platform.history import History
from orion.platform.modules import BaseModule
from orion.platform.scheduler import Scheduler
from orion.platform.store import Migration, Store, dumps, loads
from orion.tools.base import ToolError

log = logging.getLogger(__name__)

SOURCE = "fragfactory"
SYNC_JOB, REPORT_JOB = "fragfactory:sync", "fragfactory:report"
NOTIFY_ORIGIN = "delegated:fragfactory:notifications"
REPORT_ORIGIN = "delegated:fragfactory:report"
CONTEXT_TTL = 30 * 60
UNREACHABLE_CODES = frozenset({"device_unreachable", "agent_offline", "agent_token", "fragfactory_unavailable", "link_not_configured", "http_503",
                               "timeout"})
NOT_FOUND = (404, {"status": "error", "error": "not_found"})
TIER_SPOKEN = {"pepite": "pépite", "bon": "bon clip", "a_trier": "à trier"}
_STATUS_WORDS = {"APPROVED": "validé", "REJECTED": "refusé", "PUBLISHED": "publié"}

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE ff_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE ff_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE ff_events (
            id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL,
            received_at REAL NOT NULL,
            announced TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX ff_events_type ON ff_events (type, received_at);
        CREATE TABLE ff_reports (
            day TEXT PRIMARY KEY,
            report_id INTEGER,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            spoken TEXT NOT NULL,
            channels TEXT NOT NULL DEFAULT '[]',
            at REAL NOT NULL
        );
        CREATE TABLE ff_actions (
            request_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            clip_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            at REAL NOT NULL
        )
    """),
)


@dataclass(frozen=True)
class FragFactorySettings:
    poll_seconds: float = 30.0
    report_enabled: bool = True
    report_time: str = "08:00"
    timezone: str = "Europe/Paris"
    voice_enabled: bool = True
    written_enabled: bool = True
    notify_tiers: tuple = ("pepite", "bon")
    announce_failures: bool = True
    max_listed: int = 3


def _time(text: Any) -> str:
    if not isinstance(text, str):
        raise ValueError("heure attendue (« 08:00 »)")
    hours, _, minutes = text.partition(":")
    if not (hours.isdigit() and minutes.isdigit() and 0 <= int(hours) < 24 and 0 <= int(minutes) < 60):
        raise ValueError(f"heure invalide : {text!r}")
    return f"{int(hours):02d}:{int(minutes):02d}"


class Unavailable(Exception):
    """Frag Factory (ou l'agent du PC) ne répond pas."""


class FragFactoryModule(BaseModule):
    name, title = "fragfactory", "Frag Factory"

    def __init__(self, store: Store, transport: Callable[[str, dict], Any], *, alerts: AlertRouter | None = None,
                 scheduler: Scheduler | None = None, history: History | None = None,
                 settings: FragFactorySettings | None = None, clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] | None = None):
        self._store, self._call, self._alerts = store, transport, alerts
        self._defaults = settings or FragFactorySettings()
        self._clock = clock
        self._now_override = now
        self._scheduler = scheduler or Scheduler(clock)
        self._history = history or History(store, clock)
        store.migrate(SOURCE, MIGRATIONS)

    # --- Réglages ---------------------------------------------------------------------------------------------
    @property
    def settings(self) -> FragFactorySettings:
        saved = {r["key"]: loads(r["value"]) for r in self._store.query("SELECT key, value FROM ff_settings")}
        d = self._defaults
        values = {k: saved.get(k, getattr(d, k)) for k in asdict(d)}
        values["notify_tiers"] = tuple(values["notify_tiers"])
        return FragFactorySettings(**values)

    def update_settings(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("réglages attendus")
        clean: dict[str, Any] = {}
        for flag in ("report_enabled", "voice_enabled", "written_enabled", "announce_failures"):
            if flag in data:
                if not isinstance(data[flag], bool):
                    raise ValueError(f"{flag} : vrai ou faux")
                clean[flag] = data[flag]
        if "report_time" in data:
            clean["report_time"] = _time(data["report_time"])
        if "poll_seconds" in data:
            v = data["poll_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 10 <= v <= 3600:
                raise ValueError("fréquence de synchronisation : de 10 à 3600 secondes")
            clean["poll_seconds"] = float(v)
        if "notify_tiers" in data:
            tiers = data["notify_tiers"]
            if not isinstance(tiers, list) or not set(tiers) <= set(TIER_SPOKEN):
                raise ValueError("niveaux annoncés parmi pepite, bon, a_trier")
            clean["notify_tiers"] = list(dict.fromkeys(tiers))
        if "timezone" in data:
            if not isinstance(data["timezone"], str) or not 1 <= len(data["timezone"]) <= 64:
                raise ValueError("fuseau horaire invalide")
            clean["timezone"] = data["timezone"]
        with self._store.transaction():
            for key, value in clean.items():
                self._store.write("INSERT INTO ff_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                                  "SET value = excluded.value", (key, dumps(value)))
        if "poll_seconds" in clean and self._scheduler.has(SYNC_JOB):
            self._scheduler.remove(SYNC_JOB)
            self._scheduler.add(SYNC_JOB, clean["poll_seconds"], self.sync)
        self._history.record(SOURCE, "", "réglages", clean)
        return asdict(self.settings)

    # --- Cycle de vie -----------------------------------------------------------------------------------------
    def start(self) -> None:
        s = self.settings
        self._scheduler.add(SYNC_JOB, s.poll_seconds, self.sync)
        self._scheduler.add(REPORT_JOB, 60, self.report_due)

    def stop(self) -> None:
        self._scheduler.remove(SYNC_JOB)
        self._scheduler.remove(REPORT_JOB)

    # --- Transport --------------------------------------------------------------------------------------------
    def request(self, op: str, args: dict | None = None) -> Any:
        """Appel d'une opération ; ``Unavailable`` si Frag Factory ou l'agent ne répond pas."""
        try:
            data = self._call(op, dict(args or {}))
        except ToolError as exc:
            if exc.code in UNREACHABLE_CODES:
                self._state("link", {"ok": False, "error": exc.message[:160], "code": exc.code})
                raise Unavailable(exc.message) from exc
            self._state("link", {"ok": True})
            raise
        self._state("link", {"ok": True})
        return data

    def _state(self, key: str, value: Any) -> None:
        self._store.write("INSERT INTO ff_state (key, value, at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                          "value = excluded.value, at = excluded.at", (key, dumps(value), self._clock()))

    def _get_state(self, key: str) -> tuple[Any, float | None]:
        row = self._store.one("SELECT value, at FROM ff_state WHERE key = ?", (key,))
        return (loads(row["value"]), row["at"]) if row else (None, None)

    # --- Synchronisation et notifications --------------------------------------------------------------------
    def sync(self, max_pages: int = 10) -> dict:
        """Tire, garde, annonce, acquitte. Rend un résumé (pour ORION Control et les tests)."""
        cursor = int((self._get_state("cursor")[0] or 0))
        received, fresh = 0, []
        for _ in range(max_pages):
            page = self.request("events", {"after": cursor, "limit": 100})
            events = page.get("events") or []
            if not events:
                break
            for ev in events:
                if not isinstance(ev, dict) or not isinstance(ev.get("seq"), int) or not isinstance(ev.get("id"), str):
                    continue  # donnée non fiable : un élément mal formé est ignoré, pas exécuté
                if self._remember(ev):
                    fresh.append(ev)
                cursor = max(cursor, ev["seq"])
                received += 1
            if len(events) < 100:
                break
        announced = self._announce(fresh) if fresh else []
        if received:
            # Accusé APRÈS l'enregistrement local : un arrêt entre les deux fait relire, jamais perdre.
            self.request("ack", {"upto": cursor})
            self._state("cursor", cursor)
        self._state("last_sync", {"received": received, "new": len(fresh), "announced": announced})
        return {"received": received, "new": len(fresh), "cursor": cursor, "announced": announced}

    def _remember(self, ev: dict) -> bool:
        if self._store.one("SELECT 1 FROM ff_events WHERE id = ?", (ev["id"][:64],)):
            return False  # déjà reçu (relu après un arrêt) : jamais annoncé deux fois
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        resource = ev.get("resource") if isinstance(ev.get("resource"), dict) else {}
        self._store.write("INSERT INTO ff_events (id, seq, type, resource_id, title, occurred_at, received_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (ev["id"][:64], ev["seq"], str(ev.get("type", ""))[:80], str(resource.get("id", ""))[:32],
                           str(payload.get("title") or payload.get("filename") or "")[:120],
                           str(ev.get("occurred_at", ""))[:40], self._clock()))
        return True

    def _level(self, base: Level) -> tuple[Level, bool]:
        """Niveau et « écrit seulement » selon les canaux que vous avez activés pour Frag Factory."""
        s = self.settings
        if not s.voice_enabled and not s.written_enabled:
            return Level.INFO, False
        if not s.written_enabled and self._alerts is not None and self._alerts.presence() != "home":
            return Level.INFO, False  # absent et écrit désactivé : gardé dans l'historique seulement
        return base, not s.voice_enabled

    def _deliver(self, title: str, message: str, key: str, base: Level = Level.NORMAL,
                 origin: str = NOTIFY_ORIGIN) -> list[str]:
        if self._alerts is None:
            return []
        level, written_only = self._level(base)
        d = self._alerts.deliver(Alert(SOURCE, title, message, level, key=key, call_allowed=False,
                                       written_only=written_only, origin=origin))
        return list(d.channels)

    def _announce(self, events: list[dict]) -> list[str]:
        s = self.settings
        ready = [e for e in events if e.get("type") == "fragfactory.clip.ready_for_review"
                 and (e.get("payload") or {}).get("tier") in s.notify_tiers]
        out: list[str] = []
        if ready:
            payloads = [e["payload"] for e in ready]
            self._present([p.get("clip_id") for p in payloads if isinstance(p.get("clip_id"), int)])
            if len(payloads) == 1:
                text = clip_announcement(payloads[0])
                title = f"Nouveau clip : {str(payloads[0].get('title', ''))[:80]}"
            else:
                text = batch_announcement(payloads, s.max_listed)
                title = f"{len(payloads)} nouveaux clips Frag Factory"
            key = "ready:" + ",".join(str(p.get("render_id")) for p in payloads)
            channels = self._deliver(title, text, key)
            for e in ready:
                self._store.write("UPDATE ff_events SET announced = ? WHERE id = ?", (",".join(channels) or "gardé",
                                                                                      e["id"][:64]))
            out.append(f"{len(payloads)} clip(s)")
        if s.announce_failures:
            failures = [e for e in events if e.get("type") in ("fragfactory.clip.render_failed",
                                                               "fragfactory.analysis.failed")]
            if failures:
                text = (f"Frag Factory signale {len(failures)} échec(s) : "
                        + "; ".join(_failure(e) for e in failures[:3]) + ".")
                self._deliver("Frag Factory : échec de traitement", text,
                              "failure:" + ",".join(e["id"][:8] for e in failures))
                out.append(f"{len(failures)} échec(s)")
        warnings = [e for e in events if e.get("type") == "fragfactory.system.warning"]
        for w in warnings[:3]:
            msg = str((w.get("payload") or {}).get("message") or "Avertissement de Frag Factory")[:300]
            self._deliver("Frag Factory : avertissement", msg, f"warning:{w['id'][:12]}")
            out.append("avertissement")
        self._history.record(SOURCE, "", "synchronisation", {"événements": len(events), "annonces": out})
        return out

    # --- Rapport du matin -------------------------------------------------------------------------------------
    def _local_now(self) -> datetime:
        if self._now_override is not None:
            return self._now_override()
        tz = self.settings.timezone
        try:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(self._clock(), ZoneInfo(tz))
        except Exception:  # noqa: BLE001 - fuseau absent (Windows sans tzdata) : heure locale de la machine
            return datetime.fromtimestamp(self._clock())

    def report_due(self) -> dict | None:
        """Chaque minute : le rapport du jour après l'heure réglée, rattrapé dans la journée, jamais en double."""
        s = self.settings
        if not s.report_enabled:
            return None
        now = self._local_now()
        hours, minutes = map(int, s.report_time.split(":"))
        if now < now.replace(hour=hours, minute=minutes, second=0, microsecond=0):
            return None
        day = now.strftime("%Y-%m-%d")
        if self._store.one("SELECT 1 FROM ff_reports WHERE day = ?", (day,)):
            return None
        return self.deliver_report(day)

    def deliver_report(self, day: str) -> dict:
        previous = self._store.one("SELECT period_end FROM ff_reports ORDER BY day DESC LIMIT 1")
        args: dict[str, Any] = {"request_key": f"orion-report-{day}"}
        if previous:
            args["since"] = previous["period_end"]
        report = self.request("report", args)  # même clé : Frag Factory rend le même rapport, jamais deux
        spoken = str(report.get("spoken") or "")[:900]
        channels = self._deliver(f"Bilan Frag Factory du {day[8:10]}/{day[5:7]}", f"Bonjour Monsieur. {spoken}",
                                 f"report:{day}", origin=REPORT_ORIGIN)
        self._store.write("INSERT INTO ff_reports (day, report_id, period_start, period_end, spoken, channels, at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (day, report.get("id"), str(report.get("period_start", ""))[:40],
                           str(report.get("period_end", ""))[:40], spoken, dumps(channels), self._clock()))
        self._history.record(SOURCE, f"report:{day}", "rapport", {"canaux": channels})
        return {"day": day, "report_id": report.get("id"), "channels": channels}

    # --- Contexte de conversation -----------------------------------------------------------------------------
    def _present(self, clip_ids: list[int]) -> None:
        ids = [int(i) for i in clip_ids if isinstance(i, int)][:20]
        if ids:
            self._state("presented", ids)
            self._state("focus", ids[0] if len(ids) == 1 else None)

    def focus(self, clip_id: int) -> None:
        self._state("focus", int(clip_id))

    def _fresh(self, key: str) -> Any:
        value, at = self._get_state(key)
        if at is None or self._clock() - at > CONTEXT_TTL:
            return None
        return value

    def resolve(self, ref: str, *, pending_only: bool = False) -> str:
        """Référence textuelle -> clip concret (``clip:<id>``), vérifié chez Frag Factory. Jamais au hasard."""
        try:
            kind, arg = references.parse(ref)
        except ValueError as exc:
            raise ToolError("invalid_parameters", "Je ne sais pas de quel clip vous parlez.") from exc
        try:
            if kind == "clip":
                return ref
            if kind == "id":
                clip = self.request("clip", {"clip_id": int(arg)})
                if pending_only and clip.get("status") not in ("PENDING_REVIEW", "REVISION_REQUESTED"):
                    # Déjà décidé : on le dit tout de suite, sans poser de question de confirmation inutile.
                    raise ToolError("already_decided", f"« {str(clip.get('title', ''))[:60]} » est déjà "
                                                       f"{_STATUS_WORDS.get(clip.get('status'), 'traité')}.")
                return f"clip:{clip['id']}"
            if kind == "ordinal":
                presented = self._fresh("presented") or []
                n = int(arg)
                if not presented:
                    raise ToolError("ambiguous", "Je ne vous ai présenté aucune liste récemment : de quel clip parlez-vous ?")
                if not 0 < n <= len(presented):
                    raise ToolError("not_found", f"La dernière liste ne compte que {len(presented)} clip(s).")
                return f"clip:{presented[n - 1]}"
            if kind == "context":
                focus = self._fresh("focus")
                if not focus:
                    raise ToolError("ambiguous", "De quel clip parlez-vous ? Dites par exemple « le dernier clip ».")
                return f"clip:{int(focus)}"
            args: dict[str, Any] = {"order": "recent", "limit": 5}
            if pending_only:
                args["status"] = "PENDING_REVIEW"
            if kind == "latest":
                if arg:
                    args["tier"] = arg
                items = self.request("clips", {**args, "limit": 1}).get("items") or []
                if not items:
                    raise ToolError("not_found", "Je ne trouve aucun clip correspondant"
                                                 + (" en attente de validation." if pending_only else "."))
                return f"clip:{items[0]['id']}"
            items = self.request("clips", {**args, "q": arg}).get("items") or []
            if not items:
                raise ToolError("not_found", f"Je ne trouve aucun clip qui corresponde à « {arg} ».")
            if len(items) > 1:
                self._present([i["id"] for i in items])
                listed = " ; ".join(f"{n}. {i['title'][:60]}" for n, i in enumerate(items[:3], 1))
                raise ToolError("ambiguous", f"J'en trouve {len(items)} : {listed}. Lequel ? Dites « le premier », "
                                             "« le deuxième »…")
            return f"clip:{items[0]['id']}"
        except Unavailable as exc:
            raise ToolError("fragfactory_unavailable", "Frag Factory ne répond pas pour le moment.") from exc
        except ToolError as exc:
            if exc.code in ("http_404", "unknown_clip"):
                raise ToolError("not_found", "Ce clip n'existe pas dans Frag Factory.") from exc
            raise

    # --- Actions (appelées par les outils, après confirmation) --------------------------------------------------
    def act(self, action: str, clip_id: int, *, expected: str | None = None, **extra: Any) -> dict:
        """Décision transmise à Frag Factory avec une clé d'idempotence ; une erreur n'est jamais une validation."""
        request_id = f"orion-{action}-{clip_id}-{uuid.uuid4().hex[:12]}"
        op = {"approve": "decide", "reject": "decide", "revision": "revision", "version": "version"}[action]
        args: dict[str, Any] = {"clip_id": clip_id, "request_id": request_id, **extra}
        if op == "decide":
            args["action"] = action
        if expected:
            args["expected_status"] = expected
        try:
            result = self.request(op, args)
        except Unavailable as exc:
            self._log_action(request_id, action, clip_id, "injoignable")
            raise ToolError("fragfactory_unavailable", "Frag Factory ne répond pas : rien n'a été fait.") from exc
        except ToolError as exc:
            self._log_action(request_id, action, clip_id, f"refusé : {exc.code}")
            raise
        self._log_action(request_id, action, clip_id, "fait")
        self.focus(clip_id)
        return result if isinstance(result, dict) else {}

    def _log_action(self, request_id: str, action: str, clip_id: int, outcome: str) -> None:
        self._store.write("INSERT OR REPLACE INTO ff_actions (request_id, action, clip_id, outcome, at) "
                          "VALUES (?, ?, ?, ?, ?)", (request_id, action, clip_id, outcome[:80], self._clock()))
        self._history.record(SOURCE, f"clip:{clip_id}", action, {"issue": outcome})

    # --- État et ORION Control ----------------------------------------------------------------------------------
    def status(self) -> dict:
        link, link_at = self._get_state("link")
        last_sync, sync_at = self._get_state("last_sync")
        last_report = self._store.one("SELECT day, period_start, period_end, at FROM ff_reports ORDER BY day DESC "
                                      "LIMIT 1")
        return {"connected": bool(link and link.get("ok")), "last_contact": link_at,
                "error": (link or {}).get("error"), "cursor": self._get_state("cursor")[0] or 0,
                "last_sync": last_sync, "last_sync_at": sync_at, "last_report": last_report,
                "events_received": (self._store.one("SELECT COUNT(*) AS n FROM ff_events") or {}).get("n", 0),
                "settings": asdict(self.settings)}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        try:
            if not parts and method == "GET":
                return 200, self.status()
            if parts == ["settings"]:
                if method == "GET":
                    return 200, asdict(self.settings)
                if method == "PUT":
                    return 200, self.update_settings(data)
            if parts == ["events"] and method == "GET":
                return 200, self._store.query("SELECT * FROM ff_events ORDER BY received_at DESC, seq DESC LIMIT 100")
            if parts == ["reports"] and method == "GET":
                return 200, self._store.query("SELECT * FROM ff_reports ORDER BY day DESC LIMIT 30")
            if parts == ["actions"] and method == "GET":
                return 200, self._store.query("SELECT * FROM ff_actions ORDER BY at DESC LIMIT 100")
            if parts == ["sync"] and method == "POST":
                return 200, self.sync()
            if parts == ["pending"] and method == "GET":
                return 200, self.request("clips", {"status": "PENDING_REVIEW", "order": "score", "limit": 20})
            if parts == ["alerts"] and method == "GET":
                return 200, self._alerts.history(100, SOURCE) if self._alerts is not None else []
        except ValueError as exc:
            return 400, {"status": "error", "error": "invalid_settings", "message": str(exc)}
        except Unavailable as exc:
            return 503, {"status": "error", "error": "fragfactory_unavailable", "message": str(exc)}
        except ToolError as exc:
            return 502, {"status": "error", "error": exc.code, "message": exc.message}
        return NOT_FOUND


# --- Textes dits (uniquement des valeurs reçues de Frag Factory) ----------------------------------------------------
def _pct(value: Any) -> str | None:
    return f"{round(float(value) * 100)}/100" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def clip_announcement(p: dict) -> str:
    parts = ["Monsieur, un nouveau clip vient d'être produit.", f"Titre : {str(p.get('title', ''))[:100]}."]
    cats = p.get("category_labels") or []
    if cats:
        parts.append(f"Catégories : {', '.join(str(c) for c in cats[:4])}.")
    dur = p.get("duration_s")
    if isinstance(dur, (int, float)) and dur > 0:
        parts.append(f"Durée : {round(dur)} secondes.")
    if p.get("tier_label"):
        parts.append(f"Classement : {p['tier_label']}.")
    comedy = _pct((p.get("scores") or {}).get("comedy"))
    if comedy and (p.get("scores") or {}).get("comedy", 0) >= 0.3:
        parts.append(f"Score humoristique : {comedy}.")
    if p.get("summary"):
        parts.append(str(p["summary"])[:200].rstrip(".") + ".")
    if (p.get("version") or 1) > 1:
        parts.append(f"C'est la version {p['version']} de ce clip.")
    parts.append("Le clip est disponible pour validation.")
    return " ".join(parts)


def batch_announcement(payloads: list[dict], max_listed: int = 3) -> str:
    tiers: dict[str, int] = {}
    for p in payloads:
        tiers[p.get("tier", "")] = tiers.get(p.get("tier", ""), 0) + 1
    detail = ", ".join(f"{n} {TIER_SPOKEN.get(t, t)}{'s' if n > 1 and t != 'a_trier' else ''}" for t, n in tiers.items())
    listed = " ; ".join(f"{i}. {str(p.get('title', ''))[:60]}" for i, p in enumerate(payloads[:max_listed], 1))
    more = f" et {len(payloads) - max_listed} autre(s)" if len(payloads) > max_listed else ""
    return (f"Monsieur, {len(payloads)} nouveaux clips sont prêts : {detail}. {listed}{more}. "
            "Ils attendent votre validation.")


def _failure(e: dict) -> str:
    p = e.get("payload") or {}
    what = "rendu" if e.get("type") == "fragfactory.clip.render_failed" else "analyse"
    name = p.get("filename") or p.get("title") or f"#{(e.get('resource') or {}).get('id', '?')}"
    return f"{what} de {str(name)[:60]}"


__all__ = ["FragFactoryModule", "FragFactorySettings", "Unavailable", "clip_announcement", "batch_announcement",
           "replace", "field", "timedelta"]
