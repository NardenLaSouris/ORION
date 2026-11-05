"""Service de ressources et mode dégradé (CORE FOUNDATION, lot F5 ; docs/v3/foundation.md § « Lot F5 »).

Une seule réponse à « telle ressource est-elle disponible maintenant ? » : LLM principal (Katana), transcription sur
GPU, agents des PC, recherche Web, NAS facultatif ; et la charge du mini-PC.

- Chaque ressource a une sonde (rapide, bornée dans le temps) ; la réponse est gardée ``ttl`` secondes, pour ne pas
  interroger le réseau à chaque question. Une sonde qui lève ou dépasse son délai vaut « indisponible ».
- Chaque passage disponible -> indisponible (et retour) est noté, avec sa date : ORION Control montre depuis quand.
- Chaque module déclare ce qu'il fait sans chaque ressource (``Degradation``) : ORION Control l'affiche, et un test
  « ressource absente » par module vérifie que c'est vrai.
- ``busy()`` : mini-PC chargé ou conversation en cours ; les tâches lourdes (``TaskService``, ``heavy=True``)
  attendent alors, sans échouer.
- Aucune ressource n'est indispensable au Core : sans elles, il démarre et converse (Whisper local, LLM de secours ou
  règles, outils locaux).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

LLM, STT, AGENT_PC, AGENT_LAPTOP, WEB, NAS = ("llm.primary", "stt.gpu", "agent.pc", "agent.portable", "web.search",
                                              "storage.nas")
BACKUP = "backup.core"
LABELS = {LLM: "LLM principal (Katana)", STT: "Transcription sur GPU (Katana)", AGENT_PC: "Agent du PC fixe",
          AGENT_LAPTOP: "Agent du PC portable", WEB: "Recherche Web (SearXNG)", NAS: "NAS (facultatif)",
          BACKUP: "Sauvegarde de la nuit (Core)"}
PROBE_TIMEOUT = 3.0
LOAD_LIMIT = 0.85  # charge moyenne sur 1 min, rapportée au nombre de cœurs
MEMORY_FLOOR = 0.15  # part de mémoire disponible sous laquelle le mini-PC est « chargé »


@dataclass(frozen=True)
class Degradation:
    """Ce que fait un module quand une ressource manque (``resource`` -> comportement, en clair)."""
    module: str
    resource: str
    behaviour: str


@dataclass
class _Resource:
    name: str
    label: str
    probe: Callable[[], tuple[bool, str]]
    ttl: float
    available: bool | None = None
    detail: str = "jamais vérifiée"
    checked_at: float = 0.0
    since: float = 0.0


class ResourceService:
    def __init__(self, clock: Callable[[], float] = time.time, in_conversation: Callable[[], bool] = lambda: False,
                 load: Callable[[], dict] | None = None, timeout: float = PROBE_TIMEOUT):
        self._clock, self.in_conversation, self._timeout = clock, in_conversation, timeout
        self._load = load or system_load
        self._resources: dict[str, _Resource] = {}
        self._degradations: list[Degradation] = []
        self.changes: deque[dict] = deque(maxlen=50)
        self._lock = threading.Lock()

    # --- Déclaration ----------------------------------------------------------------------------------------

    def register(self, name: str, probe: Callable[[], tuple[bool, str] | bool], *, label: str = "",
                 ttl: float = 30.0) -> None:
        def wrapped():
            result = probe()
            return result if isinstance(result, tuple) else (bool(result), "")
        self._resources[name] = _Resource(name, label or LABELS.get(name, name), wrapped, max(0.0, ttl))

    def declare(self, module: str, behaviours: dict[str, str]) -> None:
        for resource, behaviour in behaviours.items():
            self._degradations.append(Degradation(module, resource, behaviour))

    # --- Questions ------------------------------------------------------------------------------------------

    def available(self, name: str) -> bool:
        """Faux pour une ressource inconnue, non configurée, en panne ou dont la sonde ne répond pas à temps."""
        resource = self._resources.get(name)
        if resource is None:
            return False
        now = self._clock()
        if resource.available is not None and now - resource.checked_at < resource.ttl:
            return resource.available
        ok, detail = self._probe(resource)
        with self._lock:
            if ok != resource.available:
                if resource.available is not None:
                    self.changes.append({"at": now, "resource": name, "available": ok, "detail": detail})
                    log.log(logging.INFO if ok else logging.WARNING, "Ressource %s : %s (%s)", resource.label,
                            "disponible" if ok else "indisponible", detail or "—")
                resource.since = now
            resource.available, resource.detail, resource.checked_at = ok, detail, now
        return ok

    def _probe(self, resource: _Resource) -> tuple[bool, str]:
        """La sonde tourne dans un fil borné : un partage réseau figé ne bloque jamais l'appelant."""
        box: dict = {}

        def run():
            try:
                box["result"] = resource.probe()
            except Exception as exc:  # noqa: BLE001 - toute panne de sonde : indisponible
                box["result"] = (False, f"{type(exc).__name__}: {exc}"[:160])

        thread = threading.Thread(target=run, name=f"sonde-{resource.name}", daemon=True)
        thread.start()
        thread.join(self._timeout)
        if thread.is_alive():
            return False, f"pas de réponse en {self._timeout:g} s"
        ok, detail = box.get("result", (False, "sonde muette"))
        return bool(ok), str(detail)[:160]

    def load(self) -> dict:
        try:
            return self._load()
        except Exception:  # noqa: BLE001 - mesure impossible : pas chargé (on ne bloque pas pour rien)
            log.exception("Ressources : charge illisible")
            return {}

    def busy(self) -> str:
        """Raison de différer une tâche lourde (mini-PC chargé, conversation en cours), ou ""."""
        try:
            if self.in_conversation():
                return "conversation en cours"
        except Exception:  # noqa: BLE001
            log.exception("Ressources : état de la conversation illisible")
        load = self.load()
        if load.get("cpu", 0.0) > LOAD_LIMIT:
            return f"processeur chargé ({load['cpu']:.0%})"
        if "memory_available" in load and load["memory_available"] < MEMORY_FLOOR:
            return f"mémoire presque pleine ({load['memory_available']:.0%} disponible)"
        return ""

    def status(self, refresh: bool = True) -> dict:
        """Vue d'ORION Control : chaque ressource, depuis quand, et ce que font les modules sans elle."""
        rows = []
        for name, resource in self._resources.items():
            if refresh:
                self.available(name)
            rows.append({"name": name, "label": resource.label, "available": resource.available,
                         "detail": resource.detail, "since": resource.since or None,
                         "checked_at": resource.checked_at or None,
                         "without": [{"module": d.module, "behaviour": d.behaviour} for d in self._degradations
                                     if d.resource == name]})
        return {"resources": rows, "load": self.load(), "busy": self.busy(), "changes": list(self.changes)[-20:]}


