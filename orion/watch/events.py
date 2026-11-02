"""Événements et annonces : rumeur, fuite, annonce officielle, événement confirmé ; dates et changements de calendrier.

Règles (déterministes) :
- « official » : la source est déclarée officielle (``reliability = "official"``) ; rien d'autre ne rend officiel ;
- « confirmed » : annonce officielle avec au moins une date d'activité ;
- « leak » : fuite (datamine, fichiers du jeu…) ; « rumor » : conditionnel, spéculation, « selon des sources » ;
- un article de média qui dit « officiel » reste « unverified » : il rapporte, il n'annonce pas.
Une rumeur ou une fuite n'est jamais présentée comme officielle, même si elle cite les bons mots-clés.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from orion.platform.store import Migration, Store, dumps, loads
from orion.watch.connectors import Item
from orion.watch.products import plain

OFFICIAL, CONFIRMED, LEAK, RUMOR, UNVERIFIED = "official", "confirmed", "leak", "rumor", "unverified"
STATUS_LABELS = {CONFIRMED: "confirmé officiellement", OFFICIAL: "annonce officielle (sans date)",
                 UNVERIFIED: "rapporté par un média, non confirmé", RUMOR: "rumeur", LEAK: "fuite"}
LEAK_WORDS = ("datamine", "data mine", "dataminers", "leak", "leaked", "leaks", "fuite", "fuites", "fichiers du jeu",
              "game files", "code du jeu", "apk teardown", "decouvert dans le code")
RUMOR_WORDS = ("rumeur", "rumeurs", "rumor", "rumors", "rumour", "pourrait", "pourraient", "could", "might",
               "may be", "possible", "possiblement", "speculation", "selon des sources", "according to sources",
               "aurait", "seraient", "serait", "probablement", "likely", "unconfirmed", "non confirme")
MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
          "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "january": 1, "february": 2, "march": 3,
          "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
          "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
          "sept": 9, "oct": 10, "nov": 11, "dec": 12}
MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
FR_RANGE = re.compile(rf"\bdu (\d{{1,2}})(?: ({MONTH}))? au (\d{{1,2}}) ({MONTH})(?: (\d{{4}}))?")
EN_RANGE = re.compile(rf"\b({MONTH}) (\d{{1,2}})(?: (?:to|through|-) (?:({MONTH}) )?(\d{{1,2}}))?(?: (\d{{4}}))?")
FR_DAY = re.compile(rf"\b(?:le )?(\d{{1,2}}) ({MONTH})(?: (\d{{4}}))?")
ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")

MIGRATIONS = (
    Migration(1, """
        CREATE TABLE event_calendar (
            watch_id TEXT NOT NULL,
            key TEXT NOT NULL,
            dates TEXT NOT NULL,
            status TEXT NOT NULL,
            url TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (watch_id, key)
        )
    """),
)


@dataclass(frozen=True)
class EventInfo:
    status: str
    key: str  # nom normalisé de l'événement (titre sans dates ni ponctuation)
    start: date | None
    end: date | None

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def dates_text(self) -> str:
        if self.start is None:
            return ""
        if self.end is None or self.end == self.start:
            return f"le {self.start:%d/%m/%Y}"
        return f"du {self.start:%d/%m} au {self.end:%d/%m/%Y}"


def _year(month: int, day: int, explicit: str | None, reference: datetime) -> date | None:
    year = int(explicit) if explicit else reference.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if not explicit and (reference.date() - found).days > 180:  # « le 3 janvier » lu en décembre : l'an prochain
        found = date(year + 1, month, day)
    return found


def dates_in(text: str, reference: datetime) -> tuple[date | None, date | None]:
    t = plain(text)
    raw = text.lower()  # les dates ISO gardent leurs tirets
    if m := ISO.search(raw):
        start = _year(int(m.group(2)), int(m.group(3)), m.group(1), reference)
        later = ISO.findall(raw[m.end():])
        end = _year(int(later[0][1]), int(later[0][2]), later[0][0], reference) if later else None
        return start, end
    if m := FR_RANGE.search(t):
        end_month = MONTHS[m.group(4)]
        start = _year(MONTHS[m.group(2)] if m.group(2) else end_month, int(m.group(1)), m.group(5), reference)
        return start, _year(end_month, int(m.group(3)), m.group(5), reference)
    if m := FR_DAY.search(t):
        return _year(MONTHS[m.group(2)], int(m.group(1)), m.group(3), reference), None
    if m := EN_RANGE.search(t):
        start = _year(MONTHS[m.group(1)], int(m.group(2)), m.group(5), reference)
        end = None
        if m.group(4):
            end = _year(MONTHS[m.group(3) or m.group(1)], int(m.group(4)), m.group(5), reference)
        return start, end
    return None, None


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def event_key(title: str) -> str:
    words = [w for w in plain(title).split() if not w.isdigit() and w not in MONTHS and len(w) > 2
             and w not in ("les", "des", "une", "the", "and", "pour", "avec", "from", "dans")]
    return " ".join(words[:8])


def classify(item: Item, now: float | None = None) -> EventInfo:
    text = plain(f"{item.title} {item.summary}")
    reference = datetime.fromtimestamp(item.published_at or (time.time() if now is None else now))
    start, end = dates_in(f"{item.title}. {item.summary}", reference)
    if _has(text, LEAK_WORDS):
        status = LEAK
    elif item.reliability == "official":
        status = CONFIRMED if start is not None else OFFICIAL
    elif _has(text, RUMOR_WORDS):
        status = RUMOR
    else:
        status = UNVERIFIED
    return EventInfo(status, event_key(item.title), start, end)


def satisfies(info: EventInfo, require: str) -> bool:
    """Exigence d'une surveillance : « confirmed » (officiel daté), « official » (officiel), « any »."""
    if require == "confirmed":
        return info.status == CONFIRMED
    if require == "official":
        return info.status in (CONFIRMED, OFFICIAL)
    return True


class EventCalendar:
    """Dates connues de chaque événement suivi : signale un nouvel événement et un changement de calendrier."""

    def __init__(self, store: Store, clock=time.time):
        self._store, self._clock = store, clock
        store.migrate("event_calendar", MIGRATIONS)

    def update(self, watch_id: str, info: EventInfo, url: str) -> str:
        """« new », « changed » (dates ou statut différents) ou « same »."""
        dates = dumps([info.start.isoformat() if info.start else None, info.end.isoformat() if info.end else None])
        row = self._store.one("SELECT dates, status FROM event_calendar WHERE watch_id = ? AND key = ?",
                              (watch_id, info.key))
        if row is not None and row["dates"] == dates and row["status"] == info.status:
            return "same"
        self._store.write("INSERT INTO event_calendar (watch_id, key, dates, status, url, updated_at) VALUES "
                          "(?, ?, ?, ?, ?, ?) ON CONFLICT(watch_id, key) DO UPDATE SET dates = excluded.dates, "
                          "status = excluded.status, url = excluded.url, updated_at = excluded.updated_at",
                          (watch_id, info.key, dates, info.status, url, self._clock()))
        return "new" if row is None else "changed"

    def known(self, watch_id: str) -> list[dict]:
        rows = self._store.query("SELECT * FROM event_calendar WHERE watch_id = ? ORDER BY updated_at DESC",
                                 (watch_id,))
        return [{**r, "dates": loads(r["dates"], [])} for r in rows]
