"""Moteur central des permissions (CORE FOUNDATION F1) : qui peut faire quoi, et quand, pour toute action sensible.

Un **principal** désigne l'origine d'une action :
- ``user`` : la conversation ; ``admin`` : ORION Control. Jamais concernés par l'interrupteur d'autonomie ;
- ``delegated:<type>[:<id>]`` : une tâche que vous avez programmée (routine, surveillance, mandat d'achat) ;
- ``module:<nom>`` : une initiative d'un module (l'autonomie proprement dite).

Une **capacité** dit ce que l'action fait au monde (``Capability``). Décision : ``allow``, ``ask`` (confirmation de
votre part) ou ``deny``, toujours avec sa raison, toujours inscrite au registre d'actions.

Invariants (codés ici, vérifiés par les tests) :
- état d'autonomie indéterminé, principal inconnu, registre ou moteur en panne : refus (échec sûr) ;
- arrêt d'urgence : tout refus pour ``delegated:*`` et ``module:*`` ; suspension : seule la lecture reste aux modules ;
- ``SPEND``, ``DESTRUCTIVE``, ``EXTERNAL_MESSAGE`` et ``CODE_CHANGE`` : jamais permis à une origine autonome sans
  autorisation explicite, bornée (échéance et plafond) et valide ; sinon ``ask`` ;
- ``CODE_CHANGE`` : EVOLVE seul, dans son espace isolé ;
- une autorisation (``Grant``) n'est créée que depuis ORION Control : aucun module ne s'accorde une capacité ; les
  modules ne reçoivent qu'un ``Gate`` à principal fixe, qui ne sait que demander.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from orion.platform.autonomy import CONTROL, VOICE, AutonomyState, AutonomySwitch
from orion.platform.ledger import ActionLedger
from orion.platform.store import Migration, Store, dumps, loads

log = logging.getLogger(__name__)

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE permission_grants (
            id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            capability TEXT NOT NULL,
            limits TEXT NOT NULL DEFAULT '{}',
            expires_at REAL,
            granted_by TEXT NOT NULL,
            granted_at REAL NOT NULL,
            revoked_at REAL,
            revoked_by TEXT NOT NULL DEFAULT ''
        )
    """),
)


class Capability(str, Enum):
    READ = "READ"
    NOTIFY = "NOTIFY"
    CALL = "CALL"
    CONTROL_DEVICE = "CONTROL_DEVICE"
    WRITE_DATA = "WRITE_DATA"
    EXTERNAL_MESSAGE = "EXTERNAL_MESSAGE"
    SPEND = "SPEND"
    DESTRUCTIVE = "DESTRUCTIVE"
    CODE_CHANGE = "CODE_CHANGE"


SENSITIVE = frozenset({Capability.EXTERNAL_MESSAGE, Capability.SPEND, Capability.DESTRUCTIVE,
                       Capability.CODE_CHANGE})
# Phase 1 de l'autonomie (décision du 2 novembre 2026) : lecture, analyse, notification.
MODULE_DEFAULT = frozenset({Capability.READ, Capability.NOTIFY})
# Ce que vous avez programmé garde ses droits ordinaires ; le sensible reste soumis à confirmation ou autorisation.
DELEGATED_DEFAULT = frozenset({Capability.READ, Capability.NOTIFY, Capability.CALL, Capability.CONTROL_DEVICE,
                               Capability.WRITE_DATA})
