"""Confirmations liées à une demande précise (CORE FOUNDATION F2).

Une demande de confirmation porte un identifiant, le principal qui agira, l'action, l'empreinte de ses paramètres
(SHA-256 du contenu canonique), une échéance, et ne sert qu'une fois :

1. ``request`` : le moteur ou le service de tâches demande votre accord (rien n'est encore permis) ;
2. ``approve`` : vous l'accordez, à la voix ou depuis ORION Control (jamais un module : ils n'ont pas accès à ce
   magasin, seulement à un ``Gate``) ;
3. ``consume`` : au moment d'agir, le moteur la consomme ; elle doit correspondre exactement (même principal, même
   action, mêmes paramètres), être accordée, non expirée, non révoquée et jamais utilisée. La consommation est une
   seule mise à jour conditionnelle : deux exécutions concurrentes ne peuvent pas l'utiliser toutes les deux.

Chaque étape est inscrite au registre d'actions (sans valeur de paramètre : seulement l'empreinte).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from orion.platform.autonomy import CONTROL, VOICE
from orion.platform.ledger import ActionLedger
from orion.platform.store import Migration, Store, dumps

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE confirmations (
            id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            action TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            approved_at REAL,
            approved_by TEXT NOT NULL DEFAULT '',
            used_at REAL,
            revoked_at REAL
        )
    """),
)
APPROVERS = frozenset({"user", VOICE, CONTROL})
DEFAULT_TTL = 15 * 60.0
MAX_TTL = 7 * 86400.0


def params_hash(params: Any) -> str:
    return hashlib.sha256(dumps(params if params is not None else {}).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Confirmation:
    id: str
    principal: str
    action: str
    params_hash: str
    summary: str
    created_at: float
    expires_at: float
    approved_at: float | None = None
    approved_by: str = ""
    used_at: float | None = None
    revoked_at: float | None = None

    def state(self, now: float) -> str:
        if self.used_at is not None:
            return "utilisée"
        if self.revoked_at is not None:
            return "refusée"
        if self.expires_at <= now:
            return "expirée"
        return "accordée" if self.approved_at is not None else "en attente"

    def as_dict(self, now: float) -> dict:
        return {"id": self.id, "principal": self.principal, "action": self.action, "summary": self.summary,
                "created_at": self.created_at, "expires_at": self.expires_at, "approved_by": self.approved_by,
                "state": self.state(now)}


class ConfirmationStore:
    def __init__(self, store: Store, ledger: ActionLedger, clock: Callable[[], float] = time.time):
        self._store, self._ledger, self._clock = store, ledger, clock
        store.migrate("confirmations", MIGRATIONS)

    def request(self, principal: str, action: str, params: Any = None, *, ttl: float = DEFAULT_TTL,
                summary: str = "") -> Confirmation:
        now = self._clock()
        ttl = max(1.0, min(float(ttl), MAX_TTL))
        confirmation = Confirmation(secrets.token_hex(8), str(principal)[:160], str(action)[:80], params_hash(params),
                                    str(summary)[:200], now, now + ttl)
        self._store.write("INSERT INTO confirmations (id, principal, action, params_hash, summary, created_at, "
                          "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (confirmation.id, confirmation.principal, confirmation.action, confirmation.params_hash,
                           confirmation.summary, now, confirmation.expires_at))
        self._ledger.append(principal, "CONFIRMATION", f"confirm.request:{confirmation.id}", "ask",
                            f"confirmation demandée pour {action}"[:200], detail={"échéance": confirmation.expires_at})
        return confirmation

    def approve(self, confirmation_id: str, *, by: str) -> bool:
        """Votre accord (voix, conversation ou ORION Control) ; sans effet sur une demande expirée ou déjà traitée."""
        if by not in APPROVERS:
            self._ledger.append(by, "CONFIRMATION", f"confirm.approve:{confirmation_id}", "deny",
                                "accord hors de la conversation ou d'ORION Control")
            raise PermissionError("une confirmation ne s'accorde qu'à la voix ou depuis ORION Control")
        now = self._clock()
        changed = self._store.write("UPDATE confirmations SET approved_at = ?, approved_by = ? WHERE id = ? AND "
                                    "approved_at IS NULL AND used_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
                                    (now, by, confirmation_id, now))
        self._ledger.append(by, "CONFIRMATION", f"confirm.approve:{confirmation_id}", "allow" if changed else "deny",
                            "confirmation accordée" if changed else "demande inconnue, expirée ou déjà traitée")
        return bool(changed)

    def refuse(self, confirmation_id: str, *, by: str) -> bool:
        if by not in APPROVERS:
            raise PermissionError("refus : voix ou ORION Control")
        changed = self._store.write("UPDATE confirmations SET revoked_at = ? WHERE id = ? AND used_at IS NULL AND "
                                    "revoked_at IS NULL", (self._clock(), confirmation_id))
        if changed:
            self._ledger.append(by, "CONFIRMATION", f"confirm.refuse:{confirmation_id}", "allow", "confirmation refusée")
        return bool(changed)

    def consume(self, confirmation_id: str, principal: str, action: str, params: Any = None) -> bool:
        """Usage unique, au moment d'agir : vrai seulement si tout correspond et que personne ne l'a déjà utilisée."""
        if not confirmation_id:
            return False
        now = self._clock()
        changed = self._store.write("UPDATE confirmations SET used_at = ? WHERE id = ? AND principal = ? AND "
                                    "action = ? AND params_hash = ? AND approved_at IS NOT NULL AND used_at IS NULL "
                                    "AND revoked_at IS NULL AND expires_at > ?",
                                    (now, confirmation_id, principal, action, params_hash(params), now))
        if not changed:
            self._ledger.append(principal, "CONFIRMATION", f"confirm.use:{confirmation_id}", "deny",
                                "confirmation absente, différente, expirée ou déjà utilisée")
        return bool(changed)

    def get(self, confirmation_id: str) -> Confirmation | None:
        row = self._store.one("SELECT * FROM confirmations WHERE id = ?", (confirmation_id,))
        return Confirmation(**row) if row else None

    def pending(self) -> list[dict]:
        now = self._clock()
        rows = self._store.query("SELECT * FROM confirmations WHERE used_at IS NULL AND revoked_at IS NULL AND "
                                 "expires_at > ? ORDER BY created_at", (now,))
        return [Confirmation(**row).as_dict(now) for row in rows]
