"""ORION WATCH : surveillances persistantes, vérifiées périodiquement, qui préviennent quand leur condition est remplie.

Cycle d'une vérification :
1. expirée ? -> EXPIRED ;
2. chaque source autorisée est lue (limite de requêtes par source, cache de 5 min partagé entre surveillances) ;
   une source en panne est notée, jamais remplacée par une donnée inventée ;
3. la condition est évaluée par des règles (prix et score d'opportunité, disponibilité, événement, annonce) ;
4. ce qui est nouveau (jamais signalé pour cette surveillance) devient une alerte (présence, heures calmes, budget
   d'appels : orion/platform/alerts.py) et un événement ``watch.found`` (PURCHASE l'écoute, sans jamais y voir une
   autorisation d'achat) ;
5. mode « once » : COMPLETED après le premier déclenchement ; « repeat » : de nouveau ACTIVE.
Toutes les sources en échec plusieurs fois de suite -> FAILED (et une alerte) ; ``resume`` la relance.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from orion.events import Event, EventBus
from orion.platform.alerts import Alert, AlertRouter, Level
from orion.platform.history import History
from orion.platform.intelligence import StructuredLLM
from orion.platform.limits import RateLimiter
from orion.platform.modules import NOT_FOUND, BaseModule
from orion.platform.scheduler import Scheduler
from orion.platform.store import Migration, Store, dumps, loads
from orion.platform.feeds import SourceError
from orion.watch import events as ev
from orion.watch.connectors import ITEMS, OFFERS, ConnectorRegistry, Item, Offer
from orion.watch.interpret import interpret
from orion.watch.models import (
    ACTIVE, ANNOUNCEMENT, AVAILABILITY, COMPLETED, EVENT, EXPIRED, FAILED, PAUSED, PRICE, RUNNING, STATES, TRIGGERED,
    Finding, Watch, WatchError,
)
from orion.watch.opportunity import PriceHistory, evaluate, market_price
from orion.watch.products import matches, plain

log = logging.getLogger(__name__)

WATCH_FOUND = "watch.found"
RELIABILITY_RANK = {"unknown": 0, "community": 1, "media": 2, "agency": 3, "official": 4}
CACHE_SECONDS = 300.0
MAX_ALERTS_PER_CHECK = 3

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE watches (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            state TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE watch_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id TEXT NOT NULL,
            at REAL NOT NULL,
            ok INTEGER NOT NULL,
            summary TEXT NOT NULL,
            errors TEXT NOT NULL
        );
        CREATE INDEX watch_checks_watch ON watch_checks (watch_id, at);
        CREATE TABLE watch_seen (
            watch_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            at REAL NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            PRIMARY KEY (watch_id, fingerprint)
        )
    """),
)


@dataclass(frozen=True)
class WatchSettings:
    default_interval_minutes: float = 60.0
    min_interval_minutes: float = 10.0
    requests_per_source_per_hour: int = 20
    failure_limit: int = 10
    opportunity: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    ok: bool
    findings: list[Finding]
    summary: dict
    errors: dict


