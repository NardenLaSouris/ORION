"""Base de connaissances : assertions, entités, conflits, dans la base personnelle chiffrée (lot F4).

Règles (codées ici, vérifiées par ``tests/test_knowledge.py``) :
- **Dédoublonnage** : la même assertion (même sujet, prédicat, valeur et sens) dite une seconde fois n'est pas
  recopiée ; sa provenance s'ajoute et sa confiance monte (combinaison « au moins une source a raison »).
- **Contradictions** :
  - même place d'un prédicat à valeur unique (« habite »), ou même valeur avec le sens inverse (« aime » / « n'aime
    pas ») ;
  - **vous** l'emportez sur toute autre origine : votre nouvelle valeur remplace l'ancienne (``superseded``, gardée
    dans l'historique) ;
  - une source automatique (module, import) qui contredit ce que vous avez dit est enregistrée comme contestée et
    ouvre un **conflit** ; ORION ne tranche jamais seul. Vous arbitrez (``resolve``).
- **Hypothèses** : origine automatique, confiance plafonnée, jamais sur une catégorie sensible (refusées), devenues
  faits seulement par votre confirmation (``confirm``).
- **Expiration** : chaque nature a sa durée de conservation ; ``expire`` retire ce qui l'a dépassée (suppression
  réelle, comptée au registre sans contenu).
- **Verrouillage** : sans la clé de la base personnelle, toute opération lève ``Locked`` ; rien n'est écrit en clair
  ailleurs, ni dans les journaux.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable

from orion.knowledge.model import (BASE_CONFIDENCE, ENTITY_KINDS, FACT_CAP, FUNCTIONAL, HYPOTHESIS_CAP, KINDS,
                                   RETENTION_DAYS, SELF, Assertion, Conflict, Entity, Provenance, Revision,
                                   new_id, normalize_predicate, origin_of, plain, sensitive)
from orion.personality import normalize

log = logging.getLogger(__name__)

ASSERTIONS, ENTITIES, CONFLICTS = "knowledge.assertions", "knowledge.entities", "knowledge.conflicts"
LIVE = ("active", "contested")
MAX_TEXT = 400
STOP = {"le", "la", "les", "l", "un", "une", "des", "de", "du", "d", "et", "ou", "a", "au", "aux", "en", "sur",
        "pour", "par", "que", "qui", "quoi", "est", "sont", "je", "tu", "il", "elle", "vous", "nous", "mon", "ma",
        "mes", "ton", "ta", "tes", "son", "sa", "ses", "votre", "vos", "ce", "cet", "cette", "ces", "y", "ne", "pas"}


class KnowledgeError(ValueError):
    """Demande refusée (valeur invalide, catégorie sensible, identifiant inconnu) : message court en français."""


def words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if w not in STOP and len(w) > 1}


class KnowledgeBase:
    def __init__(self, personal, clock: Callable[[], float] = time.time,
                 record: Callable[[str, str, str], None] | None = None):
        """``personal`` : ``PersonalStore`` (F4). ``record(principal, action, raison)`` : registre d'actions (sans
        contenu), facultatif."""
        self._personal, self._clock, self._record = personal, clock, record
        self._lock = threading.RLock()
        self._cache: dict[str, dict] | None = None

    # --- Accès chiffré ------------------------------------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self._personal is None or self._personal.locked

    def _load(self) -> dict[str, dict]:
        """Tout le contenu déchiffré, gardé en mémoire (lot F4 : rien en clair sur disque)."""
        if self._cache is None:
            if self._personal is None:
                from orion.platform.vault import Locked

                raise Locked("base personnelle non configurée")
            self._cache = {
                ASSERTIONS: {i: Assertion.from_dict(d) for i, d in self._personal.items(ASSERTIONS)},
                ENTITIES: {i: Entity.from_dict(d) for i, d in self._personal.items(ENTITIES)},
                CONFLICTS: {i: Conflict.from_dict(d) for i, d in self._personal.items(CONFLICTS)},
            }
        return self._cache

    def _save(self, collection: str, item) -> None:
        self._personal.put(collection, item.id, item.as_dict())
        self._load()[collection][item.id] = item

    def _remove(self, collection: str, item_id: str) -> None:
        self._personal.delete(collection, item_id)
        self._load()[collection].pop(item_id, None)

    def _trace(self, principal: str, action: str, reason: str) -> None:
        if self._record is not None:
            try:
                self._record(principal, action, reason)
            except Exception:  # noqa: BLE001 - le registre manquant ne doit pas perdre la connaissance
                log.exception("Connaissances : registre d'actions indisponible")

    # --- Entités ---------------------------------------------------------------------------------------------

    def entity(self, name: str, kind: str = "thing", aliases: Iterable[str] = ()) -> Entity:
        """Entité de ce nom (ou d'un de ses alias), créée si besoin. « moi » / « vous » : l'entité ``self``."""
        key = plain(name)
        if key in ("", "moi", "vous", "je", "self", "utilisateur"):
            return self._self()
        if kind not in ENTITY_KINDS:
            raise KnowledgeError(f"type d'entité inconnu : {kind}")
        with self._lock:
            for e in self._load()[ENTITIES].values():
                if key in (plain(n) for n in e.names()):
                    extra = [a for a in aliases if plain(a) not in (plain(n) for n in e.names())]
                    if extra:
                        e = replace(e, aliases=[*e.aliases, *extra])
                        self._save(ENTITIES, e)
                    return e
            e = Entity(new_id("e"), name.strip()[:80], kind, [a.strip()[:80] for a in aliases], self._clock())
            self._save(ENTITIES, e)
            return e

    def _self(self) -> Entity:
        with self._lock:
            e = self._load()[ENTITIES].get(SELF)
            if e is None:
                e = Entity(SELF, "vous", "self", [], self._clock())
                self._save(ENTITIES, e)
            return e

    def lookup(self, ref: str) -> str | None:
        """Identifiant de l'entité désignée par un identifiant, un nom ou un alias ; None si inconnue (rien créé)."""
        if ref in (SELF, "") or plain(ref) in ("moi", "vous", "je", "utilisateur"):
            return SELF
        if ref in self._load()[ENTITIES]:
            return ref
        key = plain(ref)
        for e in self._load()[ENTITIES].values():
            if key in (plain(n) for n in e.names()):
                return e.id
        return None

    def entities(self) -> list[Entity]:
        return sorted(self._load()[ENTITIES].values(), key=lambda e: e.name.lower())

    # --- Assertions -----------------------------------------------------------------------------------------

    def tell(self, predicate: str, value: str = "", *, subject: str = SELF, kind: str = "fact", principal: str = "user",
             source: str = "", text: str = "", obj: str = "", negated: bool = False, confidence: float | None = None,
             valid_until: float | None = None, request_id: str = "") -> Assertion:
        """Ajoute (ou renforce) une assertion ; applique dédoublonnage, contradictions et règles des hypothèses.

        ``subject`` / ``obj`` : noms d'entités (créées si besoin) ou identifiants existants."""
        if kind not in KINDS:
            raise KnowledgeError(f"nature inconnue : {kind}")
        try:
            predicate = normalize_predicate(predicate)
        except ValueError as exc:
            raise KnowledgeError(str(exc)) from exc
        value, text = " ".join(str(value).split())[:MAX_TEXT], " ".join(str(text).split())[:MAX_TEXT]
        if not value and not obj:
            raise KnowledgeError("valeur vide")
        origin = origin_of(principal)
        if origin == "module" and kind != "hypothesis":
            kind = "hypothesis"  # une source automatique ne fait que supposer
        if kind == "hypothesis" and sensitive(predicate, value, text, obj):
            self._trace(principal, "knowledge.refuse", "hypothèse sur une catégorie sensible refusée")
            raise KnowledgeError("ORION ne déduit jamais de catégorie sensible (santé, religion, opinions…).")
        now = self._clock()
        with self._lock:
            sid = self._entity_id(subject)
            oid = self._entity_id(obj) if obj else ""
            prov = Provenance(principal, source[:120], now, request_id[:40])
            base = BASE_CONFIDENCE[origin] if confidence is None else max(0.0, min(1.0, float(confidence)))
            cap = HYPOTHESIS_CAP if kind == "hypothesis" else FACT_CAP
            new = Assertion(new_id("a"), kind, sid, predicate, value, oid, negated, text or value,
                            min(base, cap), "active", [prov], [Revision(now, "créée", None, value, source, principal)],
                            now, now, now, valid_until, self._expiry(kind, now))
            same = self._find(new.identity)
            if same is not None:
                return self._reinforce(same, prov, base, cap)
            rivals = self._rivals(new)
            if rivals and origin in ("user", "admin"):
                for old in rivals:  # vous l'emportez : l'ancienne valeur est remplacée, gardée dans l'historique
                    self._set_status(old, "superseded", "remplacée par votre nouvelle valeur", principal)
                    new.replaces = old.id
                self._close_conflicts(new.slot, new.id, "remplacée par l'utilisateur")
            elif rivals:
                conflict = self._open_conflict(new, rivals)
                new.status, new.conflict = "contested", conflict.id
            self._save(ASSERTIONS, new)
            self._trace(principal, "knowledge.tell", f"{kind} ({new.status})")
            return new

    def _entity_id(self, ref: str) -> str:
        if ref in self._load()[ENTITIES] or ref == SELF:
            return self._self().id if ref == SELF else ref
        return self.entity(ref).id

    def _find(self, identity) -> Assertion | None:
        for a in self._load()[ASSERTIONS].values():
            if a.status in LIVE and a.identity == identity:
                return a
        return None

    def _rivals(self, new: Assertion) -> list[Assertion]:
        out = []
        for a in self._load()[ASSERTIONS].values():
            if a.status not in LIVE or a.slot != new.slot:
                continue
            same_value = plain(a.object or a.value) == plain(new.object or new.value)
            if same_value and a.negated != new.negated:
                out.append(a)  # « aime le café » contre « n'aime pas le café »
            elif new.predicate in FUNCTIONAL and not same_value and not a.negated and not new.negated:
                out.append(a)  # « habite à Nantes » contre « habite à Lyon »
        return out

    def _reinforce(self, a: Assertion, prov: Provenance, base: float, cap: float) -> Assertion:
        before = a.confidence
        a.provenance.append(prov)
        a.confidence = round(min(cap, 1 - (1 - a.confidence) * (1 - base)), 4)
        a.updated_at = prov.at
        a.expires_at = self._expiry(a.kind, prov.at)  # renforcée : sa conservation repart
        a.history.append(Revision(prov.at, "confirmée", round(before, 4), a.confidence, prov.source, prov.principal))
        self._save(ASSERTIONS, a)
        self._trace(prov.principal, "knowledge.reinforce", a.kind)
        return a

    def _set_status(self, a: Assertion, status: str, why: str, by: str) -> None:
        a.history.append(Revision(self._clock(), "état", a.status, status, why, by))
        a.status, a.updated_at = status, self._clock()
        if status not in ("contested",):
            a.conflict = ""
        self._save(ASSERTIONS, a)

    def _expiry(self, kind: str, now: float) -> float | None:
        days = RETENTION_DAYS.get(kind)
        return None if days is None else now + days * 86400

    # --- Conflits -----------------------------------------------------------------------------------------------

    def _open_conflict(self, new: Assertion, rivals: list[Assertion]) -> Conflict:
        for c in self._load()[CONFLICTS].values():
            if c.resolved_at is None and (c.subject, c.predicate) == new.slot:
                c.assertions = list(dict.fromkeys([*c.assertions, new.id]))
                self._save(CONFLICTS, c)
                return c
        c = Conflict(new_id("c"), new.subject, new.predicate, [*(r.id for r in rivals), new.id], self._clock())
        for r in rivals:
            if r.status != "contested":
                self._set_status(r, "contested", "contredite par une autre source", "système")
            r.conflict = c.id
            self._save(ASSERTIONS, r)
        self._save(CONFLICTS, c)
        return c

    def _close_conflicts(self, slot, kept: str, why: str) -> None:
        for c in self._load()[CONFLICTS].values():
            if c.resolved_at is None and (c.subject, c.predicate) == slot:
                c.resolved_at, c.resolution, c.reason = self._clock(), kept, why
                self._save(CONFLICTS, c)

    def conflicts(self, open_only: bool = True) -> list[Conflict]:
        items = self._load()[CONFLICTS].values()
        return sorted((c for c in items if not open_only or c.resolved_at is None), key=lambda c: -c.created_at)

    def resolve(self, conflict_id: str, keep: str | None, by: str = "user") -> Conflict:
        """Votre arbitrage : ``keep`` est l'assertion juste (les autres sont retirées), ou None (toutes retirées)."""
        if by not in ("user", "admin"):
            raise KnowledgeError("seul l'utilisateur arbitre une contradiction")
        with self._lock:
            c = self._load()[CONFLICTS].get(conflict_id)
            if c is None or c.resolved_at is not None:
                raise KnowledgeError("conflit inconnu ou déjà arbitré")
            if keep is not None and keep not in c.assertions:
                raise KnowledgeError("cette assertion ne fait pas partie du conflit")
            for aid in c.assertions:
                a = self._load()[ASSERTIONS].get(aid)
                if a is None:
                    continue
                if aid == keep:
                    self._set_status(a, "active", "retenue par l'utilisateur", by)
                    self._confirm(a, by)
                else:
                    self._set_status(a, "retracted", "écartée par l'utilisateur", by)
            c.resolved_at, c.resolution, c.reason = self._clock(), keep or "aucune", "arbitré"
            self._save(CONFLICTS, c)
            self._trace(by, "knowledge.resolve", "contradiction arbitrée")
            return c

    # --- Vos décisions ---------------------------------------------------------------------------------------

    def confirm(self, assertion_id: str, by: str = "user") -> Assertion:
        """Vous confirmez : une hypothèse devient un fait (ou une préférence, un intérêt si elle en était un)."""
        if by not in ("user", "admin"):
            raise KnowledgeError("seul l'utilisateur confirme une connaissance")
        with self._lock:
            return self._confirm(self._get(assertion_id), by)

    def _confirm(self, a: Assertion, by: str) -> Assertion:
        now = self._clock()
        if a.kind == "hypothesis":
            a.history.append(Revision(now, "nature", "hypothesis", "fact", "confirmée par l'utilisateur", by))
            a.kind = "fact"
        before = a.confidence
        a.confidence = max(a.confidence, BASE_CONFIDENCE["user"])
        a.provenance.append(Provenance(by, "confirmation", now))
        a.expires_at = self._expiry(a.kind, now)
        a.history.append(Revision(now, "confiance", before, a.confidence, "confirmée", by))
        a.updated_at = now
        self._save(ASSERTIONS, a)
        self._trace(by, "knowledge.confirm", a.kind)
        return a

    def retract(self, assertion_id: str, by: str = "user", why: str = "retirée") -> Assertion:
        with self._lock:
            a = self._get(assertion_id)
            self._set_status(a, "retracted", why, by)
            self._trace(by, "knowledge.retract", a.kind)
            return a

    def forget(self, assertion_id: str, by: str = "user") -> bool:
        """Suppression réelle (« oublie ») : l'assertion et son historique disparaissent de la base."""
        if by not in ("user", "admin"):
            raise KnowledgeError("seul l'utilisateur fait oublier une connaissance")
        with self._lock:
            self._get(assertion_id)
            self._remove(ASSERTIONS, assertion_id)
            self._trace(by, "knowledge.forget", "assertion supprimée")
            return True

    def forget_everything(self, by: str = "user") -> int:
        """Tout effacer ; vérifiable : rend le nombre d'enregistrements restants (0)."""
        if by not in ("user", "admin"):
            raise KnowledgeError("seul l'utilisateur fait tout oublier")
        with self._lock:
            total = 0
            for collection in (ASSERTIONS, ENTITIES, CONFLICTS):
                for item_id in list(self._load()[collection]):
                    self._remove(collection, item_id)
                    total += 1
            self._trace(by, "knowledge.forget_all", f"{total} enregistrement(s) supprimé(s)")
            return sum(self._personal.count(c) for c in (ASSERTIONS, ENTITIES, CONFLICTS))

    def _get(self, assertion_id: str) -> Assertion:
        a = self._load()[ASSERTIONS].get(assertion_id)
        if a is None:
            raise KnowledgeError("connaissance inconnue")
        return a

    # --- Conservation --------------------------------------------------------------------------------------------

    def expire(self, by: str = "delegated:knowledge:retention") -> int:
        """Retire définitivement ce qui a dépassé sa durée de conservation ou sa validité."""
        now, gone = self._clock(), 0
        with self._lock:
            for a in list(self._load()[ASSERTIONS].values()):
                ended = (a.expires_at is not None and a.expires_at <= now) or \
                        (a.valid_until is not None and a.valid_until <= now)
                old_inactive = a.status in ("superseded", "retracted", "expired") and now - a.updated_at > 180 * 86400
                if ended or old_inactive:
                    self._remove(ASSERTIONS, a.id)
                    gone += 1
            if gone:
                self._trace(by, "knowledge.expire", f"{gone} connaissance(s) expirée(s) supprimée(s)")
        return gone

    # --- Lecture ---------------------------------------------------------------------------------------------

    def known_source(self, source: str) -> bool:
        """Une assertion porte-t-elle déjà cette source (import déjà fait : idempotence) ?"""
        return any(p.source == source for a in self._load()[ASSERTIONS].values() for p in a.provenance)

    def get(self, assertion_id: str) -> Assertion:
        return self._get(assertion_id)

    def about(self, subject: str = SELF, *, kinds: Iterable[str] = KINDS, include_inactive: bool = False
              ) -> list[Assertion]:
        sid = self.lookup(subject)
        if sid is None:
            return []
        return sorted((a for a in self._load()[ASSERTIONS].values()
                       if a.subject == sid and a.kind in tuple(kinds) and (include_inactive or a.status in LIVE)),
                      key=lambda a: (-a.confidence, -a.updated_at))

    def related(self, entity: str) -> list[tuple[Assertion, Entity]]:
        """Relations d'une entité vers d'autres (dans les deux sens), actives seulement."""
        eid = self.lookup(entity)
        if eid is None:
            return []
        out, ents = [], self._load()[ENTITIES]
        for a in self._load()[ASSERTIONS].values():
            if a.status not in LIVE or not a.object:
                continue
            if a.subject == eid and a.object in ents:
                out.append((a, ents[a.object]))
            elif a.object == eid and a.subject in ents:
                out.append((a, ents[a.subject]))
        return out

    def search(self, query: str, *, kinds: Iterable[str] = KINDS, limit: int = 10, min_confidence: float = 0.0,
               context: Iterable[str] = ()) -> list[tuple[float, Assertion]]:
        """Recherche contextuelle : mots de la demande (et du contexte) contre le texte, la valeur, le prédicat et les
        noms d'entités ; pondérée par la confiance et la fraîcheur. Les connaissances contestées sont signalées par
        leur état, jamais présentées comme sûres."""
        wanted = words(query) | {w for c in context for w in words(c)}
        if not wanted:
            return []
        now, ents = self._clock(), self._load()[ENTITIES]
        scored = []
        for a in self._load()[ASSERTIONS].values():
            if a.status not in LIVE or a.kind not in tuple(kinds) or a.confidence < min_confidence:
                continue
            names = " ".join(" ".join(ents[e].names()) for e in (a.subject, a.object) if e in ents)
            have = words(f"{a.text} {a.value} {a.predicate} {names}")
            common = sum(1 for w in wanted if w in have or (len(w) > 4 and any(w[:5] == h[:5] for h in have)))
            if not common:
                continue
            age_days = max(0.0, (now - a.updated_at) / 86400)
            freshness = 1.0 / (1.0 + age_days / 365)
            score = common / len(wanted) * (0.6 + 0.4 * a.confidence) * (0.8 + 0.2 * freshness)
            if a.status == "contested":
                score *= 0.7
            scored.append((round(score, 4), a))
        scored.sort(key=lambda item: (-item[0], -item[1].confidence))
        return scored[:limit]

    def stats(self) -> dict[str, Any]:
        data = self._load()
        by_kind = {k: 0 for k in KINDS}
        by_status = {s: 0 for s in ("active", "contested", "superseded", "retracted", "expired")}
        for a in data[ASSERTIONS].values():
            by_kind[a.kind] += 1
            by_status[a.status] += 1
        return {"assertions": len(data[ASSERTIONS]), "entities": len(data[ENTITIES]),
                "open_conflicts": sum(1 for c in data[CONFLICTS].values() if c.resolved_at is None),
                "by_kind": by_kind, "by_status": by_status}

    def export(self) -> dict:
        """Tout votre contenu, lisible (droit d'accès) : entités, assertions avec provenance et historique, conflits."""
        data = self._load()
        return {"entities": [e.as_dict() for e in data[ENTITIES].values()],
                "assertions": [a.as_dict() for a in data[ASSERTIONS].values()],
                "conflicts": [c.as_dict() for c in data[CONFLICTS].values()]}
