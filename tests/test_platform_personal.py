"""Lot F4 : base personnelle chiffrée, clé, rotation, sauvegarde et restauration, migration JSON réversible,
séparation des données (textes personnels scellés dans la base opérationnelle). Clés et données factices seulement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform.autonomy import VOICE  # noqa: E402
from orion.platform.datakey import main as datakey  # noqa: E402
from orion.platform.eventhub import Event, EventHub  # noqa: E402
from orion.platform.personal import PersonalStore, restore  # noqa: E402
from orion.platform.store import OPERATIONAL, PERSONAL, PUBLIC, Store, StoreError  # noqa: E402
from orion.platform.vault import (UNAVAILABLE, DataKeyring, Locked, Sealer, VaultError, dump_keyring,  # noqa: E402
                                  load_keyring, new_key, parse_keyring)

SECRET = "rendez-vous factice chez le dentiste Dupont"  # texte témoin : jamais lisible sur disque
K1, K2 = b"\x01" * 32, b"\x02" * 32


def ring(*ids: str) -> DataKeyring:
    keys = {"k1": K1, "k2": K2}
    return DataKeyring({k: keys[k] for k in ids}, ids[-1])


def disk(path: Path) -> bytes:
    return b"".join(Path(str(path) + s).read_bytes() for s in ("", "-wal") if Path(str(path) + s).exists())


def personal(path, keyring) -> PersonalStore:
    return PersonalStore(Store(path, PERSONAL), keyring)


# --- Chiffrement --------------------------------------------------------------------------------------------

def test_encryption_binds_ciphertext_to_its_place_and_hides_keys():
    keyring = ring("k1")
    blob = keyring.encrypt(SECRET.encode(), "personal:notes:a")
    assert SECRET.encode() not in blob and keyring.decrypt(blob, "personal:notes:a") == SECRET.encode()
    with pytest.raises(VaultError):  # recopié à une autre place : refusé
        keyring.decrypt(blob, "personal:notes:b")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(VaultError):
        keyring.decrypt(tampered, "personal:notes:a")
    assert K1.hex() not in repr(keyring) and "k1" in repr(keyring)
    assert keyring.encrypt(b"x", "a") != keyring.encrypt(b"x", "a")  # nonce aléatoire


def test_keyring_comes_from_systemd_credentials_only(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert load_keyring()[0] is None and "clé absente" in load_keyring()[1]
    (tmp_path / "orion.data_key").write_text(dump_keyring({"k1": K1}, "k1"), encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    keyring, reason = load_keyring()
    assert keyring is not None and reason == "" and keyring.current == "k1"
    (tmp_path / "orion.data_key").write_text("pas un trousseau", encoding="utf-8")
    assert load_keyring()[0] is None and "illisible" in load_keyring()[1]


# --- Base personnelle ---------------------------------------------------------------------------------------

def test_personal_records_are_unreadable_on_disk_and_without_key(tmp_path):
    db = tmp_path / "personal.db"
    store = personal(db, ring("k1"))
    store.put("notes", "n1", {"text": SECRET})
    assert store.get("notes", "n1") == {"text": SECRET}
    assert SECRET.encode() not in disk(db) and "dentiste".encode() not in disk(db)
    locked = personal(db, None)
    assert locked.locked and locked.status()["state"] == "verrouillée" and locked.count() == 1
    with pytest.raises(Locked):
        locked.get("notes", "n1")


def test_a_wrong_key_is_detected_at_opening(tmp_path):
    db = tmp_path / "personal.db"
    personal(db, ring("k1")).put("notes", "n1", {"text": SECRET})
    wrong = PersonalStore(Store(db, PERSONAL), DataKeyring({"k1": b"\x09" * 32}, "k1"))
    assert wrong.locked and "incorrecte" in wrong.reason
    missing = PersonalStore(Store(db, PERSONAL), DataKeyring({"k2": K2}, "k2"))
    assert missing.locked and "k1" in missing.reason


def test_rotation_rewraps_progressively_then_retires_the_old_key(tmp_path):
    db = tmp_path / "personal.db"
    old = personal(db, ring("k1"))
    for i in range(5):
        old.put("notes", f"n{i}", {"i": i, "text": SECRET})
    new = personal(db, ring("k1", "k2"))
    assert new.status()["to_rotate"] == 5
    with pytest.raises(VaultError):
        new.retire("k1")  # encore utilisée
    assert new.rotate(limit=2) == 2 and new.usage() == {"k1": 3, "k2": 2}
    assert new.rotate() == 3 and new.usage() == {"k2": 5}
    new.retire("k1")
    with pytest.raises(VaultError):
        new.retire("k2")  # la courante
    only_new = personal(db, ring("k2"))
    assert not only_new.locked and only_new.get("notes", "n4") == {"i": 4, "text": SECRET}
    assert personal(db, ring("k1")).locked  # l'ancienne seule n'ouvre plus rien


def test_backup_never_sits_next_to_the_key_and_restore_never_overwrites(tmp_path):
    db = tmp_path / "data" / "personal.db"
    store = personal(db, ring("k1"))
    store.put("notes", "n1", {"text": SECRET})
    keydir = tmp_path / "creds"
    keydir.mkdir()
    (keydir / "orion.data_key").write_text("x", encoding="utf-8")
    with pytest.raises(VaultError, match="contient une clé"):
        store.backup(keydir)
    nas = tmp_path / "nas" / "orion"  # un dossier quelconque : NAS monté, clé USB, disque externe
    manifest = store.backup(nas, name="sauvegarde")
    info = json.loads(manifest.read_text(encoding="utf-8"))
    assert info["records"] == 1 and info["keys"] == ["k1"] and SECRET.encode() not in (nas / "sauvegarde.db").read_bytes()
    assert not any("data_key" in p.name for p in nas.iterdir())
    target = tmp_path / "restored" / "personal.db"
    assert restore(manifest, target, ring("k1")) == {"records": 1, "kept": ""}
    assert personal(target, ring("k1")).get("notes", "n1") == {"text": SECRET}
    second = restore(manifest, target, ring("k1"))  # base déjà là : gardée à côté
    assert second["kept"] and (target.parent / second["kept"]).exists()
    with pytest.raises(VaultError):  # mauvaise clé : rien d'installé
        restore(manifest, tmp_path / "other.db", DataKeyring({"k1": b"\x07" * 32}, "k1"))
    assert not (tmp_path / "other.db").exists()
    (nas / "sauvegarde.db").write_bytes((nas / "sauvegarde.db").read_bytes()[:-10] + b"0" * 10)
    with pytest.raises(VaultError, match="altérée"):
        restore(manifest, tmp_path / "x.db", ring("k1"))


def test_json_migration_is_idempotent_and_reversible_byte_for_byte(tmp_path):
    from datetime import datetime

    from orion.memory import MemoryStore

    source = tmp_path / "memory.json"
    memory = MemoryStore(source, clock=lambda: datetime(2026, 11, 3, 9, 0))
    for text in ("mon code de casier factice est 4242", "le chat factice s'appelle Pixel", SECRET):
        memory.add(text)
    original = source.read_bytes()
    store = personal(tmp_path / "personal.db", ring("k1"))
    assert store.import_json("memory", source, lambda item, i: item["id"]) == 3
    first = store.export_items("memory")
    assert store.import_json("memory", source, lambda item, i: item["id"]) == 3  # deuxième passage
    assert store.count("memory") == 3 and store.export_items("memory") == first
    assert source.read_bytes() == original  # source jamais modifiée
    back = store.export_json("memory", tmp_path / "retour.json")
    assert back.read_bytes() == original  # retour arrière : identique octet pour octet


# --- Séparation des données ---------------------------------------------------------------------------------

def test_each_database_only_accepts_its_data_class(tmp_path):
    from orion.platform.ledger import ActionLedger

    with pytest.raises(StoreError, match="classe de données"):
        ActionLedger(Store(tmp_path / "public.db", PUBLIC))
    with pytest.raises(StoreError):
        PersonalStore(Store(tmp_path / "ops.db", OPERATIONAL), None)
    ActionLedger(Store(tmp_path / "ops2.db", OPERATIONAL))


def test_event_text_is_sealed_in_the_operational_database(tmp_path):
    db = tmp_path / "ops.db"
    hub = EventHub(Store(db, OPERATIONAL), sealer=Sealer(ring("k1"), configured=True))
    hub.publish(Event("reminder.due", "reminders", "user", SECRET, "corps factice : " + SECRET, dedup_key=SECRET))
    assert SECRET.encode() not in disk(db)
    assert hub.recent()[0]["title"] == SECRET
    out = hub.publish(Event("reminder.due", "reminders", "user", SECRET, dedup_key=SECRET))
    assert out.outcome == "duplicate"  # dédoublonnage par étiquette, sans texte


def test_without_key_event_text_is_never_written_and_honestly_unavailable_after_restart(tmp_path):
    db = tmp_path / "ops.db"
    hub = EventHub(Store(db, OPERATIONAL), sealer=Sealer(None, configured=True))
    hub.publish(Event("reminder.due", "reminders", "user", SECRET))
    assert SECRET.encode() not in disk(db)
    assert hub.recent()[0]["title"] == SECRET  # même processus : gardé en mémoire
    again = EventHub(Store(db, OPERATIONAL), sealer=Sealer(None, configured=True))
    view = again.recent()[0]
    assert view["title"] == UNAVAILABLE and view["available"] is False


def test_unconfigured_personal_base_keeps_f3_behaviour(tmp_path):
    hub = EventHub(Store(tmp_path / "ops.db", OPERATIONAL))
    hub.publish(Event("reminder.due", "reminders", "user", "texte"))
    assert hub.recent()[0]["title"] == "texte" and hub.sealer.mode == "clair"


def test_personal_tasks_are_sealed_and_wait_for_the_key(tmp_path):
    from orion.platform.foundation import Foundation

    db = tmp_path / "ops.db"
    ran = []
    foundation = Foundation(Store(db, OPERATIONAL))
    foundation.attach_personal(personal(tmp_path / "p.db", ring("k1")), ring("k1"))
    foundation.tasks.register("notes.envoyer", lambda run: ran.append(run.params["texte"]) or {"dit": SECRET},
                              "NOTIFY", personal=True)
    task = foundation.tasks.schedule_for_user("notes.envoyer", {"texte": SECRET}, by=VOICE)
    foundation.tasks.run_due()
    assert ran == [SECRET] and foundation.tasks.get(task.id).result == {"dit": SECRET}
    assert SECRET.encode() not in disk(db)
    foundation.permissions.confirmations.request("user", "spend", {"a": 1}, summary=SECRET)
    assert SECRET.encode() not in disk(db)
    assert foundation.permissions.confirmations.pending()[0]["summary"] == SECRET
    locked = Foundation(Store(tmp_path / "ops2.db", OPERATIONAL))
    locked.attach_personal(personal(tmp_path / "p2.db", None), None)
    locked.tasks.register("notes.envoyer", lambda run: None, "NOTIFY", personal=True)
    with pytest.raises(Exception, match="verrouillées"):
        locked.tasks.schedule_for_user("notes.envoyer", {"texte": SECRET}, by=VOICE)


def test_personal_status_route_never_shows_content_or_key(tmp_path):
    from orion.platform.foundation import Foundation

    foundation = Foundation(Store(tmp_path / "ops.db", OPERATIONAL))
    assert foundation.admin("GET", ["personal"], {}, None, "s")[1]["state"] == "non configurée"
    store = personal(tmp_path / "p.db", ring("k1"))
    store.put("notes", "n1", {"text": SECRET})
    foundation.attach_personal(store, ring("k1"))
    status, body = foundation.admin("GET", ["personal"], {}, None, "s")
    assert status == 200 and body["state"] == "ouverte" and body["records"] == 1
    assert SECRET not in json.dumps(body) and K1.hex() not in json.dumps(body)
    assert foundation.admin("POST", ["personal", "verify"], {}, None, "s")[1] == {"records": 1, "errors": []}


# --- Outil d'administration -----------------------------------------------------------------------------------

def test_datakey_tool_generates_rotates_backs_up_and_restores(tmp_path, capsys):
    keyfile = tmp_path / "creds" / "orion.data_key"
    keyfile.parent.mkdir()
    assert datakey(["generate", str(keyfile)]) == 0
    assert datakey(["generate", str(keyfile)]) == 1  # jamais écrasé
    out = capsys.readouterr().out
    keyring = parse_keyring(keyfile.read_text(encoding="utf-8"))
    assert "empreinte" in out and keyfile.read_text() not in out
    if os.name == "posix":
        assert keyfile.stat().st_mode & 0o777 == 0o600
    db = tmp_path / "data" / "personal.db"
    personal(db, keyring).put("notes", "n1", {"text": SECRET})
    assert datakey(["rotate", str(keyfile)]) == 0
    assert len(parse_keyring(keyfile.read_text(encoding="utf-8")).ids()) == 2
    assert datakey(["rewrap", str(keyfile), str(db)]) == 0
    assert datakey(["retire", str(keyfile), keyring.current, str(db)]) == 0
    assert len(parse_keyring(keyfile.read_text(encoding="utf-8")).ids()) == 1
    assert datakey(["verify", str(keyfile), str(db)]) == 0
    assert datakey(["backup", str(keyfile), str(db), str(keyfile.parent)]) == 1  # à côté de la clé : refusé
    assert datakey(["backup", str(keyfile), str(db), str(tmp_path / "nas")]) == 0
    manifest = next((tmp_path / "nas").glob("*.json"))
    assert datakey(["restore", str(keyfile), str(manifest), str(tmp_path / "back" / "personal.db")]) == 0
    assert SECRET not in capsys.readouterr().out


# --- Test complet : chiffrement, arrêt, redémarrage, récupération de la clé, déchiffrement ---------------------

CHILD = textwrap.dedent('''
    import json, sys
    sys.path.insert(0, {root!r})
    from orion.config import load_config
    from orion.platform.foundation import build_foundation
    from orion.platform.eventhub import Event
    cfg = load_config({config!r})
    foundation = build_foundation(cfg)
    action = sys.argv[1]
    personal = foundation.personal
    if action == "write":
        personal.put("notes", "n1", {{"text": {secret!r}}})
        foundation.events.publish(Event("reminder.due", "reminders", "user", {secret!r}))
    out = {{"state": personal.status()["state"], "reason": personal.status()["reason"],
            "event": foundation.events.recent()[0]["title"] if foundation.events.recent() else None}}
    if not personal.locked:
        out["note"] = personal.get("notes", "n1")
    print(json.dumps(out))
''')


def run_core(tmp_path, action: str, credentials: Path | None) -> dict:
    config = tmp_path / "config.toml"
    if not config.exists():
        config.write_text(textwrap.dedent(f'''
            [platform]
            enabled = true
            database = "{(tmp_path / 'data' / 'ops.db').as_posix()}"
            personal = true
            personal_database = "{(tmp_path / 'data' / 'personal.db').as_posix()}"
            data_key = "orion-v3.data_key"
        '''), encoding="utf-8")
    script = tmp_path / "core.py"
    script.write_text(CHILD.format(root=str(ROOT), config=str(config), secret=SECRET), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CREDENTIALS_DIRECTORY"}
    if credentials is not None:
        env["CREDENTIALS_DIRECTORY"] = str(credentials)
    done = subprocess.run([sys.executable, str(script), action], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_full_cycle_encrypt_stop_restart_recover_key_and_decrypt(tmp_path):
    # Clé factice transmise comme par systemd (ImportCredential=orion-v3.data_key).
    creds = tmp_path / "run-credentials"
    creds.mkdir()
    datakey(["generate", str(creds / "orion-v3.data_key")])
    paper = (creds / "orion-v3.data_key").read_text(encoding="utf-8")  # copie hors ligne (papier, gestionnaire)

    first = run_core(tmp_path, "write", creds)  # service en marche : écriture chiffrée
    assert first == {"state": "ouverte", "reason": "", "event": SECRET, "note": {"text": SECRET}}
    data = tmp_path / "data"
    assert not any(SECRET.encode() in f.read_bytes() for f in data.iterdir())  # arrêt : rien en clair sur disque

    assert run_core(tmp_path, "read", creds)["note"] == {"text": SECRET}  # redémarrage : déchiffré

    for f in creds.iterdir():  # perte de la clé sur la machine (disque remplacé, identifiant effacé)
        f.unlink()
    lost = run_core(tmp_path, "read", None)
    assert lost["state"] == "verrouillée" and lost["event"] == UNAVAILABLE and "note" not in lost

    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "orion-v3.data_key").write_text(dump_keyring({"k1": new_key()}, "k1"), encoding="utf-8")
    assert run_core(tmp_path, "read", wrong)["state"] == "verrouillée"  # autre clé : jamais de faux déchiffrement

    recovered = tmp_path / "recovered"  # récupération : la copie hors ligne est remise en place
    recovered.mkdir()
    (recovered / "orion-v3.data_key").write_text(paper, encoding="utf-8")
    back = run_core(tmp_path, "read", recovered)
    assert back == {"state": "ouverte", "reason": "", "event": SECRET, "note": {"text": SECRET}}
