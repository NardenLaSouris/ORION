"""Sauvegarde des données V3 : copie cohérente d'une base en WAL, manifeste vérifié, altération détectée, rotation,
restauration refusée service en marche ou sauvegarde abîmée, anciens fichiers mis de côté, production refusée ;
script de déploiement : garde-fous présents, syntaxe valide."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import v3_backup  # noqa: E402


def make_data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    db = sqlite3.connect(data / "orion_v3.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (v TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [(f"ligne {i}",) for i in range(500)])
    db.commit()  # données encore dans le WAL : la copie doit les contenir
    (data / "activity.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (data / "ignore.bin").write_bytes(b"x")
    return data, db


def test_backup_is_consistent_verified_and_rotated(tmp_path):
    data, db = make_data(tmp_path)
    out = tmp_path / "out"
    first = v3_backup.backup(data, out, keep=2)
    assert v3_backup.verify(first) == []
    copy = sqlite3.connect(first / "orion_v3.db")
    assert copy.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    assert {p.name for p in first.iterdir()} == {"orion_v3.db", "activity.jsonl", "manifest.json"}
    import time

    for _ in range(2):
        time.sleep(1.1)
        last = v3_backup.backup(data, out, keep=2)
    assert len([p for p in out.iterdir() if p.is_dir()]) == 2 and not first.exists()
    (last / "activity.jsonl").write_text("modifié", encoding="utf-8")
    assert v3_backup.verify(last) == ["activity.jsonl : résumé différent"]
    db.close()


def test_restore_refuses_a_running_service_or_a_damaged_backup_and_keeps_current_files(tmp_path):
    data, db = make_data(tmp_path)
    saved = v3_backup.backup(data, tmp_path / "out", keep=5)
    db.execute("DELETE FROM t")
    db.commit()
    db.close()
    with pytest.raises(SystemExit):
        v3_backup.restore(saved, data, active=lambda: True)
    aside = v3_backup.restore(saved, data, active=lambda: False)
    restored = sqlite3.connect(data / "orion_v3.db")
    assert restored.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    assert (aside / "orion_v3.db").exists()  # l'état d'avant est gardé, jamais effacé
    restored.close()
    (saved / "orion_v3.db").write_bytes(b"abime")
    with pytest.raises(SystemExit):
        v3_backup.restore(saved, data, active=lambda: False)


@pytest.mark.parametrize("path", ["/opt/orion", "/opt/orion/data", "/opt/jarvis/data"])
def test_production_paths_are_refused(path):
    with pytest.raises(SystemExit):
        v3_backup._guard(Path(path))  # noqa: SLF001
    assert v3_backup._guard(Path("/opt/orion-v3/data")) == Path("/opt/orion-v3/data").resolve()  # noqa: SLF001


def test_deploy_script_targets_v3_only():
    script = (ROOT / "deploy" / "v3_deploy.sh").read_text(encoding="utf-8")
    assert "TARGET=/opt/orion-v3" in script and "SERVICE=orion-v3" in script
    assert "--ff-only" in script and "v3_backup.py\" backup" in script
    assert "systemctl restart orion " not in script and "restart orion\n" not in script
    for line in script.splitlines():
        if "systemctl" in line and ("restart" in line or "stop" in line):
            assert '"$SERVICE"' in line  # aucune commande d'arrêt ou de redémarrage ne vise un autre service
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    assert not any("| grep -q" in line for line in code)  # sous pipefail, un grep -q qui trouve fait échouer le tube (SIGPIPE)
    bash = shutil.which("bash")
    if bash:
        assert subprocess.run([bash, "-n", str(ROOT / "deploy" / "v3_deploy.sh")]).returncode == 0


def test_secrets_in_the_data_folder_are_never_backed_up(tmp_path):
    """Jeton Spotify (en 600 au compte orion-v3, illisible par le compte du déploiement) : exclu et noté, jamais
    copié ; la sauvegarde ne s'arrête plus sur lui (déploiement du 4 novembre 2026 bloqué)."""
    data, db = make_data(tmp_path)
    (data / "spotify_token.json").write_text('{"access_token": "jeton-prive-42"}', encoding="utf-8")
    saved = v3_backup.backup(data, tmp_path / "out", keep=2)
    assert not (saved / "spotify_token.json").exists() and v3_backup.verify(saved) == []
    manifest = (saved / "manifest.json").read_text(encoding="utf-8")
    assert '"excluded"' in manifest and "spotify_token.json" in manifest and "jeton-prive-42" not in manifest
    db.close()

