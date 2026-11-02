"""Remise du bulletin NEWS mis en page : dans le navigateur du PC s'il est allumé, sinon par mail ; et à l'allumage du
PC s'il y a un bulletin que vous n'avez pas encore vu.

- ``deliver`` (outil ``deliver_news_bulletin``, étape de la routine réveil) : le dernier bulletin (ou un aperçu frais
  s'il date de plus de 6 h) s'ouvre sur le PC ; si le PC ne répond pas, il part par mail à votre propre adresse.
- ``pc_started`` (micro du PC revenu après une vraie absence) : ouvre le dernier bulletin **envoyé** (8 h, 20 h) s'il
  date de moins de 18 h et que vous ne l'avez pas encore eu ; jamais deux fois le même.
- Ce qui a été remis est gardé dans un petit fichier d'état (identifiant du bulletin, canal, heure).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MAX_AGE_AT_STARTUP = 18 * 3600


def lan_address(peer: str | None = None) -> str:
    """Adresse de cette machine vue du réseau local (celle que le PC joint), sans rien envoyer."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect((peer or "192.168.1.1", 9))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class BulletinDelivery:
    def __init__(self, engine, url: Callable[[], str], open_on_pc: Callable[[str], bool],
                 send_mail: Callable[[str, str, str], bool] | None, state_path: Path | str,
                 clock: Callable[[], float] = time.time):
        self._engine, self._url, self._open, self._mail = engine, url, open_on_pc, send_mail
        self._path, self._clock = Path(state_path), clock
        self._lock = threading.Lock()

    # --- État -------------------------------------------------------------------------------------------------

    def state(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, bulletin: dict, channel: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seen_id": bulletin["id"], "channel": channel, "at": self._clock()}),
                       encoding="utf-8")
        tmp.replace(self._path)

    # --- Remise -----------------------------------------------------------------------------------------------

    def deliver(self) -> dict:
        """Navigateur du PC, sinon mail ; rend {"channel": "navigateur" | "mail" | None, "subjects": n}."""
        with self._lock:
            bulletin, html = self._engine.page()
            subjects = len(bulletin.get("entries") or [])
            if self._try_open():
                self._remember(bulletin, "navigateur")
                return {"channel": "navigateur", "subjects": subjects}
            if self._mail is not None:
                at = datetime.fromtimestamp(bulletin["at"])
                subject = f"ORION — Bulletin de {bulletin.get('slot') or at.strftime('%H:%M')} du {at:%d/%m}"
                try:
                    if self._mail(subject, bulletin.get("text") or "", html) is not False:
                        self._remember(bulletin, "mail")
                        return {"channel": "mail", "subjects": subjects}
                except Exception:  # noqa: BLE001 - serveur de mail en panne : dit, jamais caché
                    log.exception("Bulletin : envoi par mail impossible")
            return {"channel": None, "subjects": subjects}

    def pc_started(self) -> bool:
        """PC rallumé : ouvre le dernier bulletin envoyé si vous ne l'avez pas encore eu."""
        with self._lock:
            latest = self._engine.latest_bulletin(delivered_only=True)
            if latest is None or self._clock() - latest["at"] > MAX_AGE_AT_STARTUP:
                return False
            if self.state().get("seen_id") == latest["id"]:
                return False
            if not self._try_open():
                return False
            self._remember(latest, "navigateur (allumage du PC)")
            log.info("Bulletin %s ouvert à l'allumage du PC", latest["id"])
            return True

    def _try_open(self) -> bool:
        try:
            return bool(self._open(self._url()))
        except Exception:  # noqa: BLE001 - PC éteint ou agent injoignable : on passe au mail
            log.info("Bulletin : PC injoignable", exc_info=True)
            return False

    # --- Outil (routine réveil, voix) ---------------------------------------------------------------------------

    def tool(self):
        from orion.tools.base import Risk, Tool

        def say(result: dict) -> str:
            if result["channel"] == "navigateur":
                return "Le bulletin d'actualités est ouvert sur votre PC, monsieur."
            if result["channel"] == "mail":
                return "Votre PC est éteint : je vous ai envoyé le bulletin d'actualités par mail, monsieur."
            return "Je n'ai pas pu vous remettre le bulletin : ni le PC ni la messagerie ne répondent."

        # À vous seul (votre PC, votre adresse) : une notification, pas un message externe.
        return Tool("deliver_news_bulletin", "Ouvre le bulletin d'actualités mis en page sur le PC (ou l'envoie par "
                    "mail si le PC est éteint) : « montre-moi le bulletin », « envoie-moi les actualités ».", {},
                    {"channel": "canal", "subjects": "nombre de sujets"}, Risk.SAFE, self.deliver, say=say,
                    capability="NOTIFY")
