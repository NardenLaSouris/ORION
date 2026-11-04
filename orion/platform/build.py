"""Construction des modules V3 à partir de la configuration (appelé par orion/factory.py).

En deux temps, parce que les outils vocaux sont enregistrés avant le gestionnaire d'appels :
1. ``build_v3`` crée la base, le planificateur, le routeur d'alertes et les modules activés, et rend leurs outils ;
2. ``V3Modules.connect`` branche les appels et les messages Linphone, puis démarre les modules.
Aucun module activé : ``build_v3`` rend None et rien de V3 n'existe (ORION se comporte comme V2).
"""

from __future__ import annotations

import logging
from typing import Any

from orion.platform.alerts import AlertRouter, AlertSettings
from orion.platform.permissions import Capability
from orion.platform.history import History
from orion.platform.intelligence import StructuredLLM
from orion.platform.modules import BaseModule, ModuleRegistry
from orion.platform.scheduler import Scheduler
from orion.platform.store import PUBLIC, Store

log = logging.getLogger(__name__)


class V3Modules:
    def __init__(self, store: Store, scheduler: Scheduler, alerts: AlertRouter, registry: ModuleRegistry,
                 tools: list):
        self.store, self.scheduler, self.alerts, self.registry, self.tools = store, scheduler, alerts, registry, tools
        self.contexts: dict = {}
        self.knowledge = None  # module KNOWLEDGE, s'il est actif
        self.calendar_provider = None  # agenda Google synchronisé, s'il est actif
        self._calls = None

    def connect(self, calls: Any | None) -> None:
        """Appels et messages écrits (passerelle Linphone), puis démarrage des modules et du planificateur."""
        self._calls = calls
        self.registry.start()
        self.scheduler.start()

    def call(self, text: str) -> bool:
        if self._calls is None:
            return False
        from orion.call import CallRefused

        try:
            call = self._calls.request(text, "alert")
        except CallRefused as exc:
            log.info("Alerte : appel refusé (%s)", exc)
            return False
        return not getattr(call, "final", False)

    def write(self, text: str) -> bool:
        return bool(self._calls is not None and self._calls.reply(text))

    def stop(self) -> None:
        self.scheduler.stop()
        self.registry.stop()


class NotificationsModule(BaseModule):
    """Historique des alertes de tous les modules (ORION Control, section Notifications)."""

    name, title = "notifications", "Notifications"

    def __init__(self, alerts: AlertRouter):
        self._alerts = alerts

    def status(self) -> dict:
        return self._alerts.status()

    def admin(self, method: str, parts: list[str], query: dict, data: Any) -> tuple[int, Any]:
        if method == "GET" and not parts:
            return 200, self.status()
        if method == "GET" and parts == ["history"]:
            source = (query.get("source") or [None])[0]
            return 200, self._alerts.history(200, source)
        return 404, {"status": "error", "error": "not_found"}


# Classification des tâches périodiques des modules (CORE FOUNDATION F2), faite par la plateforme et non par les
# modules : (principal, capacité). Ce que vous avez programmé ou réglé part sous « delegated:… » et continue pendant
# la suspension ; une initiative de module sous « module:… ».
#   - news:bulletins, news:journal : bulletins et journal aux heures que vous avez réglées -> tâche programmée
#     (NOTIFY) ;
#   - news:collect : collecte des flux (lecture) ; ses alertes spontanées déclarent « module:news » ;
#   - watch:<id> : surveillance que vous avez créée ;
#   - evolve:scan : analyse des journaux, initiative d'EVOLVE (lecture).
JOB_CLASSES = {"news:bulletins": ("delegated:news:bulletins", Capability.NOTIFY.value),
               "news:journal": ("delegated:news:journal", Capability.NOTIFY.value),
               "news:collect": ("module:news", Capability.READ.value),
               "evolve:scan": ("module:evolve", Capability.READ.value),
               # Mode autonome que vous avez activé : il travaille dans le clone ; le déploiement est fait hors d'ORION.
               "evolve:auto": ("module:evolve", Capability.READ.value),
               "sentinel:probe": ("module:sentinel", Capability.READ.value),
               # Frag Factory : notifications de clips et rapport du matin, que vous avez activés vous-même.
               "fragfactory:sync": ("delegated:fragfactory:notifications", Capability.NOTIFY.value),
               "fragfactory:report": ("delegated:fragfactory:report", Capability.NOTIFY.value),
               "discord:poll": ("module:discord", Capability.READ.value),
               # Conservation des connaissances : règle que vous avez fixée (décision n° 2), pas une initiative.
               "knowledge:retention": ("delegated:knowledge:retention", Capability.WRITE_DATA.value),
               # Suivi de vos missions (états d'après les tâches, blocages annoncés) : il ne lance rien.
               "missions:refresh": ("delegated:missions:suivi", Capability.NOTIFY.value),
               # Agenda Google : lecture de votre calendrier « ORION » que vous avez relié (annonces de changements).
               "google_calendar:sync": ("delegated:google_calendar:sync", Capability.NOTIFY.value)}