TRUSTED = frozenset({"user", "admin"})
PRINCIPAL = re.compile(r"^(user|admin|module:[a-z][a-z0-9_]{0,30}|delegated:[a-z][a-z0-9_]{0,30}(:[\w.@<>\-]{1,120})?)$")
GRANT_PRINCIPAL = re.compile(r"^(module:[a-z][a-z0-9_]{0,30}|delegated:[a-z][a-z0-9_]{0,30}(:[\w.@<>\-]{1,120}|:\*)?)$")
CODE_CHANGE_PRINCIPALS = frozenset({"module:evolve"})


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str
    entry: int | None = None  # ligne du registre
    grant: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class PermissionDenied(PermissionError):
    def __init__(self, verdict: Verdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class Grant:
    id: str
    principal: str
    capability: Capability
    limits: dict = field(default_factory=dict)  # max_amount, per_day, devices
    expires_at: float | None = None
    granted_by: str = CONTROL
    granted_at: float = 0.0
    revoked_at: float | None = None

    def covers(self, principal: str) -> bool:
        if self.principal.endswith(":*"):
            return principal.startswith(self.principal[:-1])
        return principal == self.principal

    def as_dict(self) -> dict:
        return {"id": self.id, "principal": self.principal, "capability": self.capability.value,
                "limits": self.limits, "expires_at": self.expires_at, "granted_by": self.granted_by,
                "granted_at": self.granted_at, "revoked_at": self.revoked_at}


def kind_of(principal: str) -> str:
    return principal if principal in TRUSTED else principal.split(":", 1)[0]


class GrantStore:
    """Autorisations explicites. Création : ORION Control seul ; révocation : ORION Control ou la voix."""

    LIMIT_KEYS = frozenset({"max_amount", "per_day", "devices"})

    def __init__(self, store: Store, ledger: ActionLedger, clock: Callable[[], float] = time.time):
        self._store, self._ledger, self._clock = store, ledger, clock
        store.migrate("grants", MIGRATIONS)

    def grant(self, principal: str, capability: Capability | str, limits: dict | None = None,
              expires_at: float | None = None, *, granted_by: str) -> Grant:
        capability = Capability(capability)
        limits = dict(limits or {})
        if granted_by != CONTROL:
            self._ledger.append(granted_by, capability.value, "grant", "deny", "autorisation hors d'ORION Control")
            raise PermissionError("une autorisation ne s'accorde que depuis ORION Control")
        if not GRANT_PRINCIPAL.match(principal or ""):
            raise ValueError(f"principal invalide pour une autorisation : {principal!r}")
        if set(limits) - self.LIMIT_KEYS:
            raise ValueError(f"limites inconnues : {sorted(set(limits) - self.LIMIT_KEYS)}")
        now = self._clock()
        if expires_at is not None and expires_at <= now:
            raise ValueError("échéance déjà passée")
        if capability in SENSITIVE and (expires_at is None or not ({"max_amount", "per_day"} & set(limits))):
            raise ValueError(f"{capability.value} : autorisation bornée exigée (échéance et plafond)")
        if capability is Capability.CODE_CHANGE and principal not in CODE_CHANGE_PRINCIPALS:
            raise ValueError("CODE_CHANGE : EVOLVE seul")
        grant = Grant(uuid.uuid4().hex[:12], principal, capability, limits, expires_at, granted_by, now)
        self._store.write("INSERT INTO permission_grants (id, principal, capability, limits, expires_at, granted_by, "
                          "granted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (grant.id, principal, capability.value, dumps(limits), expires_at, granted_by, now))
        self._ledger.append(granted_by, capability.value, f"grant:{grant.id}", "allow", f"accordée à {principal}",
                            detail={"limites": sorted(limits), "échéance": expires_at})
        return grant

    def revoke(self, grant_id: str, *, by: str) -> bool:
        if by not in (CONTROL, VOICE):
            raise PermissionError("révocation : ORION Control ou la voix")
        # Mise à jour d'une autorisation (pas du registre) : la révocation est elle-même inscrite au registre.
        changed = self._store.write("UPDATE permission_grants SET revoked_at = ?, revoked_by = ? WHERE id = ? "
                                    "AND revoked_at IS NULL", (self._clock(), by, grant_id))
        if changed:
            self._ledger.append(by, "GRANT", f"revoke:{grant_id}", "allow", "autorisation révoquée")
        return bool(changed)

    def list(self, include_revoked: bool = False) -> list[Grant]:
        sql = "SELECT * FROM permission_grants" + ("" if include_revoked else " WHERE revoked_at IS NULL")
        return [self._row(r) for r in self._store.query(sql + " ORDER BY granted_at")]

    def active_for(self, principal: str, capability: Capability, now: float) -> list[Grant]:
        rows = self._store.query("SELECT * FROM permission_grants WHERE capability = ? AND revoked_at IS NULL "
                                 "AND (expires_at IS NULL OR expires_at > ?)", (capability.value, now))
        return [g for g in map(self._row, rows) if g.covers(principal)]

    @staticmethod
    def _row(row: dict) -> Grant:
        return Grant(row["id"], row["principal"], Capability(row["capability"]), loads(row["limits"], {}),
                     row["expires_at"], row["granted_by"], row["granted_at"], row["revoked_at"])


class PermissionEngine:
    def __init__(self, store: Store, *, ledger: ActionLedger | None = None, autonomy: AutonomySwitch | None = None,
                 clock: Callable[[], float] = time.time):
        self.ledger = ledger or ActionLedger(store, clock)
        self.autonomy = autonomy or AutonomySwitch(store, self.ledger, clock)
        self.grants = GrantStore(store, self.ledger, clock)
        self._clock = clock
        self._gates: dict[str, Gate] = {}
        self._lock = threading.Lock()

    def decide(self, principal: str, capability: Capability | str, context: dict[str, Any] | None = None, *,
               action: str = "", request_id: str = "", confirmed: bool = False) -> Verdict:
        """Décision, inscrite au registre. Toute erreur vaut refus pour une origine autonome."""
        context = dict(context or {})
        try:
            capability = Capability(capability)
            decision, reason, grant = self._decide(principal, capability, context, confirmed, action)
        except Exception as exc:  # noqa: BLE001 - moteur en panne : échec sûr
            log.exception("Permissions : erreur de décision")
            decision, reason, grant = Decision.DENY, f"moteur de permissions en erreur ({type(exc).__name__})", ""
            capability = capability if isinstance(capability, Capability) else str(capability)
        cap = capability.value if isinstance(capability, Capability) else str(capability)
        try:
            entry = self.ledger.append(principal, cap, action or cap.lower(), decision.value, reason, request_id,
                                       {"confirmé": bool(confirmed), "autorisation": grant,
                                        **{k: v for k, v in context.items() if k in ("amount", "device", "sandbox")}})
        except Exception:  # noqa: BLE001 - une action autonome non inscrite n'a pas lieu
            log.exception("Permissions : registre d'actions indisponible")
            if principal in TRUSTED:
                return Verdict(decision, reason, None, grant)
            return Verdict(Decision.DENY, "registre d'actions indisponible")
        return Verdict(decision, reason, entry, grant)

    def _decide(self, principal: str, capability: Capability, context: dict, confirmed: bool,
                action: str = "") -> tuple[Decision, str, str]:
        if not isinstance(principal, str) or not PRINCIPAL.match(principal):
            return Decision.DENY, "origine inconnue", ""
        if principal in TRUSTED:
            return Decision.ALLOW, "conversation" if principal == "user" else "ORION Control", ""
        state = self.autonomy.state()
        if state is None:
            return Decision.DENY, "état de l'autonomie indéterminé", ""
        if state is AutonomyState.EMERGENCY_STOP:
            return Decision.DENY, "arrêt d'urgence", ""
        kind = kind_of(principal)
        if kind == "module" and state is AutonomyState.SUSPENDED and capability is not Capability.READ:
            return Decision.DENY, "autonomie suspendue", ""
        if capability is Capability.CODE_CHANGE and (principal not in CODE_CHANGE_PRINCIPALS
                                                     or context.get("sandbox") is not True):
            return Decision.DENY, "modification du code hors de l'espace isolé d'EVOLVE", ""
        now = self._clock()
        for grant in self.grants.active_for(principal, capability, now):
            if why := self._outside(grant, principal, capability, context, now, action):
                log.info("Permissions : autorisation %s non applicable (%s)", grant.id, why)
                continue
            return Decision.ALLOW, f"autorisation {grant.id}", grant.id
        if capability in SENSITIVE:
            if confirmed:
                return Decision.ALLOW, "confirmé par l'utilisateur", ""
            return Decision.ASK, "action sensible : confirmation ou autorisation bornée requise", ""
        if kind == "delegated" and capability in DELEGATED_DEFAULT:
            return Decision.ALLOW, "tâche programmée par l'utilisateur", ""
        if kind == "module" and capability in MODULE_DEFAULT:
            return Decision.ALLOW, "autonomie phase 1", ""
        return Decision.DENY, "capacité non accordée", ""

    def _outside(self, grant: Grant, principal: str, capability: Capability, context: dict, now: float,
                 action: str = "") -> str:
        limits = grant.limits
        if "max_amount" in limits:
            amount = context.get("amount")
            if not isinstance(amount, (int, float)) or amount > float(limits["max_amount"]):
                return "montant absent ou au-delà du plafond"
        if "per_day" in limits and self.ledger.count_since(principal, capability.value, now - 86400,
                                                           exclude_action=action) >= int(limits["per_day"]):
            return "plafond quotidien atteint"
        if "devices" in limits and context.get("device") not in limits["devices"]:
            return "appareil non couvert"
        return ""

    def gate(self, principal: str) -> Gate:
        """Accès d'un module ou d'une tâche : principal fixé une fois pour toutes, rien d'autre que demander."""
        if principal in TRUSTED or not PRINCIPAL.match(principal or ""):
            raise ValueError(f"principal de module invalide : {principal!r}")
        with self._lock:
            return self._gates.setdefault(principal, Gate(self, principal))

    def suspended_for(self, principal: str) -> bool:
        """Vrai si ce principal ne peut plus rien faire maintenant (arrêt d'urgence, état indéterminé, ou initiative
        de module pendant la suspension) : le planificateur s'en sert avant chaque tâche."""
        state = self.autonomy.state()
        if state is None or state is AutonomyState.EMERGENCY_STOP:
            return principal not in TRUSTED
        return state is AutonomyState.SUSPENDED and kind_of(principal) == "module"


class Gate:
    """Ce qu'un module reçoit : demander une décision pour son propre principal. Pas d'accès aux autorisations."""

    __slots__ = ("_engine", "principal")

    def __init__(self, engine: PermissionEngine, principal: str):
        self._engine, self.principal = engine, principal

    def check(self, capability: Capability | str, context: dict | None = None, *, action: str = "",
              confirmed: bool = False, task: str = "") -> Verdict:
        """``task`` : identifiant de la tâche programmée (``delegated:<type>:<task>``) quand l'action en découle."""
        principal = self.principal
        if task:
            if not self.principal.startswith("delegated:") or self.principal.count(":") != 1:
                raise ValueError("une tâche se rattache à un principal « delegated:<type> »")
            principal = f"{self.principal}:{task}"
        from orion import trace

        return self._engine.decide(principal, capability, context, action=action, confirmed=confirmed,
                                   request_id=trace.current() or "")

    def require(self, capability: Capability | str, context: dict | None = None, **kwargs) -> Verdict:
        verdict = self.check(capability, context, **kwargs)
        if not verdict.allowed:
            raise PermissionDenied(verdict)
        return verdict
