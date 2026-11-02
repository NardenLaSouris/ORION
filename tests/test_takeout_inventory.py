"""Inventaire Google Takeout : index seulement (aucun contenu lu), services, produits de « Mon activité », formats,
volumes, archives illisibles ou suspectes. Archives factices."""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.imports import takeout  # noqa: E402

FILES = {
    "Takeout/Mon activité/Recherche/MonActivité.json": b'[{"title": "secret"}]' * 10,
    "Takeout/Mon activité/YouTube/MonActivité.json": b"[]" * 50,
    "Takeout/Chrome/Historique.json": b"{}" * 100,
    "Takeout/YouTube et YouTube Music/historique/historique-des-vidéos-regardées.html": b"<html></html>",
    "Takeout/archive_browser.html": b"<html></html>",
}


def make_zip(path, files=FILES):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def make_tgz(path, files):
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def test_inventory_groups_services_activity_formats_and_volumes(tmp_path):
    make_zip(tmp_path / "takeout-001.zip")
    make_tgz(tmp_path / "takeout-002.tgz", {"Takeout/Agenda/perso.ics": b"BEGIN:VCALENDAR"})
    inv = takeout.inventory([tmp_path])
    assert [a[0] for a in inv.archives] == ["takeout-001.zip", "takeout-002.tgz"]
    assert set(inv.services) == {"Mon activité", "Chrome", "YouTube et YouTube Music", "(racine)", "Agenda"}
    assert set(inv.activity) == {"Recherche", "YouTube"}
    assert inv.services["Chrome"].formats == {"JSON": 1} and inv.services["Chrome"].size == 200
    text = takeout.report(inv)
    assert "Recherche" in text and "Aucun import" in text and "secret" not in text


def test_contents_are_never_read(tmp_path, monkeypatch):
    make_zip(tmp_path / "takeout.zip")

    def forbidden(*args, **kwargs):
        raise AssertionError("contenu lu")

    monkeypatch.setattr(zipfile.ZipFile, "read", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", forbidden)
    assert takeout.inventory([tmp_path / "takeout.zip"]).archives[0][1] == len(FILES)


def test_suspicious_paths_and_broken_archives_are_reported(tmp_path):
    make_zip(tmp_path / "a.zip", {"../evasion.json": b"x", "Takeout/Chrome/ok.json": b"{}"})
    (tmp_path / "b.zip").write_bytes(b"pas une archive")
    inv = takeout.inventory([tmp_path])
    assert any("chemin suspect" in p for p in inv.problems)
    assert any("b.zip : illisible" in p for p in inv.problems)
    assert inv.services["Chrome"].files == 1


def test_command_line(tmp_path, capsys):
    make_zip(tmp_path / "takeout.zip")
    assert takeout.main([str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("# Inventaire Google Takeout")
    assert takeout.main([]) == 2
