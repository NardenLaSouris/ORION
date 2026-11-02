"""Observation : journal d'ORION -> anomalies classées (logicielle, panne extérieure, latence, tests).

- Une trace Python (``Traceback``) dont l'exception est une erreur de programmation (TypeError, KeyError…) et dont au
  moins une ligne est dans le code d'ORION : anomalie **logicielle**, candidate à un diagnostic.
- Délais dépassés, réseau, TLS, serveur IMAP, HTTP 5xx d'un service extérieur, « injoignable » : **panne extérieure**,
  jamais « corrigée » dans le code ; signalée seulement au-delà d'un seuil quotidien (dégradation durable).
- Lignes ``[mesure]`` : latence médiane au-dessus du seuil -> anomalie de **latence** (rapport seulement).
- Même cause (type d'exception + dernière fonction d'ORION en cause) : une seule anomalie, avec un compteur.
Les extraits gardés sont courts ; le journal d'ORION masque déjà les textes privés (V2).
"""

from __future__ import annotations

import hashlib
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable

SOFTWARE, EXTERNAL, LATENCY, TESTS, UNKNOWN = "logicielle", "extérieure", "latence", "tests", "à examiner"
PROGRAMMING = ("TypeError", "KeyError", "AttributeError", "ValueError", "IndexError", "NameError", "AssertionError",
               "UnboundLocalError", "ZeroDivisionError", "RecursionError", "ImportError", "ModuleNotFoundError",
               "NotImplementedError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError", "LookupError",
               "InvalidTransition", "StopIteration")
NETWORK = ("TimeoutError", "timeout", "ConnectionError", "ConnectionResetError", "ConnectionRefusedError",
           "ConnectionAbortedError", "BrokenPipeError", "SSLError", "SSLEOFError", "URLError", "HTTPError",
           "RemoteDisconnected", "gaierror", "abort", "IncompleteRead", "SourceError", "LLMUnavailable",
           "LLMUnreachable", "LLMTimeout", "MailError", "OSError")
EXTERNAL_WARNING = re.compile(r"(?i)injoignable|timeouterror|ssleoferror|no route to host|device unreachable|"
                              r"http 5\d\d|erreur http 5\d\d|connexion perdue|connexion imap|indisponible|offline")
TRACE_START = re.compile(r"^Traceback \(most recent call last\):")
FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)')
EXCEPTION = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|abort|timeout|Unavailable|Unreachable|"
                       r"Timeout|Transition|Iteration)):?\s?(.*)$")
LEVEL = re.compile(r"^(?:\d\d:\d\d:\d\d )?(ERROR|WARNING|CRITICAL)\s+(?:\[r:[^\]]+\]\s*)?(.*)$")
MEASURE = re.compile(r"\[mesure\].*?\btotal=(\d+(?:\.\d+)?)")
NUMBERS = re.compile(r"\d+")


@dataclass
class Frame:
    path: str  # relatif au dépôt (« orion/mail/provider.py ») si dans ORION, sinon tel quel
    line: int
    function: str

    @property
    def in_orion(self) -> bool:
        return self.path.startswith(("orion/", "jarvis/"))


@dataclass
class Anomaly:
    signature: str
    kind: str
    title: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    samples: list[str] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)
    exception: str = ""
    message: str = ""

    @property
    def fixable(self) -> bool:
        return self.kind == SOFTWARE


def _relative(path: str) -> str:
    """« /opt/orion/orion/mail/provider.py » -> « orion/mail/provider.py » (dernier dossier du paquet, pas celui de
    l'installation qui porte le même nom)."""
    text = path.replace("\\", "/")
    for marker in ("/orion/", "/jarvis/"):
        if marker in text:
            return marker.strip("/") + "/" + text.rsplit(marker, 1)[1]
    return path


