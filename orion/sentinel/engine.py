"""ORION SENTINEL : vue de santé unique, historique sur 7 jours, incidents (docs/v3/sentinel.md).

Lecture seule : SENTINEL interroge le service de ressources (lot F5) et n'agit sur rien. Les reprises restent celles
de chaque composant (le LLM à secours revient seul au Katana, la transcription au GPU, etc.) ; SENTINEL les constate.

- Sondage toutes les ``probe_seconds`` (tâche périodique, lecture : permise pendant la suspension de l'autonomie).
- Échantillons : un par changement d'état, plus un battement toutes les ``heartbeat_seconds`` ; au-delà de
  ``history_days`` jours, effacés. Disponibilité calculée au temps passé dans chaque état.
- Incident ouvert à la première panne d'une ressource configurée, fermé à son retour (« levé sans intervention »).
  Une alerte n'est envoyée qu'après ``notify_after_seconds`` de panne (pas de bruit pour une coupure de quelques
  secondes) ; le retour n'est annoncé que si la panne l'a été. Alertes d'initiative (``module:sentinel``) : soumises
  à l'autonomie et aux heures calmes comme les autres, toujours gardées dans l'historique.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from orion.platform.alerts import Alert, AlertRouter, Level
from orion.platform.history import History
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.resources import ResourceService
from orion.platform.scheduler import Scheduler
from orion.platform.store import Migration, Store

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE sentinel_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, resource TEXT NOT NULL,
            available INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX sentinel_samples_resource ON sentinel_samples (resource, at);
        CREATE TABLE sentinel_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT NOT NULL, label TEXT NOT NULL,
            started_at REAL NOT NULL, ended_at REAL, detail TEXT NOT NULL DEFAULT '',
            notified INTEGER NOT NULL DEFAULT 0, recovery TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX sentinel_incidents_open ON sentinel_incidents (resource, ended_at)
    """),
)
NOT_CONFIGURED = "non configuré"


@dataclass
class SentinelSettings:
    probe_seconds: float = 60.0
    heartbeat_seconds: float = 900.0
    history_days: int = 7
    notify_after_seconds: float = 120.0


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "moins d'une minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


