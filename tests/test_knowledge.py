"""KNOWLEDGE : modèle commun des connaissances (clés et données fictives uniquement).

Couvre : chiffrement (rien en clair sur disque), dédoublonnage et confiance, contradictions (vous l'emportez ; une
source automatique ouvre un conflit que vous arbitrez), négation, hypothèses (plafond, catégories sensibles
refusées, confirmation), relations entre entités, recherche contextuelle, expiration, oubli vérifiable, base
verrouillée, interface tracée des modules (suspension de l'autonomie), routes d'ORION Control, import idempotent de
``memory.json``, construction depuis la configuration."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.knowledge import KnowledgeBase, KnowledgeError, KnowledgeModule  # noqa: E402
from orion.knowledge.importers import import_memory_facts, plan_memory_facts  # noqa: E402
from orion.platform.personal import PersonalStore  # noqa: E402
from orion.platform.store import PERSONAL, Store  # noqa: E402
from orion.platform.vault import DataKeyring, Locked  # noqa: E402

KEY = b"\x11" * 32  # clé factice


class Clock:
    def __init__(self):
        self.t = 1_793_750_000.0

    def __call__(self):
        return self.t


def kb(tmp_path, clock=None, record=None):
    personal = PersonalStore(Store(tmp_path / "personal.db", PERSONAL), DataKeyring({"k1": KEY}, "k1"))
    return KnowledgeBase(personal, clock=clock or Clock(), record=record), personal


def test_everything_is_encrypted_on_disk(tmp_path):
    base, _ = kb(tmp_path)
    base.tell("habite", "Nantes", text="vous habitez à Nantes")
    base.tell("aime", "le café noir", kind="preference")
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    for secret in (b"Nantes", b"caf", b"habite", b"preference"):
        assert secret not in raw


def test_duplicate_assertions_merge_provenance_and_raise_confidence(tmp_path):
    base, _ = kb(tmp_path)
    first = base.tell("aime", "Le café", kind="preference", principal="import:memory.json", source="memory.json#1")
    again = base.tell("aime", "le cafe", kind="preference", principal="user", source="conversation")
    assert again.id == first.id and len(again.provenance) == 2
    assert again.confidence > 0.95  # 1 - (1 - 0.8)(1 - 0.95)
    assert base.stats()["assertions"] == 1
    assert [r.change for r in again.history] == ["créée", "confirmée"]


def test_your_new_value_replaces_the_old_one_and_keeps_history(tmp_path):
    clock = Clock()
    base, _ = kb(tmp_path, clock)
    old = base.tell("habite", "Nantes")
    clock.t += 86400
    new = base.tell("habite", "Lyon")
    assert base.get(old.id).status == "superseded" and new.status == "active" and new.replaces == old.id
    assert [a.value for a in base.about("self")] == ["Lyon"]
    assert any(r.after == "superseded" for r in base.get(old.id).history)
    assert base.conflicts() == []


def test_an_automatic_source_contradicting_you_opens_a_conflict_you_arbitrate(tmp_path):
    base, _ = kb(tmp_path)
    yours = base.tell("habite", "Nantes")
    guess = base.tell("habite", "Paris", principal="module:news", source="article 42")
    assert guess.kind == "hypothesis" and guess.status == "contested"  # un module ne fait que supposer
    assert base.get(yours.id).status == "contested"
    conflict = base.conflicts()[0]
    assert set(conflict.assertions) == {yours.id, guess.id}
    with pytest.raises(KnowledgeError):
        base.resolve(conflict.id, yours.id, by="module:news")  # seul vous arbitrez
    base.resolve(conflict.id, yours.id, by="user")
    assert base.get(yours.id).status == "active" and base.get(guess.id).status == "retracted"
    assert base.conflicts() == [] and base.conflicts(open_only=False)[0].resolution == yours.id


def test_negation_contradicts_the_same_value_but_other_values_coexist(tmp_path):
    base, _ = kb(tmp_path)
    base.tell("aime", "le jazz", kind="preference")
    base.tell("aime", "le rock", kind="preference")  # non fonctionnel : les deux coexistent
    assert len(base.about("self")) == 2
    base.tell("aime", "le jazz", kind="preference", negated=True)
    texts = {(a.value, a.negated, a.status) for a in base.about("self", include_inactive=True)}
    assert ("le jazz", False, "superseded") in texts and ("le jazz", True, "active") in texts


def test_hypotheses_are_capped_refused_on_sensitive_categories_and_confirmed_only_by_you(tmp_path):
    base, _ = kb(tmp_path)
    h = base.tell("s interesse a", "les cartes graphiques", principal="module:news", confidence=0.99)
    assert h.kind == "hypothesis" and h.confidence <= 0.75
    for value in ("le diabète", "la religion catholique", "le vote à gauche"):
        with pytest.raises(KnowledgeError):
            base.tell("a", value, principal="module:proactive")
    with pytest.raises(KnowledgeError):
        base.confirm(h.id, by="module:news")
    confirmed = base.confirm(h.id, by="user")
    assert confirmed.kind == "fact" and confirmed.confidence >= 0.95
    # ce que VOUS dites d'une catégorie sensible n'est pas une déduction : vous seul décidez de le confier
    assert base.tell("suit", "un traitement contre l'asthme", principal="user").kind == "fact"


def test_relations_between_entities(tmp_path):
    base, _ = kb(tmp_path)
    julie = base.entity("Julie", "person", aliases=["Ju"])
    base.tell("soeur", "", obj="Julie", text="Julie est votre sœur")
    base.tell("habite", "Rennes", subject="Ju", text="Julie habite à Rennes")
    assert base.lookup("ju") == julie.id and base.lookup("Inconnu") is None
    relations = base.related("Julie")
    assert [(a.predicate, e.name) for a, e in relations] == [("soeur", "vous")]
    assert [a.value for a in base.about("Julie")] == ["Rennes"]


def test_contextual_search_uses_words_entities_confidence_and_context(tmp_path):
    base, _ = kb(tmp_path)
    base.entity("Julie", "person")
    base.tell("habite", "Rennes", subject="Julie", text="Julie habite à Rennes")
    base.tell("aime", "les randonnées en Bretagne", kind="interest")
    base.tell("s interesse a", "la Bretagne", principal="module:news")
    found = base.search("Où habite Julie ?")
    assert found and found[0][1].value == "Rennes"
    bretagne = base.search("Bretagne")
    assert [a.kind for _, a in bretagne][0] == "interest"  # plus sûr que l'hypothèse
    assert base.search("voyage", context=["Rennes"])[0][1].value == "Rennes"
    assert base.search("") == []


def test_retention_expires_hypotheses_and_validity_ends(tmp_path):
    clock = Clock()
    base, personal = kb(tmp_path, clock)
    base.tell("s interesse a", "les échecs", principal="module:news")
    base.tell("habite", "Nantes")  # un fait n'expire pas
    base.tell("est en", "vacances", valid_until=clock.t + 7 * 86400)
    clock.t += 8 * 86400
    assert base.expire() == 1  # la validité des vacances est passée
    clock.t += 200 * 86400
    assert base.expire() == 1  # l'hypothèse a dépassé ses 180 jours
    assert [a.value for a in base.about("self")] == ["Nantes"]
    assert personal.count("knowledge.assertions") == 1


def test_forgetting_is_real_and_verifiable(tmp_path):
    base, personal = kb(tmp_path)
    a = base.tell("aime", "le thé", kind="preference")
    base.tell("habite", "Nantes")
    assert base.forget(a.id, by="user")
    with pytest.raises(KnowledgeError):
        base.get(a.id)
    assert personal.count("knowledge.assertions") == 1
    with pytest.raises(KnowledgeError):
        base.forget_everything(by="module:evolve")
    assert base.forget_everything(by="user") == 0 and personal.count() == 0


def test_a_locked_base_refuses_everything_and_writes_nothing(tmp_path):
    base, _ = kb(tmp_path)
    base.tell("habite", "Nantes")
    locked = KnowledgeBase(PersonalStore(Store(tmp_path / "personal.db", PERSONAL), None))
    assert locked.locked
    with pytest.raises(Locked):
        locked.tell("habite", "Lyon")
    module = KnowledgeModule(locked)
    assert module.status() == {"locked": True}
    assert module.admin("GET", ["search"], {"q": ["Nantes"]}, None)[0] == 423
    assert module.reader("module:news").context("Nantes", "journal") == []


def test_modules_read_through_a_traced_interface_and_cannot_write_while_suspended(tmp_path):
    from orion.platform.foundation import Foundation

    foundation = Foundation(Store())  # autonomie suspendue par défaut
    base, _ = kb(tmp_path)
    base.tell("aime", "les cartes graphiques", kind="interest")
    module = KnowledgeModule(base, decide=foundation.permissions.decide)
    reader = module.reader("module:news")
    out = reader.context("cartes graphiques", "personnaliser le journal")
    assert out and set(out[0]) == {"text", "kind", "confidence", "status"}  # sans provenance
    assert reader.propose("s interesse a", "la Formule 1") is None  # écriture d'initiative : refusée (suspension)
    entries = foundation.ledger.list(10)
    assert any(e["action"] == "knowledge:read" and e["decision"] == "allow" for e in entries)
    assert any(e["action"] == "knowledge:propose" and e["decision"] == "deny" for e in entries)
    assert "cartes" not in json.dumps(entries)  # jamais le contenu au registre
    with pytest.raises(ValueError):
        reader.context("x", "")  # une lecture déclare son but
    with pytest.raises(ValueError):
        module.reader("user")


def test_control_routes(tmp_path):
    from orion.platform.foundation import Foundation

    foundation = Foundation(Store())
    base, _ = kb(tmp_path)
    module = KnowledgeModule(base, decide=foundation.permissions.decide)
    status, made = module.admin("POST", ["assertions"], {}, {"predicate": "habite", "value": "Nantes"})
    assert status == 200 and made["sources"] == ["admin"]
    base.tell("habite", "Paris", principal="module:news")
    status, conflicts = module.admin("GET", ["conflicts"], {}, None)
    assert status == 200 and len(conflicts[0]["choices"]) == 2
    status, _ = module.admin("POST", ["conflicts", conflicts[0]["id"], "resolve"], {}, {"keep": made["id"]})
    assert status == 200 and module.admin("GET", ["conflicts"], {}, None)[1] == []
    assert module.admin("GET", ["search"], {"q": ["Nantes"]}, None)[1][0]["value"] == "Nantes"
    assert module.admin("GET", ["assertions", made["id"]], {}, None)[1]["history"]
    assert module.admin("DELETE", ["assertions", made["id"]], {}, None)[1] == {"forgotten": True}
    assert module.admin("POST", ["assertions"], {}, {"predicate": "", "value": "x"})[0] == 400
    export = module.admin("GET", ["export"], {}, None)[1]
    assert set(export) == {"entities", "assertions", "conflicts"}
    assert any(e["action"] == "knowledge:tell" for e in foundation.ledger.list(50))


def test_memory_json_import_is_idempotent_and_keeps_its_source(tmp_path):
    facts = [{"id": "a1", "text": "Ma sœur s'appelle Julie", "category": "fact", "user": "owner",
              "created": "2026-10-01T10:00:00"},
             {"id": "a2", "text": "Je préfère le thé", "category": "preference", "user": "owner"},
             {"id": "a3", "text": "Autre profil", "user": "invité"}, {"text": "sans identifiant"}]
    source = tmp_path / "memory.json"
    source.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    before = source.read_bytes()
    assert [p["kind"] for p in plan_memory_facts(facts)] == ["fact", "preference"]  # sans clé, sans écrire
    base, _ = kb(tmp_path)
    assert import_memory_facts(base, json.loads(source.read_text(encoding="utf-8"))) == \
        {"added": 2, "skipped": 0, "refused": 0}
    assert import_memory_facts(base, facts) == {"added": 0, "skipped": 2, "refused": 0}
    assert source.read_bytes() == before
    assert base.stats()["assertions"] == 2
    assert {p.source for a in base.about("self") for p in a.provenance} == {"memory.json#a1", "memory.json#a2"}


def test_build_needs_the_personal_base_and_retention_is_a_delegated_rule(tmp_path):
    from orion.config import load_config
    from orion.platform.build import build_v3, classify_job
    from orion.platform.foundation import Foundation

    assert classify_job("knowledge:retention") == ("delegated:knowledge:retention", "WRITE_DATA")
    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, knowledge=replace(cfg.knowledge, enabled=True),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    alone = build_v3(cfg, foundation=Foundation(Store()))
    try:
        assert alone.registry.get("knowledge") is None and alone.knowledge is None  # pas de base personnelle
    finally:
        alone.stop()
    foundation = Foundation(Store())
    personal = PersonalStore(Store(tmp_path / "personal.db", PERSONAL), DataKeyring({"k1": KEY}, "k1"))
    foundation.attach_personal(personal, DataKeyring({"k1": KEY}, "k1"))
    cfg = replace(cfg, modules=replace(cfg.modules, database=tmp_path / "v3b.db"))
    modules = build_v3(cfg, foundation=foundation)
    try:
        assert modules.knowledge is modules.registry.get("knowledge")
        assert modules.knowledge.status()["locked"] is False
    finally:
        modules.stop()
