"""Inventaire d'un export Google Takeout, avant toute décision d'import.

Ne lit **que l'index des archives** (noms des fichiers, tailles, dates) : aucun contenu n'est ouvert ni décompressé,
rien n'est envoyé nulle part, rien n'est copié. Le rapport dit :
- quelles archives et quels services Google (« Mon activité », « Chrome », « YouTube et YouTube Music »…) ;
- pour « Mon activité », quels produits (Recherche, YouTube, Chrome, Discover…) ;
- les formats (JSON, HTML, CSV, MBOX…) et les volumes, fichier par fichier groupés ;
- les fichiers illisibles ou suspects (chemin absolu, « .. », archive chiffrée ou corrompue).

Usage : ``python -m orion.imports.takeout <archives ou dossier>`` (rapport Markdown sur la sortie standard).
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

ARCHIVES = (".zip", ".tgz", ".tar.gz", ".tar")
FORMATS = {".json": "JSON", ".html": "HTML", ".htm": "HTML", ".csv": "CSV", ".mbox": "MBOX", ".ics": "ICS",
           ".vcf": "VCF", ".jpg": "image", ".jpeg": "image", ".png": "image", ".heic": "image", ".gif": "image",
           ".mp4": "vidéo", ".mov": "vidéo", ".mp3": "audio", ".xml": "XML", ".txt": "texte", ".kml": "KML",
           ".pdf": "PDF"}


@dataclass
class Entry:
    path: str
    size: int


@dataclass
class Group:
    files: int = 0
    size: int = 0
    formats: dict = field(default_factory=dict)

    def add(self, entry: Entry, fmt: str) -> None:
        self.files += 1
        self.size += entry.size
        self.formats[fmt] = self.formats.get(fmt, 0) + 1


@dataclass
class Inventory:
    archives: list[tuple[str, int, int]] = field(default_factory=list)  # (nom, fichiers, octets)
    services: dict[str, Group] = field(default_factory=dict)
    activity: dict[str, Group] = field(default_factory=dict)  # produits de « Mon activité »
    problems: list[str] = field(default_factory=list)


def _format(name: str) -> str:
    suffix = PurePosixPath(name.lower()).suffix
    return FORMATS.get(suffix, suffix.lstrip(".").upper() or "sans extension")


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and not name.startswith("\\")


def _entries(archive: Path) -> Iterator[Entry]:
    """Index seulement : ``infolist`` (zip) et ``getmembers`` (tar) ne décompressent aucun contenu."""
    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.flag_bits & 0x1:
                    raise ValueError("archive chiffrée")
                if not info.is_dir():
                    yield Entry(info.filename, info.file_size)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if member.isfile():
                    yield Entry(member.name, member.size)


def find_archives(paths: Iterable[Path]) -> list[Path]:
    found = []
    for path in paths:
        if path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.is_file() and p.name.lower().endswith(ARCHIVES))
        elif path.is_file():
            found.append(path)
    return found


def inventory(paths: Iterable[Path | str]) -> Inventory:
    result = Inventory()
    for archive in find_archives(Path(p) for p in paths):
        count = total = 0
        try:
            for entry in _entries(archive):
                if not _safe(entry.path):
                    result.problems.append(f"{archive.name} : chemin suspect ignoré ({entry.path[:80]})")
                    continue
                count, total = count + 1, total + entry.size
                parts = PurePosixPath(entry.path).parts
                if parts and parts[0] == "Takeout":
                    parts = parts[1:]
                service = parts[0] if len(parts) > 1 else "(racine)"
                fmt = _format(entry.path)
                result.services.setdefault(service, Group()).add(entry, fmt)
                if service in ("Mon activité", "My Activity") and len(parts) > 2:
                    result.activity.setdefault(parts[1], Group()).add(entry, fmt)
        except (zipfile.BadZipFile, tarfile.TarError, ValueError, OSError) as exc:
            result.problems.append(f"{archive.name} : illisible ({exc})")
        result.archives.append((archive.name, count, total))
    return result


def size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} Go"


def report(inv: Inventory) -> str:
    def table(groups: dict[str, Group]) -> list[str]:
        rows = ["| Élément | Fichiers | Volume | Formats |", "|---|---|---|---|"]
        for name, g in sorted(groups.items(), key=lambda kv: -kv[1].size):
            formats = ", ".join(f"{f} ({n})" for f, n in sorted(g.formats.items(), key=lambda kv: -kv[1]))
            rows.append(f"| {name} | {g.files} | {size(g.size)} | {formats} |")
        return rows

    lines = ["# Inventaire Google Takeout", "",
             "Index des archives seulement : aucun contenu ouvert, rien d'importé ni d'envoyé.", "",
             "## Archives", "", "| Archive | Fichiers | Volume |", "|---|---|---|"]
    lines += [f"| {name} | {count} | {size(total)} |" for name, count, total in inv.archives]
    total_size = sum(t for _, _, t in inv.archives)
    lines += ["", f"Total : {sum(c for _, c, _ in inv.archives)} fichiers, {size(total_size)}.", "",
              "## Services", ""] + table(inv.services)
    if inv.activity:
        lines += ["", "## Mon activité (par produit)", ""] + table(inv.activity)
    if inv.problems:
        lines += ["", "## Problèmes", ""] + [f"- {p}" for p in inv.problems]
    lines += ["", "## Suite", "", "Aucun import n'est fait sans votre validation explicite."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage : python -m orion.imports.takeout <archives ou dossier>", file=sys.stderr)
        return 2
    sys.stdout.write(report(inventory(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