def _signature(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _stamp(line: str) -> str:
    m = re.match(r"^(\d\d:\d\d:\d\d)", line)
    return m.group(1) if m else ""


def parse(lines: Iterable[str], external_threshold: int = 50, latency_threshold: float = 6.0) -> list[Anomaly]:
    found: dict[str, Anomaly] = {}
    external: dict[str, Anomaly] = {}
    totals: list[float] = []
    lines = list(lines)
    i = 0
    last_error = ""
    while i < len(lines):
        line = lines[i].rstrip("\n")
        if m := MEASURE.search(line):
            totals.append(float(m.group(1)))
        level = LEVEL.match(line)
        if level and level.group(1) in ("ERROR", "CRITICAL"):
            last_error = line
        if TRACE_START.match(line.strip()):
            frames: list[Frame] = []
            j = i + 1
            exception, message = "", ""
            while j < len(lines):
                text = lines[j].rstrip("\n")
                if f := FRAME.match(text):
                    frames.append(Frame(_relative(f.group(1)), int(f.group(2)), f.group(3)))
                elif e := EXCEPTION.match(text.strip()):
                    exception, message = e.group(1).rsplit(".", 1)[-1], e.group(2)[:200]
                    if not text.startswith(" "):
                        break
                elif text and not text.startswith(" ") and not text.startswith("~") and "^" not in text:
                    break
                j += 1
            own = [f for f in frames if f.in_orion]
            if exception in NETWORK or (exception and not own):
                kind = EXTERNAL
            elif exception in PROGRAMMING and own:
                kind = SOFTWARE
            else:
                kind = UNKNOWN
            where = f"{own[-1].path}:{own[-1].function}" if own else (frames[-1].function if frames else "")
            sig = _signature(kind, exception, where)
            table = external if kind == EXTERNAL else found
            anomaly = table.setdefault(sig, Anomaly(sig, kind, f"{exception} dans {where}" if where else exception,
                                                    exception=exception, message=NUMBERS.sub("N", message),
                                                    frames=[f.__dict__ for f in frames[-8:]]))
            anomaly.count += 1
            anomaly.first_seen = anomaly.first_seen or _stamp(last_error)
            anomaly.last_seen = _stamp(last_error) or anomaly.last_seen
            if len(anomaly.samples) < 3:
                anomaly.samples.append((last_error[:200] + f" -> {exception}: {message}")[:300])
            i = j + 1
            continue
        if level and level.group(1) == "WARNING" and EXTERNAL_WARNING.search(level.group(2)):
            key = NUMBERS.sub("N", level.group(2))[:120]
            sig = _signature(EXTERNAL, key)
            anomaly = external.setdefault(sig, Anomaly(sig, EXTERNAL, key))
            anomaly.count += 1
            anomaly.first_seen = anomaly.first_seen or _stamp(line)
            anomaly.last_seen = _stamp(line) or anomaly.last_seen
            if len(anomaly.samples) < 3:
                anomaly.samples.append(line[:200])
        i += 1
    out = list(found.values())
    out += [a for a in external.values() if a.count >= external_threshold]  # sous le seuil : bruit normal du réseau
    if len(totals) >= 5 and statistics.median(totals) > latency_threshold:
        median = statistics.median(totals)
        out.append(Anomaly(_signature(LATENCY), LATENCY, f"Latence médiane {median:.1f} s (seuil "
                                                         f"{latency_threshold:.0f} s)", count=len(totals)))
    return out


Runner = Callable[[list[str], float], tuple[int, str]]


def run(argv: list[str], timeout: float = 60.0) -> tuple[int, str]:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return completed.returncode, completed.stdout + completed.stderr


def journal(units: tuple[str, ...], since: str = "-24h", runner: Runner = run) -> list[str]:
    """Lignes du journal systemd des unités d'ORION (lecture seule ; ``journalctl`` sans privilège)."""
    argv = ["journalctl", "--no-pager", "-o", "cat", "--since", since]
    for unit in units:
        if not re.match(r"^[a-z][a-z0-9_.-]{0,40}$", unit):
            raise ValueError(f"unité refusée : {unit!r}")
        argv += ["-u", unit]
    code, output = runner(argv, 60.0)
    return output.splitlines() if code == 0 else []