def classify_job(job_id: str) -> tuple[str, str]:
    if job_id in JOB_CLASSES:
        return JOB_CLASSES[job_id]
    module, _, rest = job_id.partition(":")
    if module == "watch" and rest:
        return f"delegated:watch:{rest}", Capability.READ.value
    return f"module:{module}", Capability.NOTIFY.value  # inconnue : initiative, la plus surveillée


def _owner(cfg) -> str:
    from orion.factory import build_profiles

    return next((u.id for u in build_profiles(cfg).users() if u.role == "owner"), "")


def build_v3(cfg, *, events=None, llm=None, presence=None, notifications=None,
             foundation=None) -> V3Modules | None:
    """``foundation`` (CORE FOUNDATION F1, facultatif) : alertes, achats et EVOLVE soumis au moteur central ; le
    planificateur s'arrête net sur arrêt d'urgence."""
    wanted = [name for name in ("watch", "news", "evolve", "purchase", "sentinel", "fragfactory", "discord",
                                       "knowledge", "missions", "google_calendar")
              if getattr(cfg, name).enabled]
    if not wanted:
        return None
    m = cfg.modules
    store = Store(m.database, PUBLIC)  # lot F4 : base publique, aucune donnée personnelle
    permits = foundation.permissions if foundation is not None else None  # moteur central (CORE FOUNDATION)
    scheduler = Scheduler(halted=(lambda: permits.suspended_for("delegated:scheduler")) if permits else None,
                          classify=classify_job if permits else None,
                          blocked=permits.blocked if permits else None)
    history = History(store)
    owner = _owner(cfg)
    modules: V3Modules | None = None

    def is_home() -> bool | None:
        if presence is None or not owner:
            return None
        return presence.engine.is_home(owner)

    def speak(text: str) -> bool:
        if notifications is None:
            return False
        from orion.notifications import Notification, Priority

        used = notifications.notify(Notification("ORION", text[:500], "alerts", Priority.HIGH))
        return "voice" in used

    alerts = AlertRouter(store, AlertSettings(m.quiet_start, m.quiet_end, m.max_calls_per_day,
                                              m.min_call_interval_minutes, m.dedup_hours, m.critical_ignores_quiet),
                         presence=is_home, speak=speak, call=lambda text: modules.call(text),
                         write=lambda text: modules.write(text),
                         permit=permits.decide if permits else None,
                         hub=foundation.events if foundation is not None else None)
    structured = StructuredLLM(llm, available=lambda: not getattr(llm, "degraded", False)) if llm is not None \
        else None
    registry, tools = ModuleRegistry(), []
    contexts: dict = {}  # états lus par la compréhension sans LLM (« réactive le son » : Discord en sourdine ?)
    registry.register(NotificationsModule(alerts))  # historique commun des alertes (ORION Control)
    news = None
    if "news" in wanted:
        from orion.news import Feed, NewsEngine, NewsSettings
        from orion.news.tools import news_tools

        n = cfg.news
        feeds = []
        for name, spec in (n.feeds or {}).items():
            if not isinstance(spec, dict) or not isinstance(spec.get("url"), str) or not spec.get("category"):
                raise ValueError(f"[news] feeds.{name} : « url » et « category » attendues")
            feeds.append(Feed(name, spec["url"], spec["category"], spec.get("reliability", "media")))
        news = NewsEngine(store, feeds, alerts=alerts, scheduler=scheduler, history=history,
                          llm=structured if n.use_llm else None,
                          settings=NewsSettings(tuple(n.bulletin_times), tuple(n.categories), dict(n.thresholds), {},
                                                n.max_calls_per_day, n.min_call_interval_minutes,
                                                collect_minutes=n.collect_minutes, journal_time=n.journal_time,
                                                journal_enabled=n.journal_enabled))
        registry.register(news)
        tools += news_tools(news)
        if foundation is not None:
            # Bulletin que vous programmez à une heure précise (« fais-moi un bulletin à 18 h ») : tâche persistante.
            foundation.tasks.register("news.bulletin", lambda run, engine=news: {"bulletin": engine.bulletin(
                run.params.get("slot"), deliver=True, origin=run.principal)}, Capability.NOTIFY)
    if "watch" in wanted:
        from orion.watch import WatchEngine, WatchSettings
        from orion.watch.connectors import build_connectors
        from orion.watch.tools import watch_tools

        w = cfg.watch
        connectors = build_connectors(w.sources)
        if cfg.web.enabled and cfg.web.provider == "searxng":
            from orion.watch.web import WebSearchConnector
            from orion.web.searxng import SearXNGProvider

            # Source Web automatique (SearXNG local) : chaque surveillance d'articles ou d'événements y cherche.
            connectors.register(WebSearchConnector(SearXNGProvider(cfg.web.base_url, cfg.web.language, 10,
                                                                   cfg.web.timeout)))
        if news is not None:
            from orion.news import NewsConnector

            connectors.register(NewsConnector(news))  # « préviens-moi si l'on annonce… » à partir des actualités
        engine = WatchEngine(store, connectors, alerts=alerts, scheduler=scheduler, history=history,
                             events=events, llm=structured,
                             settings=WatchSettings(w.default_interval_minutes, w.min_interval_minutes,
                                                    w.requests_per_source_per_hour, w.failure_limit, w.opportunity))
        registry.register(engine)
        tools += watch_tools(engine)
    if "evolve" in wanted:
        from pathlib import Path

        import orion
        from orion.evolve import EvolveEngine, EvolveSettings, Workspace

        e = cfg.evolve
        code_root = Path(orion.__file__).resolve().parents[1]
        workspace = Workspace(Path(e.workspace), e.repository or str(code_root), [code_root])
        claude = None
        if e.claude_code:
            from orion.config import secret
            from orion.evolve.claude_code import TOKEN_NAME, ClaudeCodeAdvisor
            from orion.factory import ENV_FILE

            claude = ClaudeCodeAdvisor(Path(e.claude_binary), Path(e.claude_home),
                                       lambda: secret(TOKEN_NAME, ENV_FILE), model=e.claude_model,
                                       effort=e.claude_effort, timeout=e.claude_timeout)
        settings = EvolveSettings(e.level, e.scan_minutes, tuple(e.journal_units), e.remote,
                                  claude_per_day=e.claude_per_day, claude_auto=e.claude_auto)
        evolve = EvolveEngine(store, workspace, settings=settings, llm=structured, alerts=alerts, scheduler=scheduler,
                              history=history, gate=permits.gate("module:evolve") if permits else None, claude=claude)
        if e.claude_code:
            from orion.evolve.autopilot import AutoPilot, AutoSettings

            evolve.autopilot = AutoPilot(evolve, AutoSettings(e.autonomous, e.deploys_per_day, e.auto_cycle_minutes,
                                                              Path(e.workspace) / "deploy"))
        registry.register(evolve)
    if "purchase" in wanted:
        from orion.purchase import PurchaseEngine, PurchaseSettings, SandboxMerchant, SandboxPayment
        from orion.purchase.tools import purchase_tools

        p = cfg.purchase
        if not p.sandbox:
            raise ValueError("[purchase] sandbox = false : aucun prestataire de paiement réel n'existe dans cette "
                             "version")
        purchase = PurchaseEngine(store, SandboxPayment(), SandboxMerchant(), alerts=alerts, events=events,
                                  history=history, settings=PurchaseSettings(p.level, p.sandbox, p.allow_level3,
                                                                             p.per_purchase_max, p.period_max,
                                                                             p.period_days),
                                  gate=permits.gate("delegated:purchase") if permits else None)
        registry.register(purchase)
        tools += purchase_tools(purchase)
    if "sentinel" in wanted:
        if foundation is None:
            log.warning("SENTINEL : [platform] désactivé, pas de service de ressources : SENTINEL non démarré")
        else:
            from orion.sentinel import SentinelEngine, SentinelSettings
            from orion.sentinel.tools import sentinel_tools

            s = cfg.sentinel
            sentinel = SentinelEngine(store, foundation.resources, alerts=alerts, scheduler=scheduler, history=history,
                                      settings=SentinelSettings(s.probe_seconds, history_days=s.history_days,
                                                                notify_after_seconds=s.notify_after_seconds))
            registry.register(sentinel)
            tools += sentinel_tools(sentinel)
    if "fragfactory" in wanted:
        from orion.fragfactory import build_module
        from orion.fragfactory.tools import fragfactory_tools

        try:
            fragfactory = build_module(cfg, store, alerts=alerts, scheduler=scheduler, history=history)
        except ValueError as exc:  # agent V3 ou jeton absent : le reste d'ORION démarre quand même
            log.warning("Frag Factory : module non démarré (%s)", exc)
        else:
            registry.register(fragfactory)
            tools += fragfactory_tools(fragfactory)
    if "discord" in wanted:
        from pathlib import Path  # local : la branche EVOLVE en importe un aussi (nom local à toute la fonction)

        from orion.config import secret
        from orion.factory import ENV_FILE
        from orion.discord import AgentLink, DiscordEngine, DiscordSettings
        from orion.discord.tools import discord_tools

        d = cfg.discord
        token = secret(d.token_env, ENV_FILE) if d.agent_url else ""
        if not d.agent_url or len(token) < 32:
            log.warning("DISCORD : agent_url ou jeton %s absent : module non démarré", d.token_env)
        else:
            discord = DiscordEngine(AgentLink(d.agent_url, token, d.timeout), scheduler=scheduler,
                                    decide=permits.decide if permits else None,
                                    ledger=foundation.ledger if foundation is not None else None,
                                    settings=DiscordSettings(d.poll_seconds,
                                                             undeafen_keeps_mute=d.undeafen_keeps_mute,
                                                             default_salon=d.default_salon,
                                                             default_serveur=d.default_serveur),
                                    seen_path=Path(m.database).with_name("discord_seen.json"))
            registry.register(discord)
            tools += discord_tools(discord)
            contexts["discord.deafened"] = discord.deafened
    if "knowledge" in wanted:
        if foundation is None or foundation.personal is None:
            log.warning("KNOWLEDGE : [platform] personal désactivé, pas de base personnelle : module non démarré")
        else:
            from orion.knowledge import KnowledgeBase, KnowledgeModule, KnowledgeSettings

            def record(principal: str, action: str, reason: str) -> None:
                foundation.ledger.append(principal, Capability.WRITE_DATA.value, action, "allow", reason)

            knowledge = KnowledgeModule(KnowledgeBase(foundation.personal, record=record),
                                        decide=permits.decide, scheduler=scheduler,
                                        settings=KnowledgeSettings(cfg.knowledge.retention_hours))
            registry.register(knowledge)
    if "missions" in wanted:
        if foundation is None:
            log.warning("MISSIONS : sans CORE FOUNDATION (tâches F2) : module non démarré")
        else:
            from orion.missions import MissionsModule

            registry.register(MissionsModule(foundation.store, foundation.tasks, events=foundation.events,
                                             ledger=foundation.ledger, scheduler=scheduler,
                                             refresh_seconds=cfg.missions.refresh_seconds))
    calendar_provider = None
    if "google_calendar" in wanted:
        if foundation is None:
            log.warning("Agenda Google : sans CORE FOUNDATION (scellement F4) : module non démarré")
        else:
            from orion.config import secret
            from orion.factory import ENV_FILE
            from orion.google_calendar import GoogleCalendarModule, GoogleCalendarProvider, GoogleCalendarSettings

            g = cfg.google_calendar
            gcal = GoogleCalendarModule(
                foundation.store, foundation.tasks.sealer, lambda: secret(g.secret_env, ENV_FILE),
                events=foundation.events, scheduler=scheduler,
                settings=GoogleCalendarSettings(g.name, g.interval_seconds, g.past_days, g.future_days, g.timeout,
                                                g.max_bytes, g.failure_alert_minutes, 3 * g.interval_seconds))
            registry.register(gcal)
            calendar_provider = GoogleCalendarProvider(gcal)
    modules = V3Modules(store, scheduler, alerts, registry, tools)
    modules.calendar_provider = calendar_provider  # lu par les outils de l'agenda (orion.agenda.Calendar)
    modules.contexts = contexts
    modules.knowledge = registry.get("knowledge")  # interface tracée pour les modules (reader(principal))
    log.info("Modules V3 activés : %s (base %s)", ", ".join(wanted), m.database)
    return modules
