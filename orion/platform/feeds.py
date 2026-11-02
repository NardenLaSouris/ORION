"""Lecture des sources extérieures : HTTP borné et flux RSS / Atom, pour WATCH et NEWS.

- HTTP : http(s) seulement, délai, taille maximale, identification honnête d'ORION ; pas de contournement de
  protection (aucun faux navigateur, aucun captcha) : une source qui refuse est simplement indisponible.
- XML : un document avec DOCTYPE ou ENTITY est refusé (entités externes, « milliard de rires ») ; seul le texte est
  gardé (balises HTML retirées), jamais interprété.
- Le contenu reste une donnée non fiable : rien ici ne suit une instruction qu'il contiendrait.
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

USER_AGENT = "ORION/3 (assistant personnel local ; usage privé, quelques requêtes par heure)"
MAX_BYTES = 2_000_000
TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|at_|xtor$)")


class SourceError(RuntimeError):
    """Source indisponible (réseau, refus, contenu illisible) : jamais une donnée inventée à la place."""


Fetcher = Callable[[str], bytes]


def http_get(url: str, timeout: float = 15.0, max_bytes: int = MAX_BYTES) -> bytes:
    if urlsplit(url).scheme not in ("http", "https"):
        raise SourceError(f"adresse refusée : {url[:80]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise SourceError(f"HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise SourceError(type(exc).__name__) from exc
    if len(data) > max_bytes:
        raise SourceError("réponse trop volumineuse")
    return data


def text_of(value: str | None, limit: int = 2000) -> str:
    """Texte brut : balises retirées, entités décodées, espaces normalisés."""
    return SPACES.sub(" ", html.unescape(TAG.sub(" ", value or ""))).strip()[:limit]


def canonical_url(url: str) -> str:
    """Même article, même adresse : sans fragment ni paramètres de suivi, hôte en minuscules."""
    parts = urlsplit((url or "").strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k.lower())])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def domain(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True)
class FeedEntry:
    title: str
    summary: str
    url: str
    published_at: float | None
    guid: str = ""
    categories: tuple[str, ...] = field(default_factory=tuple)


def _date(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    for child in node:
        if _local(child.tag) in names:
            return child
    return None


def _text(node: ET.Element, *names: str) -> str:
    child = _child(node, *names)
    return "".join(child.itertext()) if child is not None else ""


def parse_feed(data: bytes, limit: int = 200) -> list[FeedEntry]:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise SourceError("document XML refusé (DOCTYPE ou ENTITY)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceError("flux illisible") from exc
    items = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    entries = []
    for node in items[:limit]:
        link = _child(node, "link")
        url = ""
        if link is not None:
            url = (link.get("href") or link.text or "").strip()
        title = text_of(_text(node, "title"), 300)
        if not title or not url:
            continue
        summary = text_of(_text(node, "description", "summary", "content"), 2000)
        published = _date(_text(node, "pubdate", "published", "updated", "date"))
        guid = _text(node, "guid", "id").strip()[:300]
        categories = tuple(text_of(c.get("term") or c.text or "", 60) for c in node if _local(c.tag) == "category")
        entries.append(FeedEntry(title, summary, url, published, guid, tuple(c for c in categories if c)))
    return entries


def fetch_feed(url: str, fetcher: Fetcher = http_get) -> list[FeedEntry]:
    return parse_feed(fetcher(url))


def age_hours(published_at: float | None, now: float | None = None) -> float | None:
    if published_at is None:
        return None
    return max(0.0, ((time.time() if now is None else now) - published_at) / 3600)