class SentinelEngine(BaseModule):
    name, title = "sentinel", "SENTINEL"

    def __init__(self, store: Store, resources: ResourceService, *, alerts: AlertRouter | None = None,
                 scheduler: Scheduler | None = None, history: History | None = None,
                 settings: SentinelSettings | None = None, clock: Callable[[], float] = time.time):
        self._store, self._resources, self._alerts = store, resources, alerts
        self._clock = clock
        self._scheduler = scheduler or Scheduler(clock)
        self._history = history or History(store, clock)
        self.settings = settings or SentinelSettings()
        store.migrate("sentinel", MIGRATIONS)

    # --- Cycle de vie ---------------------------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.add("sentinel:probe", self.settings.probe_seconds, self.probe)

    def stop(self) -> None:
        self._scheduler.remove("sentinel:probe")

    # --- Sondage --------------------------------------------------------------------------------------------

    def probe(self) -> dict:
        """Un tour : état de chaque ressource, échantillons, incidents ouverts et fermés, alertes. Rend un résumé."""
        now = self._clock()
        report = {"changes": [], "opened": [], "closed": [], "notified": []}
        for row in self._resources.status()["resources"]:
            name, ok, detail = row["name"], bool(row["available"]), row["detail"] or ""
            if detail == NOT_CONFIGURED:
                continue  # rien à surveiller : jamais un incident
            last = self._store.one("SELECT at, available FROM sentinel_samples WHERE resource = ? ORDER BY at DESC, "
                                   "id DESC LIMIT 1", (name,))
            changed = last is None or bool(last["available"]) != ok
            if changed or now - last["at"] >= self.settings.heartbeat_seconds:
                self._store.write("INSERT INTO sentinel_samples (at, resource, available, detail) VALUES (?, ?, ?, ?)",
                                  (now, name, int(ok), detail[:160]))
            if changed and last is not None:
                report["changes"].append(name)
            incident = self._store.one("SELECT * FROM sentinel_incidents WHERE resource = ? AND ended_at IS NULL",
                                       (name,))
            if not ok and incident is None:
                self._store.write("INSERT INTO sentinel_incidents (resource, label, started_at, detail) "
                                  "VALUES (?, ?, ?, ?)", (name, row["label"], now, detail[:160]))
                report["opened"].append(name)
                log.warning("SENTINEL : %s indisponible (%s)", row["label"], detail or "—")
            elif not ok and not incident["notified"] and now - incident["started_at"] >= \
                    self.settings.notify_after_seconds:
                self._notify(incident, row, now, recovered=False)
                report["notified"].append(name)
            elif ok and incident is not None:
                self._store.write("UPDATE sentinel_incidents SET ended_at = ?, recovery = ? WHERE id = ?",
                                  (now, "levé sans intervention", incident["id"]))
                report["closed"].append(name)
                log.info("SENTINEL : %s de nouveau disponible après %s", row["label"],
                         duration(now - incident["started_at"]))
                if incident["notified"]:
                    self._notify(incident, row, now, recovered=True)
        self._purge(now)
        return report

    def _notify(self, incident: dict, row: dict, now: float, recovered: bool) -> None:
        behaviours = "; ".join(f"{w['module']} : {w['behaviour']}" for w in row.get("without", []))
        if recovered:
            title = f"{row['label']} de nouveau disponible"
            message = f"{row['label']} est revenu après {duration(now - incident['started_at'])}, sans intervention."
            level = Level.NORMAL  # la panne a été annoncée : son retour aussi
        else:
            title = f"{row['label']} indisponible"
            message = (f"{row['label']} ne répond plus depuis {duration(now - incident['started_at'])} "
                       f"({incident['detail'] or 'sans détail'}). " + (f"En attendant : {behaviours}." if behaviours
                                                                       else ""))
            level = Level.NORMAL
            self._store.write("UPDATE sentinel_incidents SET notified = 1 WHERE id = ?", (incident["id"],))
        if self._alerts is not None:
            self._alerts.deliver(Alert("sentinel", title, message, level,
                                       key=f"{incident['id']}:{'retour' if recovered else 'panne'}",
                                       call_allowed=False, origin="module:sentinel"))
        self._history.record("sentinel", str(incident["id"]), "retour" if recovered else "panne",
                             {"ressource": row["name"], "détail": incident["detail"]})

    def _purge(self, now: float) -> None:
        horizon = now - self.settings.history_days * 86400
        # Le dernier état connu avant la fenêtre reste : une panne commencée avant et toujours en cours compte.
        self._store.write("DELETE FROM sentinel_samples WHERE at < ? AND id NOT IN (SELECT MAX(id) FROM "
                          "sentinel_samples WHERE at < ? GROUP BY resource)", (horizon, horizon))
        self._store.write("DELETE FROM sentinel_incidents WHERE ended_at IS NOT NULL AND ended_at < ?", (horizon,))

    # --- Lecture --------------------------------------------------------------------------------------------

    def availability(self, resource: str, days: float | None = None) -> float | None:
        """Part du temps disponible sur la période (au temps passé dans chaque état), ou None sans mesure."""
        now = self._clock()
        start = now - (days or self.settings.history_days) * 86400
        before = self._store.one("SELECT available FROM sentinel_samples WHERE resource = ? AND at < ? ORDER BY at "
                                 "DESC, id DESC LIMIT 1", (resource, start))
        rows = self._store.query("SELECT at, available FROM sentinel_samples WHERE resource = ? AND at >= ? "
                                 "ORDER BY at, id", (resource, start))
        if not rows and before is None:
            return None
        points = ([(start, bool(before["available"]))] if before else []) + [(r["at"], bool(r["available"]))
                                                                                for r in rows]
        up = total = 0.0
        for (at, ok), nxt in zip(points, [p[0] for p in points[1:]] + [now]):
            total += nxt - at
            up += (nxt - at) if ok else 0.0
        return round(100 * up / total, 2) if total > 0 else (100.0 if points[-1][1] else 0.0)

    def incidents(self, limit: int = 50) -> list[dict]:
        now = self._clock()
        rows = self._store.query("SELECT * FROM sentinel_incidents ORDER BY started_at DESC LIMIT ?", (limit,))
        return [{**r, "duration": (r["ended_at"] or now) - r["started_at"], "open": r["ended_at"] is None}
                for r in rows]

    def status(self) -> dict:
        state = self._resources.status(refresh=False)
        resources = []
        for row in state["resources"]:
            resources.append({**row, "availability_7d": self.availability(row["name"])})
        open_incidents = [i for i in self.incidents(200) if i["open"]]
        return {"resources": resources, "open_incidents": open_incidents, "load": state["load"],
                "busy": state["busy"], "incidents_7d": len(self.incidents(500))}

    def summary(self) -> str:
        """Phrase dite à « comment va le système ? »."""
        now = self._clock()
        open_incidents = [i for i in self.incidents(200) if i["open"]]
        if not open_incidents:
            count = len(self.incidents(500))
            tail = f" {count} incident{'s' if count > 1 else ''} sur 7 jours, tous levés." if count else \
                " Aucun incident sur 7 jours."
            return "Tout fonctionne normalement." + tail
        parts = [f"{i['label']} indisponible depuis {duration(now - i['started_at'])}" for i in open_incidents]
        return "Problème en cours : " + " ; ".join(parts) + ". Le reste fonctionne, en mode dégradé si besoin."

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        if not parts and method == "GET":
            return 200, self.status()
        if parts == ["incidents"] and method == "GET":
            return 200, self.incidents()
        if parts == ["probe"] and method == "POST":
            return 200, self.probe()
        if parts == ["history"] and method == "GET":
            resource = (query.get("resource") or [""])[0]
            rows = self._store.query("SELECT at, available, detail FROM sentinel_samples WHERE resource = ? "
                                     "ORDER BY at DESC LIMIT 500", (resource,))
            return 200, rows
        return NOT_FOUND