def _fingerprint(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


class WatchEngine(BaseModule):
    name, title = "watch", "WATCH"

    def __init__(self, store: Store, connectors: ConnectorRegistry, *, alerts: AlertRouter | None = None,
                 scheduler: Scheduler | None = None, history: History | None = None, events: EventBus | None = None,
                 llm: StructuredLLM | None = None, settings: WatchSettings | None = None,
                 clock: Callable[[], float] = time.time):
        self._store, self.connectors = store, connectors
        self._alerts, self._events, self._llm = alerts, events, llm
        self.llm = llm
        self.settings = settings or WatchSettings()
        self._clock = clock
        self._scheduler = scheduler or Scheduler(clock)
        self._history = history or History(store, clock)
        self.prices = PriceHistory(store, clock)
        self.calendar = ev.EventCalendar(store, clock)
        self._limiter = RateLimiter(max(1, self.settings.requests_per_source_per_hour), 3600.0, clock=clock)
        self._cache: dict[str, tuple[float, list]] = {}
        store.migrate("watch", MIGRATIONS)

    # --- Cycle de vie ----------------------------------------------------------------------------------------

    def start(self) -> None:
        """Les surveillances actives reprennent là où elles en étaient (redémarrage d'ORION)."""
        for watch in self.list():
            if watch.state in RUNNING:
                self._schedule(watch)

    def stop(self) -> None:
        for watch in self.list():
            self._scheduler.remove(f"watch:{watch.id}")

    def _schedule(self, watch: Watch) -> None:
        first = watch.next_check_at if watch.next_check_at is not None else self._clock()
        self._scheduler.add(f"watch:{watch.id}", watch.interval_s, lambda wid=watch.id: self.check(wid), first)

    # --- Persistance -----------------------------------------------------------------------------------------

    def _save(self, watch: Watch) -> None:
        self._store.write("INSERT INTO watches (id, data, state, updated_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(id) DO UPDATE SET data = excluded.data, state = excluded.state, "
                          "updated_at = excluded.updated_at", (watch.id, dumps(watch.as_dict()), watch.state,
                                                               self._clock()))

    def get(self, watch_id: str) -> Watch | None:
        row = self._store.one("SELECT data FROM watches WHERE id = ?", (watch_id,))
        return Watch.from_dict(loads(row["data"], {})) if row else None

    def list(self) -> list[Watch]:
        return [Watch.from_dict(loads(r["data"], {})) for r in
                self._store.query("SELECT data FROM watches ORDER BY updated_at DESC")]

    def _move(self, watch: Watch, state: str, why: str) -> None:
        STATES.check(watch.state, state)
        previous, watch.state = watch.state, state
        self._save(watch)
        self._history.record("watch", watch.id, "état", {"de": previous, "vers": state, "raison": why})
        if state not in RUNNING:
            self._scheduler.remove(f"watch:{watch.id}")

    # --- Création et commandes -------------------------------------------------------------------------------

    def create(self, spec: dict) -> Watch:
        """``spec`` : {"request": phrase} ou {"name", "condition", "sources"?, "interval_minutes"?, "expires_at"?,
        "channel"?, "mode"?, "description"?, "min_reliability"?}. WatchError si quelque chose ne va pas."""
        if not isinstance(spec, dict):
            raise WatchError("description de la surveillance attendue")
        if spec.get("request"):
            found = interpret(str(spec["request"])[:500], self._llm)
            spec = {**spec, "name": spec.get("name") or found.name, "condition": found.condition,
                    "description": spec.get("description") or str(spec["request"])[:300],
                    "sources": spec.get("sources") or self.connectors.names(found.source_kinds[0])}
        condition = spec.get("condition") or {}
        kind = OFFERS if condition.get("kind") in (PRICE, AVAILABILITY) else ITEMS
        sources = tuple(spec.get("sources") or self.connectors.names(kind))
        if not sources:
            raise WatchError("aucune source configurée pour ce type de surveillance ([watch] sources)")
        for name in sources:
            connector = self.connectors.get(name)
            if connector is None:
                raise WatchError(f"source inconnue : {name}")
            if connector.kind != kind:
                raise WatchError(f"la source {name} ne fournit pas ce type d'information")
        minutes = spec.get("interval_minutes", self.settings.default_interval_minutes)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise WatchError("fréquence en minutes attendue")
        minutes = max(float(minutes), self.settings.min_interval_minutes)
        expires = spec.get("expires_at")
        if expires is not None and (not isinstance(expires, (int, float)) or expires <= self._clock()):
            raise WatchError("date d'expiration dans le futur attendue")
        watch = Watch(spec.get("name", ""), condition, sources, minutes * 60, description=spec.get("description", ""),
                      expires_at=expires, channel=spec.get("channel", "auto"), mode=spec.get("mode", "once"),
                      min_reliability=spec.get("min_reliability", "media"), created_at=self._clock())
        watch.next_check_at = self._clock()
        self._save(watch)
        self._history.record("watch", watch.id, "créée", {"nom": watch.name, "condition": watch.condition,
                                                          "sources": list(sources)})
        self._schedule(watch)
        return watch

    def pause(self, watch_id: str) -> Watch:
        watch = self._require(watch_id)
        self._move(watch, PAUSED, "mise en pause")
        return watch

    def resume(self, watch_id: str) -> Watch:
        watch = self._require(watch_id)
        if watch.state == FAILED:
            watch.failures, watch.last_error = 0, ""
        self._move(watch, ACTIVE, "reprise")
        watch.next_check_at = self._clock()
        self._save(watch)
        self._schedule(watch)
        return watch

    def delete(self, watch_id: str) -> bool:
        watch = self.get(watch_id)
        if watch is None:
            return False
        self._scheduler.remove(f"watch:{watch_id}")
        with self._store.transaction():
            for table in ("watches", "watch_seen", "watch_checks"):
                column = "id" if table == "watches" else "watch_id"
                self._store.write(f"DELETE FROM {table} WHERE {column} = ?", (watch_id,))
        self._history.record("watch", watch_id, "supprimée", {"nom": watch.name})
        return True

    def _require(self, watch_id: str) -> Watch:
        watch = self.get(watch_id)
        if watch is None:
            raise KeyError(watch_id)
        return watch

    # --- Vérification ----------------------------------------------------------------------------------------

    def _read(self, source: str) -> list:
        now = self._clock()
        cached = self._cache.get(source)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        connector = self.connectors.get(source)
        if connector is None:
            raise SourceError("source inconnue")
        if not self._limiter.take(source, now):
            raise SourceError("limite de requêtes atteinte pour cette source")
        data = connector.fetch({})
        self._cache[source] = (now, data)
        return data

    def check(self, watch_id: str) -> CheckResult | None:
        watch = self.get(watch_id)
        if watch is None or watch.state not in RUNNING:
            self._scheduler.remove(f"watch:{watch_id}")
            return None
        now = self._clock()
        if watch.expires_at is not None and now >= watch.expires_at:
            self._move(watch, EXPIRED, "date d'expiration atteinte")
            return None
        observations: list = []
        errors: dict[str, str] = {}
        for source in watch.sources:
            try:
                observations += self._read(source)
            except SourceError as exc:
                errors[source] = str(exc)
            except Exception as exc:  # noqa: BLE001 - connecteur en erreur imprévue : noté, la surveillance continue
                log.exception("WATCH : source %s en erreur", source)
                errors[source] = type(exc).__name__
        ok = len(errors) < len(watch.sources)
        findings, summary = self._evaluate(watch, observations) if ok else ([], {})
        watch.last_check_at, watch.next_check_at = now, now + watch.interval_s
        self._store.write("INSERT INTO watch_checks (watch_id, at, ok, summary, errors) VALUES (?, ?, ?, ?, ?)",
                          (watch.id, now, int(ok), dumps(summary), dumps(errors)))
        if not ok:
            watch.failures += 1
            watch.last_error = "; ".join(f"{k} : {v}" for k, v in errors.items())[:300]
            self._save(watch)
            if watch.failures >= self.settings.failure_limit:
                self._move(watch, FAILED, f"{watch.failures} vérifications de suite sans aucune source")
                self._alert(watch, Alert("watch", f"Surveillance en échec : {watch.name}",
                                         f"La surveillance « {watch.name} » est arrêtée : aucune source ne répond "
                                         f"depuis {watch.failures} vérifications ({watch.last_error}).",
                                         Level.NORMAL, key=f"failed:{watch.id}", call_allowed=False))
            return CheckResult(False, [], summary, errors)
        watch.failures, watch.last_error = 0, ""
        new = [f for f in findings if self._first_time(watch, f)]
        self._save(watch)
        if new:
            self._trigger(watch, new)
        return CheckResult(True, new, summary, errors)

    def _first_time(self, watch: Watch, finding: Finding) -> bool:
        seen = self._store.one("SELECT 1 FROM watch_seen WHERE watch_id = ? AND fingerprint = ?",
                               (watch.id, finding.fingerprint))
        if seen is not None:
            return False
        self._store.write("INSERT INTO watch_seen (watch_id, fingerprint, at, title, url) VALUES (?, ?, ?, ?, ?)",
                          (watch.id, finding.fingerprint, self._clock(), finding.title[:200], finding.url[:500]))
        return True

    def _trigger(self, watch: Watch, findings: list[Finding]) -> None:
        self._move(watch, TRIGGERED, f"{len(findings)} élément(s) trouvé(s)")
        watch.triggers += 1
        self._save(watch)
        for finding in findings[:MAX_ALERTS_PER_CHECK]:
            self._history.record("watch", watch.id, "déclenchée", {"titre": finding.title, "url": finding.url,
                                                                   "source": finding.source, **finding.details})
            self._alert(watch, Alert("watch", finding.title, finding.message, Level(finding.level),
                                     key=f"{watch.id}:{finding.fingerprint}", details=finding.details))
            if self._events is not None:
                payload = {"watch_id": watch.id, "watch": watch.name, "kind": watch.condition["kind"],
                           "title": finding.title, "url": finding.url, "source": finding.source, **finding.details}
                self._events.publish(Event(WATCH_FOUND, "watch", payload))
        if len(findings) > MAX_ALERTS_PER_CHECK:
            self._history.record("watch", watch.id, "regroupées", {"non_annoncées": len(findings)
                                                                   - MAX_ALERTS_PER_CHECK})
        if watch.mode == "once":
            self._move(watch, COMPLETED, "condition remplie (une seule fois)")
        else:
            self._move(watch, ACTIVE, "reste active (mode répété)")

    def _alert(self, watch: Watch, alert: Alert) -> None:
        if self._alerts is None:
            return
        # Surveillance créée par vous : tâche programmée (CORE FOUNDATION F1), pas une initiative du module.
        alert = replace(alert, origin=f"delegated:watch:{watch.id}", written_only=watch.channel == "written"
                        or alert.written_only)
        self._alerts.deliver(alert)

    # --- Évaluation des conditions (règles) ------------------------------------------------------------------

    def _evaluate(self, watch: Watch, observations: list) -> tuple[list[Finding], dict]:
        kind = watch.condition["kind"]
        if kind in (PRICE, AVAILABILITY):
            return self._offers(watch, [o for o in observations if isinstance(o, Offer)])
        return self._items(watch, [o for o in observations if isinstance(o, Item)])

    def _offers(self, watch: Watch, offers: list[Offer]) -> tuple[list[Finding], dict]:
        c = watch.condition
        product = c["product"]
        relevant = [o for o in offers if matches(product, o.title) is not None]
        if c.get("condition") == "new":
            relevant = [o for o in relevant if o.condition == "new"]
        market = market_price(relevant)
        for offer in relevant:
            # Historique : vendeurs fiables, prix plausibles (une offre suspecte fausserait le « plus bas observé »).
            plausible = market is None or (offer.total is not None and offer.total >= market * 0.6)
            if offer.seller_rating is not None and offer.seller_rating >= 0.8 and plausible:
                self.prices.record(product, offer)
        stats = self.prices.stats(product)
        findings, summary = [], {"offres_vues": len(offers), "offres_du_produit": len(relevant),
                                 "prix_marché": market, "écartées": {}}
        best: tuple[int, str] | None = None
        for offer in relevant:
            if c["kind"] == AVAILABILITY and offer.available is False:
                self._forget(watch, _fingerprint("dispo", offer.seller.lower(), offer.url))  # rupture : le retour
                continue                                                                     # sera signalé
            opp = evaluate(offer, market, stats, self.settings.opportunity)
            if best is None or opp.score > best[0]:
                best = (opp.score, f"{offer.seller} {offer.total} €")
            reason = self._refusal(c, offer, opp)
            if reason:
                summary["écartées"][reason] = summary["écartées"].get(reason, 0) + 1
                continue
            details = {"vendeur": offer.seller, "prix": offer.price, "livraison": offer.shipping,
                       "total": offer.total, "score": opp.score, "appréciation": opp.label,
                       "raisons": list(opp.reasons), "données_manquantes": list(opp.missing),
                       "état": offer.condition, "disponible": offer.available, "garantie_mois": offer.warranty_months,
                       "retour_jours": offer.returns_days, "vendeur_note": offer.seller_rating,
                       "vendeur_avis": offer.seller_reviews, "place_de_marché": offer.marketplace}
            total = f"{offer.total:.0f} € livraison comprise" if offer.total is not None else "prix final inconnu"
            if c["kind"] == AVAILABILITY:
                fp = _fingerprint("dispo", offer.seller.lower(), offer.url)
                message = f"{offer.title[:90]} est disponible chez {offer.seller} ({total})."
                findings.append(Finding(fp, f"{product.upper()} disponible", message, offer.url, offer.source, details))
            else:
                fp = _fingerprint("prix", offer.seller.lower(), offer.url, offer.total)
                quality = f"score {opp.score}/100, {opp.label}"
                if opp.missing:
                    quality += " (données incomplètes)"
                message = f"{offer.title[:90]} chez {offer.seller} : {total} ; {quality}."
                findings.append(Finding(fp, f"{product.upper()} : {opp.label}", message, offer.url, offer.source,
                                        details, int(Level.HIGH)))
        summary["meilleure"] = best[1] if best else None
        return findings, summary

    @staticmethod
    def _refusal(c: dict, offer: Offer, opp) -> str:
        if offer.available is not True:
            return "disponibilité inconnue" if offer.available is None else "indisponible"
        if opp.suspicious:
            return "offre suspecte"
        if c.get("max_price") is not None:
            if offer.total is None:
                return "prix final inconnu"
            if offer.total > c["max_price"]:
                return "au-dessus du prix maximum"
        if c["kind"] == PRICE and c.get("min_score") is not None and opp.score < c["min_score"]:
            return "score insuffisant"
        return ""

    def _forget(self, watch: Watch, fingerprint: str) -> None:
        self._store.write("DELETE FROM watch_seen WHERE watch_id = ? AND fingerprint = ?", (watch.id, fingerprint))

    def _items(self, watch: Watch, items: list[Item]) -> tuple[list[Finding], dict]:
        c = watch.condition
        floor = RELIABILITY_RANK.get(watch.min_reliability, 2)
        findings, counts = [], {"articles_vus": len(items), "correspondants": 0, "rumeurs_ou_fuites": 0,
                                "sources_trop_peu_fiables": 0}
        for item in items:
            text = plain(f"{item.title} {item.summary}")
            if not all(k in text for k in (plain(k) for k in c["keywords"])):
                continue
            counts["correspondants"] += 1
            info = ev.classify(item, self._clock())
            if info.status in (ev.RUMOR, ev.LEAK):
                counts["rumeurs_ou_fuites"] += 1  # jamais annoncées comme officielles
                continue
            if RELIABILITY_RANK.get(item.reliability, 0) < floor and info.status not in (ev.CONFIRMED, ev.OFFICIAL):
                counts["sources_trop_peu_fiables"] += 1
                continue
            if not ev.satisfies(info, c["require"]):
                continue
            change = self.calendar.update(watch.id, info, item.url) if c["kind"] == EVENT else "new"
            if change == "same":
                continue
            what = "Calendrier modifié" if change == "changed" else (
                "Événement confirmé" if info.status == ev.CONFIRMED else "Annonce")
            when = f" ({info.dates_text})" if info.dates_text else ""
            message = f"{what} : {item.title[:140]}{when}. Statut : {info.label}. Source : {item.source}."
            fp = _fingerprint(c["kind"], info.key, info.start, info.end, info.status, item.url if c["kind"]
                              == ANNOUNCEMENT else "")
            findings.append(Finding(fp, f"{what} : {item.title[:70]}", message, item.url, item.source,
                                    {"statut": info.status, "début": info.start.isoformat() if info.start else None,
                                     "fin": info.end.isoformat() if info.end else None,
                                     "publié": item.published_at}, int(Level.HIGH if change == "changed"
                                                                         or info.status == ev.CONFIRMED
                                                                         else Level.NORMAL)))
        return findings, counts

    # --- Lecture pour ORION Control --------------------------------------------------------------------------

    def checks(self, watch_id: str, limit: int = 20) -> list[dict]:
        rows = self._store.query("SELECT * FROM watch_checks WHERE watch_id = ? ORDER BY at DESC, id DESC LIMIT ?",
                                 (watch_id, limit))
        return [{**r, "ok": bool(r["ok"]), "summary": loads(r["summary"], {}), "errors": loads(r["errors"], {})}
                for r in rows]

    def found(self, watch_id: str, limit: int = 50) -> list[dict]:
        return self._store.query("SELECT fingerprint, at, title, url FROM watch_seen WHERE watch_id = ? "
                                 "ORDER BY at DESC LIMIT ?", (watch_id, limit))

    def status(self) -> dict:
        watches = self.list()
        return {"surveillances": len(watches), "actives": sum(w.state in RUNNING for w in watches),
                "en_échec": sum(w.state == FAILED for w in watches), "sources": len(self.connectors.names())}

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        try:
            if not parts:
                if method == "GET":
                    return 200, [w.as_dict() for w in self.list()]
                if method == "POST":
                    return 201, self.create(data).as_dict()
            if parts == ["sources"] and method == "GET":
                return 200, self.connectors.describe()
            if parts == ["interpret"] and method == "POST":
                found = interpret(str((data or {}).get("request", ""))[:500], self._llm)
                return 200, {"name": found.name, "condition": found.condition, "by": found.by,
                             "sources": self.connectors.names(found.source_kinds[0])}
            if parts == ["prices"] and method == "GET":
                product = (query.get("product") or [""])[0]
                return 200, {"product": product, "stats": self.prices.stats(product).__dict__
                             if self.prices.stats(product) else None, "series": self.prices.series(product)}
            if len(parts) == 1:
                if method == "GET":
                    watch = self._require(parts[0])
                    return 200, {**watch.as_dict(), "checks": self.checks(watch.id), "found": self.found(watch.id),
                                 "history": self._history.list("watch", watch.id, 50),
                                 "calendar": self.calendar.known(watch.id)}
                if method == "DELETE":
                    return (200, {"status": "ok"}) if self.delete(parts[0]) else NOT_FOUND
            if len(parts) == 2 and method == "POST":
                action = {"pause": self.pause, "resume": self.resume}.get(parts[1])
                if action is not None:
                    return 200, action(parts[0]).as_dict()
                if parts[1] == "check":
                    self._require(parts[0])
                    result = self.check(parts[0])
                    return 200, None if result is None else {"ok": result.ok, "nouveaux": len(result.findings),
                                                             "résumé": result.summary, "erreurs": result.errors}
        except KeyError:
            return 404, {"status": "error", "error": "watch_not_found"}
        except (WatchError, ValueError) as exc:
            return 400, {"status": "error", "error": "invalid_watch", "message": str(exc)}
        return NOT_FOUND
