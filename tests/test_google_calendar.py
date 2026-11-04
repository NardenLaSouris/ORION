"""Agenda Google (V3) : calendriers, adresses et clés fictifs uniquement.

Couvre : analyse ICS (fuseaux, journée entière, plusieurs jours), répétitions et leurs exceptions (EXDATE, occurrence
modifiée, occurrence annulée, BYMONTHDAY), détection des nouveautés, modifications et suppressions, idempotence,
erreurs réseau et flux invalide (jamais une suppression), secret absent ou refusé, base personnelle verrouillée,
absence du secret dans les journaux et l'état, copie scellée sur disque, annonces F3 (une fois), échec prolongé et
reprise, aucune tâche F2 créée, réserve de fraîcheur des réponses, disponibilités, construction et routes."""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.agenda import Calendar, calendar_tools, expand, parse_ics  # noqa: E402
from orion.google_calendar import GoogleCalendarModule, GoogleCalendarProvider, IcalError, fetch_ical  # noqa: E402
from orion.google_calendar.module import GoogleCalendarSettings  # noqa: E402
from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.personal import PersonalStore  # noqa: E402
from orion.platform.store import PERSONAL, Store  # noqa: E402
from orion.platform.vault import DataKeyring  # noqa: E402

KEY = b"\x33" * 32  # clé factice
URL = "https://calendar.google.com/calendar/ical/orion.essai%40group.calendar.google.com/private-0123456789abcdef0123/basic.ics"
NOW = datetime(2026, 11, 4, 12, 0)


def at(zone: str, *parts: int) -> datetime:
    """Heure attendue sur la machine qui fait tourner les tests (Paris sur le Core, UTC sur un poste de test)."""
    from zoneinfo import ZoneInfo

    return datetime(*parts, tzinfo=ZoneInfo(zone)).astimezone().replace(tzinfo=None)


def ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def vevent(uid: str, start: str, end: str, title: str, *extra: str) -> str:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART{start}", f"DTEND{end}", f"SUMMARY:{title}", *extra, "END:VEVENT"]
    return "\r\n".join(lines) + "\r\n"


DENTISTE = vevent("a1", ";TZID=Europe/Paris:20261106T100000", ";TZID=Europe/Paris:20261106T110000", "Dentiste",
                  "LOCATION:Nantes")
VACANCES = vevent("a2", ";VALUE=DATE:20261113", ";VALUE=DATE:20261116", "Vacances")  # vendredi au dimanche
SPORT = vevent("a3", ":20261105T170000Z", ":20261105T180000Z", "Sport", "RRULE:FREQ=WEEKLY;COUNT=6",
               "EXDATE:20261112T170000Z")
SPORT_MOVED = vevent("a3", ":20261119T190000Z", ":20261119T200000Z", "Sport (décalé)",
                     "RECURRENCE-ID:20261119T170000Z")
SPORT_CANCELLED = vevent("a3", ":20261126T170000Z", ":20261126T180000Z", "Sport", "RECURRENCE-ID:20261126T170000Z",
                         "STATUS:CANCELLED")


class Feed:
    def __init__(self, text: str = ""):
        self.text, self.error, self.calls = text, None, []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.error:
            raise IcalError(self.error)
        return self.text


class Clock:
    def __init__(self):
        self.t = 1_793_800_000.0

    def __call__(self):
        return self.t


def world(tmp_path, feed, *, key=True, secret=URL):
    foundation = Foundation(Store(tmp_path / "ops.db"))
    personal = PersonalStore(Store(tmp_path / "personal.db", PERSONAL), DataKeyring({"k1": KEY}, "k1") if key else None)
    foundation.attach_personal(personal, DataKeyring({"k1": KEY}, "k1") if key else None)
    clock = Clock()
    module = GoogleCalendarModule(foundation.store, foundation.tasks.sealer, lambda: secret, events=foundation.events,
                                  fetch=feed, clock=lambda: NOW, monotonic=clock,
                                  settings=GoogleCalendarSettings(failure_alert_minutes=30))
    return foundation, module, clock


def published(foundation, kind):
    return [e for e in foundation.events.recent(500) if e.get("type") == kind]


# --- Analyse ICS ------------------------------------------------------------------------------------------------

