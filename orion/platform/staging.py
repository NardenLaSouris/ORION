"""Zones d'attente des comptes séparés (CORE FOUNDATION, lot F5 ; roadmap.md § 2.6).

Les comptes ``orion-import`` (imports de données personnelles) et ``orion-evolve`` (propositions d'EVOLVE) n'écrivent
jamais dans les bases ni dans le dépôt en service, et ne lisent ni la clé ni la base personnelle. Ils déposent un
**lot** dans leur zone d'attente ; le Core le vérifie avant d'en faire quoi que ce soit :

- un fichier JSON par lot, déposé sous un nom provisoire puis renommé en ``*.json`` (jamais lu à moitié écrit) ;
- format : ``{"format": 1, "kind": "<type déclaré>", "source": "...", "created_at": <epoch>, "items": [...],
  "sha256": "<empreinte des items>"}`` ;
- refusé (mis en quarantaine avec sa raison, jamais supprimé) : trop gros, illisible, type non déclaré, empreinte
  fausse, élément hors schéma, lien symbolique, propriétaire inattendu ;
- accepté : remis au destinataire déclaré pour ce type (par exemple la base personnelle chiffrée), puis rangé dans
  ``done/``. Si le destinataire ne peut pas le prendre (base verrouillée), le lot reste en attente, intact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_BATCH = 5 * 1024 * 1024  # un lot plus gros est refusé : un import se découpe
MAX_ITEMS = 5000
FIELDS = {"format", "kind", "source", "created_at", "items", "sha256"}


class Rejected(ValueError):
    pass


class NotNow(RuntimeError):
    """Le destinataire ne peut pas prendre le lot maintenant (base verrouillée…) : le lot reste en attente."""


@dataclass(frozen=True)
class Kind:
    """Type de lot accepté : ``check(item)`` lève Rejected si un élément est hors schéma ; ``accept(lot)`` le reçoit."""
    name: str
    check: Callable[[Any], None]
    accept: Callable[[dict], None]


def digest(items: list) -> str:
    canonical = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def batch(kind: str, source: str, items: list, created_at: float | None = None) -> dict:
    """Lot prêt à déposer (côté compte séparé)."""
    return {"format": 1, "kind": kind, "source": source, "created_at": created_at or time.time(), "items": items,
            "sha256": digest(items)}


def deposit(zone: Path | str, lot: dict, name: str) -> Path:
    """Dépôt atomique (côté compte séparé) : écrit sous un nom provisoire, puis renomme."""
    zone = Path(zone)
    tmp = zone / f".{name}.part"
    tmp.write_text(json.dumps(lot, ensure_ascii=False), encoding="utf-8")
    final = zone / f"{name}.json"
    os.replace(tmp, final)
    return final


class StagingInbox:
    def __init__(self, zone: Path | str, kinds: list[Kind], *, owner_uid: int | None = None,
                 clock: Callable[[], float] = time.time):
        self.zone = Path(zone)
        self._kinds = {k.name: k for k in kinds}
        self._owner = owner_uid  # compte attendu pour les dépôts (None : non vérifié, tests et développement)
        self._clock = clock
        for sub in ("done", "quarantine"):
            (self.zone / sub).mkdir(parents=True, exist_ok=True)

    def scan(self) -> dict:
        """Traite les lots déposés : {"accepted": [...], "rejected": {nom: raison}, "waiting": {nom: raison}}."""
        report: dict = {"accepted": [], "rejected": {}, "waiting": {}}
        for path in sorted(self.zone.glob("*.json")):
            try:
                lot = self._read(path)
                kind = self._kinds[lot["kind"]]
                kind.accept(lot)
            except Rejected as exc:
                self._move(path, "quarantine", str(exc))
                report["rejected"][path.name] = str(exc)
                log.warning("Zone d'attente %s : lot %s refusé (%s)", self.zone.name, path.name, exc)
                continue
            except NotNow as exc:
                report["waiting"][path.name] = str(exc)
                continue
            self._move(path, "done", "")
            report["accepted"].append(path.name)
        return report

    def _read(self, path: Path) -> dict:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise Rejected("pas un fichier ordinaire (lien symbolique ?)")
        if self._owner is not None and info.st_uid != self._owner:
            raise Rejected("déposé par un autre compte que celui de la zone")
        if info.st_size > MAX_BATCH:
            raise Rejected(f"lot trop gros ({info.st_size} octets)")
        try:
            lot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise Rejected("illisible (JSON attendu)") from exc
        if not isinstance(lot, dict) or set(lot) != FIELDS or lot.get("format") != 1:
            raise Rejected("format inconnu")
        if lot["kind"] not in self._kinds:
            raise Rejected(f"type de lot non déclaré : {str(lot['kind'])[:40]}")
        items = lot["items"]
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise Rejected("éléments : liste de 5000 au plus attendue")
        if not isinstance(lot["sha256"], str) or digest(items) != lot["sha256"]:
            raise Rejected("empreinte fausse : lot modifié ou incomplet")
        for i, item in enumerate(items):
            try:
                self._kinds[lot["kind"]].check(item)
            except Rejected as exc:
                raise Rejected(f"élément {i + 1} : {exc}") from exc
        return lot

    def _move(self, path: Path, where: str, reason: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        target = self.zone / where / f"{stamp}-{path.name}"
        os.replace(path, target)
        if reason:
            target.with_suffix(".raison.txt").write_text(reason + "\n", encoding="utf-8")
