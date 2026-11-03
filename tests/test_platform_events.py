"""CORE FOUNDATION F3 : événements normalisés, décision séparée de la remise, dédoublonnage, priorités, échéances,
heures calmes, remise persistante et reprise, notifications manquées pendant l'arrêt d'urgence (récapitulatif,
relance manuelle, jamais de rejeu automatique ni d'obsolète), raccordement des alertes et des notifications.
Données factices uniquement."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform import Store  # noqa: E402
from orion.platform.alerts import Alert, AlertRouter, AlertSettings, Level  # noqa: E402
from orion.platform.autonomy import CONFIRMATION_PHRASE, CONTROL, VOICE, AutonomyState  # noqa: E402
from orion.platform.eventhub import (  # noqa: E402
    ANNOUNCE, DEFERRED, DENIED, DUPLICATE, EXPIRED, KEPT, MISSED, Event, EventHub, InterruptionPolicy,
)
from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.permissions import PermissionEngine  # noqa: E402

NOON = datetime(2026, 11, 3, 12, 0).timestamp()
NIGHT = datetime(2026, 11, 3, 23, 30).timestamp()


class Clock:
    def __init__(self, t=NOON):
        self.t = t

    def __call__(self):
        return self.t


class Speaker:
    def __init__(self, up=True):
        self.up, self.said = up, []

    def __call__(self, event):
        if not self.up:
            raise ConnectionError("enceintes injoignables")
        self.said.append(event["title"])
        return True


def hub_world(path=None, clock=None):
    clock = clock or Clock()
    store = Store(path) if path else Store()
    engine = PermissionEngine(store, clock=clock)
    hub = EventHub(store, permit=engine.decide, clock=clock,
                   policy=InterruptionPolicy("22:30", "08:00", quiet_exempt=frozenset({"reminder"})))
    speaker = Speaker()
    hub.channel("voix", speaker)
    return hub, engine, speaker, clock


def resume(engine, to=AutonomyState.SUSPENDED):
    engine.autonomy.change(to, CONTROL, confirmation=CONFIRMATION_PHRASE, session="control@test")


def reminder(title="Sortir le linge", ttl=6 * 3600, clock=None):
    return Event("reminder.due", "reminder", "delegated:reminder", title, f"Il est l'heure : {title}.",
                 expires_at=(clock() if clock else NOON) + ttl)


def mail(title="Facture EDF", **kwargs):
    return Event("mail.new", "mail", "delegated:mail", title, "Nouveau mail important.", **kwargs)


# --- Format et provenance -------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"type": "sans-point"}, {"source": "Majuscule"}, {"principal": "user:moi"}, {"priority": "urgent"}, {"title": " "},
])
def test_events_have_a_common_validated_format(bad):
    hub, *_ = hub_world()
    fields = {"type": "news.alert", "source": "news", "principal": "module:news", "title": "Titre", **bad}
    with pytest.raises(ValueError):
        hub.publish(Event(**fields))


def test_a_module_publishes_only_under_its_own_identity():
    hub, engine, speaker, _ = hub_world()
    resume(engine, AutonomyState.ACTIVE)
    gate = hub.gate("sentinel", "module:sentinel")
    assert gate.publish("sentinel.disk", "Disque presque plein", "Il reste 3 %.").outcome == ANNOUNCE
    with pytest.raises(ValueError):
        gate.publish("news.alert", "Usurpation")  # le type d'un autre module
    assert gate.principal == "module:sentinel" and not hasattr(gate, "__dict__")
    with pytest.raises(ValueError):
        hub.gate("sentinel", "user")  # jamais l'identité de l'utilisateur
    stored = hub.recent(1)[0]
    assert (stored["source"], stored["principal"]) == ("sentinel", "module:sentinel")


# --- Décision -----------------------------------------------------------------------------------------------

def test_decision_and_delivery_are_separate_and_both_kept():
    hub, engine, speaker, _ = hub_world()
    outcome = hub.publish(reminder())
    assert outcome.outcome == ANNOUNCE and speaker.said == ["Sortir le linge"]
    entry = hub.recent(1)[0]
    assert entry["decision"]["outcome"] == ANNOUNCE
    assert entry["deliveries"] == [{"channel": "voix", "status": "sent", "attempts": 1, "last_error": ""}]


def test_duplicates_are_not_announced_twice():
    hub, engine, speaker, clock = hub_world()
    first = hub.publish(mail(dedup_key="m-42"))
    again = hub.publish(mail(dedup_key="m-42"))
    assert again.outcome == DUPLICATE and again.event_id == first.event_id and len(speaker.said) == 1
    clock.t += 24 * 3600  # hors de la fenêtre de dédoublonnage (12 h), en journée
    assert hub.publish(mail(dedup_key="m-42")).outcome == ANNOUNCE


def test_low_priority_is_kept_and_expired_is_dropped():
    hub, engine, speaker, _ = hub_world()
    assert hub.publish(mail(priority="low")).outcome == KEPT
    assert hub.publish(mail(expires_at=NOON - 1)).outcome == EXPIRED
    assert speaker.said == []


def test_module_initiatives_are_denied_while_suspended():
    hub, engine, speaker, _ = hub_world()
    outcome = hub.publish(Event("news.alert", "news", "module:news", "Alerte"))
    assert outcome.outcome == DENIED and "suspendue" in outcome.reason and hub.missed() == []


def test_quiet_hours_defer_initiatives_but_not_what_you_scheduled():
    hub, engine, speaker, clock = hub_world(clock=Clock(NIGHT))
    assert hub.publish(reminder(clock=clock)).outcome == ANNOUNCE  # votre rappel de 23 h 30 : dit
    deferred = hub.publish(mail(expires_at=NIGHT + 3 * 86400))
    assert deferred.outcome == DEFERRED and speaker.said == ["Sortir le linge"]
    assert hub.publish(mail("Trop court", expires_at=NIGHT + 600)).outcome == KEPT  # échue avant la fin du calme
    clock.t = datetime(2026, 11, 4, 8, 0, 30).timestamp()
    hub.dispatch()
    assert speaker.said == ["Sortir le linge", "Facture EDF"]


def test_critical_may_bypass_quiet_hours_only_if_configured():
    policy = InterruptionPolicy("22:30", "08:00", critical_ignores_quiet=True)
    assert policy.route("critical", "home", True).sequence == ("voix",)
    assert policy.route("high", "home", True).sequence == ("écrit",)
    away = InterruptionPolicy().route("high", "away", False)
    assert away.sequence == ("écrit",) and away.call
    assert not InterruptionPolicy().route("high", "unknown", False).call  # présence inconnue : jamais d'appel


# --- Remise persistante et reprise ----------------------------------------------------------------------------

def test_failed_delivery_is_retried_and_survives_a_restart(tmp_path):
    clock = Clock()
    hub, engine, speaker, _ = hub_world(tmp_path / "ops.db", clock)
    speaker.up = False
    assert hub.publish(reminder()).outcome == ANNOUNCE and speaker.said == []
    assert hub.pending() == 1
    clock.t += 60
    restarted, _, voice, _ = hub_world(tmp_path / "ops.db", clock)  # ORION redémarré, enceintes revenues
    assert restarted.dispatch() == 1 and voice.said == ["Sortir le linge"] and restarted.pending() == 0


def test_a_delivery_is_never_made_after_the_event_expired():
    hub, engine, speaker, clock = hub_world()
    speaker.up = False
    hub.publish(reminder(ttl=120))
    speaker.up = True
    clock.t += 300
    assert hub.dispatch() == 0 and speaker.said == []
    assert hub.recent(1)[0]["decision"]["outcome"] == EXPIRED


# --- Arrêt d'urgence : notifications manquées -------------------------------------------------------------------

def test_reminders_and_mails_missed_during_emergency_stop_are_kept_never_replayed():
    hub, engine, speaker, clock = hub_world()
    engine.autonomy.emergency_stop(VOICE)
    assert hub.publish(reminder()).outcome == MISSED
    assert hub.publish(mail()).outcome == MISSED
    resume(engine)
    clock.t += 60
    assert hub.dispatch() == 0 and speaker.said == []  # jamais rejoué tout seul, ni tous les mails d'un coup
    assert [m["title"] for m in hub.missed()] == ["Sortir le linge", "Facture EDF"]
    assert hub.recap() == "Pendant l'arrêt d'urgence, vous avez manqué : 1 rappel, 1 annonce de mail."


def test_missed_notification_is_relaunched_by_you_unless_obsolete():
    hub, engine, speaker, clock = hub_world()
    engine.autonomy.emergency_stop(VOICE)
    linge = hub.publish(reminder(ttl=3600)).event_id
    facture = hub.publish(mail(expires_at=NOON + 3 * 86400)).event_id
    resume(engine)
    with pytest.raises(PermissionError):
        hub.relaunch(linge, by="module:news")
    clock.t += 2 * 3600  # le rappel du linge est devenu sans objet
    assert hub.relaunch(linge, by=CONTROL) == "obsolete" and speaker.said == []
    assert hub.relaunch(facture, by=VOICE) == "relaunched" and speaker.said == ["Facture EDF"]
    assert hub.relaunch(facture, by=VOICE) == "unknown"  # une seule fois
    assert [m["obsolete"] for m in hub.missed(include_obsolete=True)] == [True]
    assert hub.dismiss(linge, by=CONTROL) and hub.missed(include_obsolete=True) == []


def test_relaunch_is_still_subject_to_the_emergency_stop():
    hub, engine, speaker, clock = hub_world()
    engine.autonomy.emergency_stop(VOICE)
    event_id = hub.publish(mail(expires_at=NOON + 86400)).event_id
    assert hub.relaunch(event_id, by=CONTROL) == "relaunched" and speaker.said == []  # toujours arrêté : rien dit


def test_emergency_stop_between_decision_and_delivery_makes_it_missed():
    hub, engine, speaker, clock = hub_world(clock=Clock(NIGHT))
    hub.publish(mail(expires_at=NIGHT + 3 * 86400))  # différé aux heures calmes
    engine.autonomy.emergency_stop(VOICE)
    clock.t = datetime(2026, 11, 4, 8, 1).timestamp()
    assert hub.dispatch() == 0 and speaker.said == []
    assert [m["title"] for m in hub.missed()] == ["Facture EDF"]


def test_resumption_gives_a_recap_once():
    foundation = Foundation(Store())
    said = []
    foundation.events.channel("voix", lambda e: said.append(e["body"]) or True)
    foundation.autonomy.emergency_stop(VOICE)
    foundation.events.publish(reminder(clock=foundation.events._clock))  # noqa: SLF001
    foundation.events.publish(mail(expires_at=foundation.events._clock() + 86400))  # noqa: SLF001
    resume(foundation.permissions)
    assert len(said) == 1 and said[0].startswith("Pendant l'arrêt d'urgence, vous avez manqué : 1 rappel, 1 annonce")
    foundation.autonomy.change(AutonomyState.ACTIVE, CONTROL, confirmation=CONFIRMATION_PHRASE, session="s")
    assert len(said) == 1  # pas de nouveau récapitulatif sans nouvel arrêt


def test_voice_tools_list_and_relaunch_missed_notifications():
    foundation = Foundation(Store())
    said = []
    foundation.events.channel("voix", lambda e: said.append(e["title"]) or True)
    foundation.autonomy.emergency_stop(VOICE)
    foundation.events.publish(mail(expires_at=foundation.events._clock() + 86400))  # noqa: SLF001
    resume(foundation.permissions)
    tools = {t.name: t for t in foundation.tools()}
    listed = tools["missed_notifications"].run()
    assert listed["count"] == 1 and "Facture EDF" in tools["missed_notifications"].say(listed)
    result = tools["relaunch_missed"].run(position=1)
    assert result["status"] == "relaunched" and "Facture EDF" in said
    assert tools["relaunch_missed"].say(tools["relaunch_missed"].run(position=3)).startswith("Je ne trouve pas")


# --- Raccordements ------------------------------------------------------------------------------------------

def test_module_alerts_are_recorded_and_missed_during_emergency_stop():
    foundation = Foundation(Store())
    said = []
    router = AlertRouter(Store(), AlertSettings(quiet_start="00:00", quiet_end="00:00"), presence=lambda: True,
                         speak=lambda t: said.append(t) or True, permit=foundation.permissions.decide,
                         hub=foundation.events)
    assert router.deliver(Alert("watch", "RTX 5090", "Prix atteint.", origin="delegated:watch:w1")).delivered
    foundation.autonomy.emergency_stop(VOICE)
    assert not router.deliver(Alert("watch", "RTX 5090", "Encore.", key="k2", origin="delegated:watch:w1")).delivered
    outcomes = [e["decision"]["outcome"] for e in foundation.events.recent(5)]
    assert outcomes == [MISSED, ANNOUNCE] and said == ["Prix atteint."]
    assert [m["title"] for m in foundation.events.missed()] == ["RTX 5090"]


def test_notifications_go_through_the_hub_and_reminders_missed_on_emergency_stop():
    from orion.notifications import Notification, NotificationManager

    foundation = Foundation(Store())
    manager = NotificationManager()

    class Channel:
        name, sent = "test", []

        def accepts(self, n):
            return True

        def send(self, n):
            self.sent.append(n)

        def start(self):
            pass

        def stop(self):
            pass

    channel = Channel()
    manager.register(channel)
    manager.attach_hub(foundation.events)
    assert manager.notify(Notification("Rappel", "Sortir le linge.", "reminder")) == ["test"]
    assert channel.sent[0].metadata["principal"] == "delegated:reminder" and channel.sent[0].metadata["event_id"]
    foundation.autonomy.emergency_stop(VOICE)
    assert manager.notify(Notification("Rappel", "Appeler le garage.", "reminder")) == []
    assert manager.notify(Notification("Mail", "Nouveau mail.", "mail")) == []
    assert [m["title"] for m in foundation.events.missed()] == ["Rappel", "Mail"]
    assert len(channel.sent) == 1


def test_control_api_lists_relaunches_and_dismisses():
    foundation = Foundation(Store())
    foundation.events.channel("voix", lambda e: True)
    foundation.autonomy.emergency_stop(VOICE)
    first = foundation.events.publish(mail(expires_at=foundation.events._clock() + 86400)).event_id  # noqa: SLF001
    second = foundation.events.publish(reminder(ttl=1, clock=foundation.events._clock)).event_id  # noqa: SLF001
    resume(foundation.permissions)
    import time as _time

    _time.sleep(1.1)  # le second rappel devient obsolète
    status, missed = foundation.admin("GET", ["events", "missed"], {}, None, "s")
    assert status == 200 and {m["id"]: m["obsolete"] for m in missed} == {first: False, second: True}
    assert foundation.admin("POST", ["events", second, "relaunch"], {}, None, "s")[0] == 409
    assert foundation.admin("POST", ["events", first, "relaunch"], {}, None, "s")[0] == 200
    assert foundation.admin("POST", ["events", second, "dismiss"], {}, None, "s")[0] == 200
    events = foundation.admin("GET", ["events"], {}, None, "s")[1]["events"]
    assert any(e["type"] == "system.recap" for e in events)


# --- Maison vide : rappels gardés, présentés au retour (décision du 3 novembre 2026) ----------------------------

def test_a_reminder_in_an_empty_house_waits_for_your_return():
    hub, _, speaker, clock = hub_world()
    home = {"occupied": False}
    hub.presence = lambda: home["occupied"]
    out = hub.publish(reminder())
    assert out.outcome == "awaiting_return" and speaker.said == []
    clock.t += 600
    assert hub.dispatch() == 0 and speaker.said == []  # toujours personne : rien n'est dit
    home["occupied"] = True
    assert hub.on_return() == 1 and speaker.said == ["Sortir le linge"]
    assert hub.on_return() == 0 and speaker.said == ["Sortir le linge"]  # une seule fois


def test_an_obsolete_reminder_is_never_replayed_at_return_but_stays_consultable():
    hub, _, speaker, clock = hub_world()
    hub.presence = lambda: False
    hub.publish(reminder("Rendez-vous factice", ttl=1800))
    clock.t += 3600
    hub.presence = lambda: True
    assert hub.on_return() == 0 and speaker.said == []
    missed = hub.missed(include_obsolete=True)
    assert [m["title"] for m in missed] == ["Rendez-vous factice"] and missed[0]["obsolete"]


def test_unknown_presence_and_other_sources_keep_their_behaviour():
    hub, _, speaker, _ = hub_world()
    hub.presence = lambda: None  # inconnue : annoncé comme avant
    assert hub.publish(reminder()).outcome == ANNOUNCE
    hub.presence = lambda: False
    assert hub.publish(mail()).outcome == ANNOUNCE  # les mails suivent leurs propres règles
    assert speaker.said == ["Sortir le linge", "Facture EDF"]


def test_leaving_before_a_deferred_reminder_is_due_keeps_it_for_your_return():
    hub, _, speaker, clock = hub_world()
    hub.policy = InterruptionPolicy("22:30", "08:00")  # sans exemption : le rappel est différé par les heures calmes
    clock.t = NIGHT
    assert hub.publish(reminder(clock=clock, ttl=12 * 3600)).outcome == DEFERRED
    hub.presence = lambda: False
    clock.t = datetime(2026, 11, 4, 8, 5).timestamp()
    assert hub.dispatch() == 0 and speaker.said == []
    hub.presence = lambda: True
    assert hub.on_return() == 1 and speaker.said == ["Sortir le linge"]