# --- Sondes ---------------------------------------------------------------------------------------------------

def system_load() -> dict:
    """Charge du mini-PC : moyenne sur 1 min rapportée aux cœurs ; part de mémoire disponible (Linux)."""
    out: dict = {}
    if hasattr(os, "getloadavg"):
        out["cpu"] = round(os.getloadavg()[0] / max(1, os.cpu_count() or 1), 3)
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        total = int(info["MemTotal"].split()[0])
        available = int(info["MemAvailable"].split()[0])
        out["memory_available"] = round(available / max(1, total), 3)
    except (OSError, KeyError, ValueError):
        pass
    return out


def directory_probe(path: str | Path) -> Callable[[], tuple[bool, str]]:
    """NAS monté (SMB, NFS, peu importe le modèle) : le dossier existe et accepte une écriture. Vide : non configuré."""
    def probe() -> tuple[bool, str]:
        if not str(path):
            return False, "non configuré"
        folder = Path(path)
        if not folder.is_dir():
            return False, "dossier absent (NAS non monté ?)"
        test = folder / f".orion-sonde-{os.getpid()}"
        try:
            test.write_bytes(b"ok")
            test.unlink()
        except OSError as exc:
            return False, f"lecture seule ou refusé ({exc.strerror or exc})"
        return True, str(folder)
    return probe


def backup_probe(path: str | Path, max_hours: float = 26.0,
                 clock: Callable[[], float] = time.time) -> Callable[[], tuple[bool, str]]:
    """État écrit par ``scripts/v3_backup.py periodic`` : absent, non configuré (minuterie pas installée, aucune
    alerte) ; en échec, illisible ou plus vieux que ``max_hours`` : indisponible (SENTINEL prévient par écrit)."""
    import json

    def probe() -> tuple[bool, str]:
        target = Path(path)
        if not target.is_file():
            return False, "non configuré"
        try:
            state = json.loads(target.read_text(encoding="utf-8"))
            at = float(state["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return False, "état de la sauvegarde illisible"
        hours = (clock() - at) / 3600
        when = time.strftime("%d/%m %H:%M", time.localtime(at))
        if not state.get("ok"):
            problems = "; ".join(str(p) for p in state.get("problems") or []) or "raison inconnue"
            return False, f"échec le {when} : {problems}"[:160]
        if hours > max_hours:
            return False, f"dernière sauvegarde le {when}, il y a {hours:.0f} h"
        return True, f"vérifiée le {when}"
    return probe


def tcp_probe(url: str, timeout: float = 1.0) -> Callable[[], tuple[bool, str]]:
    from orion.llm.failover import tcp_reachable

    def probe() -> tuple[bool, str]:
        if not url:
            return False, "non configuré"
        return (True, url) if tcp_reachable(url, timeout) else (False, f"injoignable ({url})")
    return probe
