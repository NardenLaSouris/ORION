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
from orion.platform.history import History
from orion.platform.intelligence import StructuredLLM
from orion.platform.modules import ModuleRegistry
from orion.platform.scheduler import Scheduler
from orion.platform.store import Store

log = logging.getLogger(__name__)


class V3Modules:
    def __init__(self, store: Store, scheduler: Scheduler, alerts: AlertRouter, registry: ModuleRegistry,
                 tools: list):
        self.store, self.scheduler, self.alerts, self.registry, self.tools = store, scheduler, alerts, registry, tools
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


def _owner(cfg) -> str:
    from orion.factory import build_profiles

    return next((u.id for u in build_profiles(cfg).users() if u.role == "owner"), "")


def build_v3(cfg, *, events=None, llm=None, presence=None, notifications=None) -> V3Modules | None:
    wanted = [name for name in ("watch", "news", "evolve", "purchase") if getattr(cfg, name).enabled]
    if not wanted:
        return None
    m = cfg.modules
    store = Store(m.database)
    scheduler = Scheduler()
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
                         write=lambda text: modules.write(text))
    structured = StructuredLLM(llm, available=lambda: not getattr(llm, "degraded", False)) if llm is not None \
        else None
    registry, tools = ModuleRegistry(), []
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
                                                collect_minutes=n.collect_minutes))
        registry.register(news)
        tools += news_tools(news)
    if "watch" in wanted:
        from orion.watch import WatchEngine, WatchSettings
        from orion.watch.connectors import build_connectors
        from orion.watch.tools import watch_tools

        w = cfg.watch
        connectors = build_connectors(w.sources)
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
        registry.register(EvolveEngine(store, workspace, settings=EvolveSettings(e.level, e.scan_minutes,
                                                                                 tuple(e.journal_units), e.remote),
                                       llm=structured, alerts=alerts, scheduler=scheduler, history=history))
    modules = V3Modules(store, scheduler, alerts, registry, tools)
    log.info("Modules V3 activés : %s (base %s)", ", ".join(wanted), m.database)
    return modules