def test_ics_parsing_timezones_all_day_multi_day_recurrence_and_exceptions():
    events = parse_ics(ics(DENTISTE, VACANCES, SPORT, SPORT_MOVED, SPORT_CANCELLED))
    found = expand(events, datetime(2026, 11, 1), datetime(2026, 12, 31))
    by = [(e.title, e.start, e.all_day) for e in found]
    assert ("Dentiste", at("Europe/Paris", 2026, 11, 6, 10, 0), False) in by  # fuseau du calendrier respecté
    assert ("Vacances", datetime(2026, 11, 13), True) in by
    vacances = next(e for e in found if e.title == "Vacances")
    assert vacances.end == datetime(2026, 11, 16)  # trois jours
    sports = [e.start for e in found if e.title.startswith("Sport")]
    # 6 occurrences (17 h UTC) : 5/11, 12/11 exclue, 19/11 déplacée à 19 h UTC, 26/11 annulée, 3/12, 10/12
    assert sports == [at("UTC", 2026, 11, 5, 17), at("UTC", 2026, 11, 19, 19), at("UTC", 2026, 12, 3, 17),
                      at("UTC", 2026, 12, 10, 17)]
    assert next(e for e in found if e.start == at("UTC", 2026, 11, 19, 19)).title == "Sport (décalé)"


def test_monthly_by_month_day_and_a_cancelled_event():
    loyer = vevent("m1", ":20261115T080000", ":20261115T081500", "Loyer", "RRULE:FREQ=MONTHLY;BYMONTHDAY=15;COUNT=3")
    gone = vevent("m2", ":20261120T080000", ":20261120T090000", "Annulé", "STATUS:CANCELLED")
    found = expand(parse_ics(ics(loyer, gone)), datetime(2026, 11, 1), datetime(2027, 3, 1))
    assert [e.start.date().isoformat() for e in found] == ["2026-11-15", "2026-12-15", "2027-01-15"]


def test_a_newer_sequence_replaces_the_older_copy_without_duplicates():
    old = vevent("s1", ":20261110T090000", ":20261110T100000", "Réunion", "SEQUENCE:1")
    new = vevent("s1", ":20261110T140000", ":20261110T150000", "Réunion", "SEQUENCE:2")
    found = expand(parse_ics(ics(old, new)), datetime(2026, 11, 1), datetime(2026, 12, 1))
    assert [(e.title, e.start.hour) for e in found] == [("Réunion", 14)]


# --- Synchronisation --------------------------------------------------------------------------------------------

def test_first_sync_is_a_baseline_then_changes_are_detected_and_announced_once(tmp_path):
    feed = Feed(ics(DENTISTE, SPORT))
    foundation, module, clock = world(tmp_path, feed)
    first = module.sync()
    assert first["state"] == "à jour" and first["count"] == 6 and first["announced"] == 0  # point de départ (dentiste + 5 séances)
    assert module.sync()["changes"] == 0  # idempotente
    moved = DENTISTE.replace("T100000", "T150000").replace("T110000", "T160000")
    feed.text = ics(moved, SPORT, VACANCES)
    result = module.sync()
    assert result["changes"] == 2 and result["announced"] == 2  # dentiste modifié, vacances ajoutées
    assert module.sync()["changes"] == 0
    feed.text = ics(SPORT, VACANCES)
    assert module.sync()["changes"] == 1  # dentiste supprimé
    kinds = [e["type"] for e in foundation.events.recent(500) if e.get("source") == "google_calendar"]
    assert sorted(kinds) == ["google_calendar.cancelled", "google_calendar.changed", "google_calendar.created"]
    assert foundation.tasks.list() == []  # aucune tâche ni rappel créé (seulement proposé)


def test_a_network_error_or_an_invalid_feed_never_deletes_anything(tmp_path):
    feed = Feed(ics(DENTISTE))
    foundation, module, clock = world(tmp_path, feed)
    module.sync()
    feed.error = "réseau (TimeoutError)"
    failed = module.sync()
    assert failed["state"] == "en échec" and module.status()["count"] == 1
    feed.error, feed.text = None, "<html>Erreur</html>"
    assert module.sync()["state"] == "en échec" and module.status()["count"] == 1  # flux illisible : rien supprimé
    calendar = Calendar([GoogleCalendarProvider(module)], clock=lambda: NOW)
    assert [e.title for e in calendar.events(NOW, NOW + timedelta(days=7))] == ["Dentiste"]
    assert "pas pu être mis à jour" in calendar.caveat()


def test_a_prolonged_failure_is_announced_once_then_the_recovery(tmp_path):
    feed = Feed(ics(DENTISTE))
    foundation, module, clock = world(tmp_path, feed)
    module.sync()
    feed.error = "réponse HTTP 503"
    for _ in range(4):
        clock.t += 600
        module.sync()
    assert len(published(foundation, "google_calendar.failing")) == 1
    feed.error = None
    clock.t += 300
    assert module.sync()["state"] == "à jour"
    assert len(published(foundation, "google_calendar.recovered")) == 1 and module.caveat() == ""