def test_the_nightly_backup_is_verified_kept_apart_promoted_weekly_and_reported(tmp_path):
    """Sauvegarde de la nuit : quotidienne vérifiée, à part des sauvegardes d'avant déploiement (aucune rotation ne
    touche l'autre) ; hebdomadaire seulement si la dernière a 7 jours ; état écrit pour SENTINEL, sans s'inclure."""
    import json
    import time

    data, db = make_data(tmp_path)
    out, status = tmp_path / "out", data / v3_backup.STATUS_NAME
    before_deploy = v3_backup.backup(data, out, keep=14)
    first = v3_backup.periodic(data, out, status)
    assert first["ok"] and first["problems"] == [] and first["files"] == 2
    assert first["folder"].startswith("quotidiennes/") and first["weekly"].startswith("hebdomadaires/")
    assert json.loads(status.read_text(encoding="utf-8"))["ok"] is True
    time.sleep(1.1)
    second = v3_backup.periodic(data, out, status)
    assert second["weekly"] == first["weekly"]  # moins de 7 jours : pas de nouvelle hebdomadaire
    assert not (out / second["folder"] / v3_backup.STATUS_NAME).exists()
    time.sleep(1.1)
    later = v3_backup.periodic(data, out, status, keep=2, weekly_keep=1, clock=lambda: time.time() + 8 * 86400)
    assert later["weekly"] != first["weekly"] and len(list((out / "hebdomadaires").iterdir())) == 1
    assert len(list((out / "quotidiennes").iterdir())) == 2
    assert before_deploy.exists() and v3_backup.verify(out / later["weekly"]) == []
    db.close()


def test_a_failed_nightly_backup_is_reported_never_raised(tmp_path):
    status = tmp_path / v3_backup.STATUS_NAME
    result = v3_backup.periodic(tmp_path / "absent", tmp_path / "out", status)
    assert result["ok"] is False and "absent" in result["problems"][0]
    assert '"ok": false' in status.read_text(encoding="utf-8")
    assert v3_backup.main(["periodic", "--data", str(tmp_path / "absent"), "--out", str(tmp_path / "out"),
                           "--status", str(status)]) == 1
    refused = v3_backup.periodic(tmp_path, Path("/opt/orion/sauvegardes"), None)
    assert refused["ok"] is False and "production" in refused["problems"][0]


def test_the_nightly_timer_runs_as_user_without_sudo_and_keeps_backups():
    service = (ROOT / "deploy" / "orion-v3-backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "orion-v3-backup.timer").read_text(encoding="utf-8")
    script = (ROOT / "deploy" / "v3_backup_timer.sh").read_text(encoding="utf-8")
    assert "/opt/orion-v3/scripts/v3_backup.py periodic" in service and "User=" not in service
    assert "OnCalendar=*-*-* 03:15:00" in timer and "Persistent=true" in timer
    assert "systemctl --user" in script and "sudo systemctl" not in script
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    assert not any("rm " in line and "sauvegardes" in line for line in code)  # aucune sauvegarde effacée ici
    bash = shutil.which("bash")
    if bash:
        assert subprocess.run([bash, "-n", str(ROOT / "deploy" / "v3_backup_timer.sh")]).returncode == 0


def test_the_pc_copy_is_read_only_on_the_core_and_verified():
    script = (ROOT / "deploy" / "pc_core_backup_pull.ps1").read_bytes()
    assert script.startswith(b"\xef\xbb\xbf")  # PowerShell 5.1 : accents lus correctement
    text = script.decode("utf-8")
    assert "BatchMode=yes" in text and "Get-FileHash -Algorithm SHA256" in text and ".partiel" in text
    for line in text.splitlines():
        if "ssh.exe" in line:
            assert '"ls -1 $Remote' in line  # seule commande lancée sur le Core : une lecture


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid") or __import__("os").geteuid() == 0,
                    reason="droits de fichier POSIX, compte non root")
def test_unreadable_data_leaves_no_partial_backup_and_an_unwritable_status_never_crashes(tmp_path):
    """5 novembre 2026 : le service de la nuit n'avait pas le groupe orion-v3 (bases illisibles, puis état impossible
    à écrire, plantage). La sauvegarde ratée ne laisse plus de dossier vide, et l'échec est rendu, jamais levé."""
    data, db = make_data(tmp_path)
    db.close()
    (data / "orion_v3.db").chmod(0)
    locked = tmp_path / "verrouille"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = v3_backup.periodic(data, tmp_path / "out", locked / v3_backup.STATUS_NAME)
    finally:
        (data / "orion_v3.db").chmod(0o600)
        locked.chmod(0o700)
    assert result["ok"] is False and len(result["problems"]) == 2 and "état non écrit" in result["problems"][1]
    assert list((tmp_path / "out" / "quotidiennes").iterdir()) == []


def test_the_nightly_service_takes_the_orion_v3_group():
    service = (ROOT / "deploy" / "orion-v3-backup.service").read_text(encoding="utf-8")
    assert 'ExecStart=/usr/bin/sg orion-v3 -c "/opt/orion-v3/.venv/bin/python' in service
