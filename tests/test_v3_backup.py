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
    assert "| grep -q" not in script  # sous pipefail, un grep -q qui trouve fait échouer le tube (SIGPIPE)
    bash = shutil.which("bash")
    if bash:
        assert subprocess.run([bash, "-n", str(ROOT / "deploy" / "v3_deploy.sh")]).returncode == 0