def test_the_snapshot_survives_a_restart_and_is_sealed_on_disk(tmp_path):
    feed = Feed(ics(DENTISTE))
    foundation, module, _ = world(tmp_path, feed)
    module.sync()
    raw = " ".join(str(v) for (v,) in sqlite3.connect(tmp_path / "ops.db").execute("SELECT v FROM google_calendar"))
    assert "Dentiste" not in raw and "Nantes" not in raw and "private-" not in raw
    again = GoogleCalendarModule(foundation.store, foundation.tasks.sealer, lambda: URL, fetch=feed,
                                 clock=lambda: NOW)
    assert again.status()["count"] == 1  # copie relue au démarrage, avant toute synchronisation


# --- Secret et verrouillage -------------------------------------------------------------------------------------

@pytest.mark.parametrize("secret", ["", "http://calendar.google.com/calendar/ical/x/private-0123456789abcdef/basic.ics",
                                    "https://exemple.fr/agenda.ics",
                                    "https://calendar.google.com/calendar/ical/x/public/basic.ics"])
def test_a_missing_or_foreign_address_is_never_used(tmp_path, secret):
    feed = Feed(ics(DENTISTE))
    _, module, _ = world(tmp_path, feed, secret=secret)
    assert module.sync() == {"state": "non configuré"} and feed.calls == []
    with pytest.raises(IcalError):
        fetch_ical(secret, 1.0, 1000)


def test_an_unreadable_secret_reads_as_not_configured(tmp_path):
    feed = Feed(ics(DENTISTE))
    foundation = Foundation(Store(tmp_path / "ops.db"))

    def broken():
        raise PermissionError("illisible")

    module = GoogleCalendarModule(foundation.store, foundation.tasks.sealer, broken, fetch=feed, clock=lambda: NOW)
    assert module.health() == "non configuré" and module.sync() == {"state": "non configuré"}


def test_a_locked_personal_base_keeps_the_connector_off(tmp_path):
    feed = Feed(ics(DENTISTE))
    _, module, _ = world(tmp_path, feed, key=False)
    assert module.sync() == {"state": "verrouillé"} and feed.calls == []
    assert module.status()["locked"] is True and "verrouillée" in module.caveat()


def test_the_secret_never_appears_in_logs_status_or_errors(tmp_path, caplog):
    feed = Feed(ics(DENTISTE))
    _, module, _ = world(tmp_path, feed)
    caplog.set_level(logging.DEBUG)
    module.sync()
    feed.error = "réseau (URLError)"
    module.sync()
    text = caplog.text + str(module.status()) + str(module.admin("GET", [], {}, None))
    assert "private-" not in text and "orion.essai" not in text and "Dentiste" not in text


# --- Conversation et ORION Control --------------------------------------------------------------------------------

def test_availability_counts_multi_day_events_and_answers_carry_the_caveat(tmp_path):
    feed = Feed(ics(VACANCES, DENTISTE))
    _, module, _ = world(tmp_path, feed)
    module.sync()
    calendar = Calendar([GoogleCalendarProvider(module)], clock=lambda: NOW)
    tools = {t.name: t for t in calendar_tools(calendar)}
    friday = tools["free_slots"].execute({"day": "2026-11-14"})
    assert friday["all_day"] == ["Vacances"]
    assert "toute la journée" in tools["free_slots"].say(friday)
    events = tools["list_events"].execute({"day": "2026-11-06"})
    assert "Dentiste" in tools["list_events"].say(events) and "pas pu" not in tools["list_events"].say(events)
    feed.error = "réseau (TimeoutError)"
    module.sync()
    assert "peut ne pas être à jour" in tools["list_events"].say(events)


def test_orion_control_routes_enable_disable_and_sync(tmp_path):
    feed = Feed(ics(DENTISTE))
    _, module, _ = world(tmp_path, feed)
    assert module.admin("POST", ["sync"], {}, None)[1]["state"] == "à jour"
    status = module.admin("POST", ["disable"], {}, None)[1]
    assert status["state"] == "désactivé" and module.sync() == {"state": "désactivé"}
    assert module.admin("POST", ["enable"], {}, None)[1]["enabled"] is True
    assert module.admin("DELETE", [], {}, None)[0] == 404  # lecture seule : rien à supprimer


def test_build_wires_the_connector_to_the_calendar_tools(tmp_path):
    from orion.config import load_config
    from orion.platform.build import build_v3, classify_job
    from orion.platform.permissions import Capability

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, google_calendar=replace(cfg.google_calendar, enabled=True),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"))
    modules = build_v3(cfg, foundation=Foundation(Store()))
    try:
        assert isinstance(modules.registry.get("google_calendar"), GoogleCalendarModule)
        assert isinstance(modules.calendar_provider, GoogleCalendarProvider)
    finally:
        modules.stop()
    assert classify_job("google_calendar:sync") == ("delegated:google_calendar:sync", Capability.NOTIFY.value)
