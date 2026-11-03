"""CORE FOUNDATION F2 : tâches persistantes (redémarrage, interruption brutale, double exécution, expiration, échecs,
permissions au moment de l'exécution, arrêt d'urgence prioritaire), distinction tâches programmées / initiatives,
confirmations liées à une demande, annonces vocales et veille soumises au moteur central, bulletins NEWS programmés,
signature préparée du registre. Données factices uniquement."""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.platform import Store  # noqa: E402
from orion.platform.alerts import Alert, AlertRouter, AlertSettings  # noqa: E402
from orion.platform.autonomy import CONFIRMATION_PHRASE, CONTROL, VOICE, AutonomyState  # noqa: E402
from orion.platform.build import classify_job  # noqa: E402
from orion.platform.foundation import Foundation  # noqa: E402
from orion.platform.ledger import ActionLedger, Keyring, entry_hash  # noqa: E402
from orion.platform.permissions import Capability, PermissionEngine  # noqa: E402
from orion.platform.scheduler import Scheduler  # noqa: E402
from orion.platform.tasks import (  # noqa: E402
    CANCEL_PENDING, CANCELLED, DONE, EXPIRED, FAILED, HELD, INTERRUPTED, MODULE, PENDING, RUNNING, USER, TaskError, TaskService,
)

NOW = 1_800_000_000.0
DAY = 86400.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Crash(BaseException):
    """Interruption brutale (processus tué) : rien ne l'attrape dans le service."""


def world(path=None, clock=None, instance=None):
    clock = clock or Clock()
    store = Store(path) if path else Store()
    engine = PermissionEngine(store, clock=clock)
    tasks = TaskService(store, engine, clock=clock, instance=instance)
    return SimpleNamespace(clock=clock, store=store, engine=engine, tasks=tasks, ran=[])


def activate(engine):
    engine.autonomy.change(AutonomyState.ACTIVE, CONTROL, confirmation=CONFIRMATION_PHRASE, session="control@test")


def back_to_suspended(engine):
    engine.autonomy.change(AutonomyState.SUSPENDED, CONTROL, confirmation=CONFIRMATION_PHRASE, session="control@test")


def note(w, label="ok"):
    def handler(run):
        w.ran.append((run.task_id, label))
        return {"fait": label}
    return handler


def actions(w):
    return [e["action"] for e in reversed(w.engine.ledger.list(500))]


# --- Persistance et redémarrage ------------------------------------------------------------------------------

def test_pending_tasks_survive_a_restart_and_run_once(tmp_path):
    first = world(tmp_path / "ops.db")
    first.tasks.register("rappel.dire", note(first), Capability.NOTIFY)
    one = first.tasks.schedule_for_user("rappel.dire", {"texte": "linge"}, by=VOICE, run_at=NOW + 60)
    daily = first.tasks.schedule_for_user("rappel.dire", {}, by=CONTROL, run_at=NOW + 120, interval=3600)
    del first  # arrêt d'ORION

    clock = Clock(NOW + 5 * 3600)  # redémarrage cinq heures plus tard
    again = world(tmp_path / "ops.db", clock)
    again.tasks.register("rappel.dire", note(again), Capability.NOTIFY)
    assert {t.id for t in again.tasks.list((PENDING,))} == {one.id, daily.id}
    assert sorted(again.tasks.run_due()) == sorted([(one.id, DONE), (daily.id, DONE)])
    assert again.tasks.get(one.id).state == DONE
    recurring = again.tasks.get(daily.id)
    assert recurring.state == PENDING and NOW + 5 * 3600 < recurring.run_at <= NOW + 6 * 3600  # pas de rafale
    assert again.tasks.run_due() == []  # rien en double
    assert len(again.ran) == 2


def test_creation_is_deduplicated_by_key():
    w = world()
    w.tasks.register("rappel.dire", note(w), Capability.NOTIFY)
    a = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE, key="demande-42")
    b = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE, key="demande-42")
    assert a.id == b.id and len(w.tasks.list()) == 1


# --- Interruption brutale ------------------------------------------------------------------------------------

def crash_world(path, idempotent):
    w = world(path, instance="avant")

    def crash(run):
        raise Crash()

    w.tasks.register("achat.passer", crash, Capability.WRITE_DATA, idempotent=idempotent, timeout=60)
    task = w.tasks.schedule_for_user("achat.passer", {"article": "x"}, by=VOICE)
    with pytest.raises(Crash):
        w.tasks.run_due()
    assert w.tasks.get(task.id).state == RUNNING  # processus tué en pleine exécution
    return task


def test_interrupted_non_idempotent_task_is_never_rerun_by_itself(tmp_path):
    task = crash_world(tmp_path / "ops.db", idempotent=False)
    clock = Clock(NOW + 30)
    again = world(tmp_path / "ops.db", clock, instance="après")
    again.tasks.register("achat.passer", note(again), Capability.WRITE_DATA, timeout=60)
    assert again.tasks.recover() == []  # bail encore valide : on n'y touche pas
    clock.t = NOW + 120
    assert again.tasks.recover() == [task.id]
    assert again.tasks.get(task.id).state == INTERRUPTED
    assert again.tasks.run_due() == [] and again.ran == []  # résultat inconnu : jamais relancée seule
    assert again.tasks.runs(task.id)[0]["outcome"] == "interrompue"
    assert f"task.recover:{task.id}" in actions(again)
    assert again.tasks.retry(task.id, by=CONTROL)  # vous seul la relancez
    assert again.tasks.run_due() == [(task.id, DONE)]


def test_interrupted_idempotent_task_resumes_after_its_lease(tmp_path):
    task = crash_world(tmp_path / "ops.db", idempotent=True)
    again = world(tmp_path / "ops.db", Clock(NOW + 120), instance="après")
    again.tasks.register("achat.passer", note(again), Capability.WRITE_DATA, idempotent=True, timeout=60)
    assert again.tasks.run_due() == [(task.id, DONE)]
    runs = again.tasks.runs(task.id)
    assert [r["outcome"] for r in runs] == ["interrompue", "réussie"] and runs[1]["attempt"] == 2


# --- Double exécution ----------------------------------------------------------------------------------------

def test_two_processes_never_run_the_same_task_twice(tmp_path):
    clock = Clock()
    a, b = world(tmp_path / "ops.db", clock, "a"), world(tmp_path / "ops.db", clock, "b")
    count, gate = [], threading.Barrier(2)

    def slow(run):
        count.append(run.run_key)
        time.sleep(0.2)

    for w in (a, b):
        w.tasks.register("rappel.dire", slow, Capability.NOTIFY)
    task = a.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    results = {}

    def run(name, w):
        gate.wait()
        results[name] = w.tasks.run_due()

    threads = [threading.Thread(target=run, args=(n, w)) for n, w in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert count == [f"{task.id}:1"]
    assert sorted(len(r) for r in results.values()) == [0, 1]
    assert a.tasks.run_due() == [] and b.tasks.run_due() == []


def test_a_stale_claim_cannot_be_reused():
    w = world()
    w.tasks.register("rappel.dire", note(w), Capability.NOTIFY)
    task = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    assert w.tasks.run_due() == [(task.id, DONE)]
    stale = task  # copie lue avant l'exécution
    assert w.tasks._claim(stale, NOW, w.tasks._types["rappel.dire"]) is False  # noqa: SLF001
    assert len(w.ran) == 1


# --- Expiration et échecs ------------------------------------------------------------------------------------

def test_expired_and_failed_tasks_are_handled_and_traced():
    w = world()
    w.tasks.register("rappel.dire", note(w), Capability.NOTIFY)

    def broken(run):
        raise ValueError("service injoignable")

    w.tasks.register("meteo.lire", broken, Capability.READ)
    late = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE, run_at=NOW + 120, expires_at=NOW + 60)
    flaky = w.tasks.schedule_for_user("meteo.lire", {}, by=VOICE, max_attempts=2)
    w.clock.t = NOW + 200
    assert w.tasks.run_due() == [(flaky.id, "retry")]
    assert w.tasks.get(late.id).state == EXPIRED and w.ran == []
    assert w.tasks.get(flaky.id).state == PENDING
    w.clock.t += 3600
    assert w.tasks.run_due() == [(flaky.id, FAILED)]
    failed = w.tasks.get(flaky.id)
    assert failed.state == FAILED and "service injoignable" in failed.last_error
    assert [r["outcome"] for r in w.tasks.runs(flaky.id)] == ["échec", "échec"]
    trail = actions(w)
    for expected in (f"task.create:{late.id}", f"task.expire:{late.id}", f"task.fail:{flaky.id}"):
        assert expected in trail
    assert w.engine.ledger.verify().ok


def test_cancelling_a_running_task_stops_it_at_its_next_checkpoint():
    w = world()
    effects = []

    def two_steps(run):
        effects.append("premier")
        w.tasks.cancel(run.task_id, by=CONTROL)
        assert w.tasks.get(run.task_id).state == CANCEL_PENDING  # demandée, pas encore faite
        run.checkpoint()
        effects.append("second")  # jamais atteint

    w.tasks.register("rappel.dire", two_steps, Capability.NOTIFY)
    task = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    assert w.tasks.run_due() == [(task.id, CANCELLED)]
    assert effects == ["premier"] and "point de contrôle" in w.tasks.get(task.id).last_error
    assert f"task.stopped:{task.id}" in actions(w)


def test_a_task_that_could_not_stop_is_reported_as_such():
    w = world()

    def no_checkpoint(run):
        w.tasks.cancel(run.task_id, by=CONTROL)
        assert [r["id"] for r in w.tasks.running()] == [run.task_id]  # elle tourne encore, et on le dit
        return {"fait": True}

    w.tasks.register("rappel.dire", no_checkpoint, Capability.NOTIFY)
    task = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    w.tasks.run_due()
    done = w.tasks.get(task.id)
    assert done.state == DONE and "n'a pas pu s'interrompre" in done.last_error  # jamais présentée comme arrêtée
    assert f"task.cancel_late:{task.id}" in actions(w)


# --- Permissions au moment de l'exécution --------------------------------------------------------------------

def test_revoking_a_grant_while_the_task_waits_blocks_it():
    w = world()
    activate(w.engine)
    grant = w.engine.grants.grant("delegated:paiement:*", Capability.SPEND, {"max_amount": 50}, NOW + DAY,
                                  granted_by=CONTROL)
    w.tasks.register("paiement.regler", note(w), Capability.SPEND)
    task = w.tasks.schedule_for_user("paiement.regler", {"amount": 20}, by=VOICE, run_at=NOW + 3600,
                                     grant_id=grant.id)
    other = w.engine.grants.grant("delegated:paiement:*", Capability.SPEND, {"max_amount": 50}, NOW + DAY,
                                  granted_by=CONTROL)
    w.engine.grants.revoke(grant.id, by=CONTROL)
    w.clock.t += 3600
    assert w.tasks.run_due() == [(task.id, FAILED)]
    assert w.ran == [] and "autorisation liée révoquée" in w.tasks.get(task.id).last_error
    assert other.id  # une autre autorisation valide ne se substitue pas à celle qui a été révoquée


def test_an_expired_grant_is_never_inherited():
    w = world()
    activate(w.engine)
    w.engine.grants.grant("delegated:paiement:*", Capability.SPEND, {"max_amount": 50}, NOW + 600,
                          granted_by=CONTROL)
    w.tasks.register("paiement.regler", note(w), Capability.SPEND)
    covered = w.tasks.schedule_for_user("paiement.regler", {"amount": 20}, by=VOICE)
    assert w.tasks.run_due() == [(covered.id, DONE)]  # autorisation valide au moment de l'exécution
    later = w.tasks.schedule_for_user("paiement.regler", {"amount": 20}, by=VOICE, run_at=NOW + 3600)
    w.clock.t += 3600  # l'autorisation a expiré pendant l'attente
    assert w.tasks.run_due() == [(later.id, HELD)]
    assert len(w.ran) == 1 and w.tasks.get(later.id).state == HELD


def test_held_task_runs_once_with_a_confirmation_bound_to_it():
    w = world()
    w.tasks.register("paiement.regler", note(w), Capability.SPEND)
    task = w.tasks.schedule_for_user("paiement.regler", {"amount": 30}, by=VOICE, interval=DAY)
    assert w.tasks.run_due() == [(task.id, HELD)]
    held = w.tasks.get(task.id)
    pending = w.engine.confirmations.pending()
    assert [p["id"] for p in pending] == [held.confirmation] and pending[0]["state"] == "en attente"
    assert w.tasks.run_due() == [] and w.ran == []
    assert w.tasks.confirm(task.id, by=CONTROL)
    assert w.tasks.run_due() == [(task.id, DONE)] and len(w.ran) == 1
    assert w.engine.confirmations.get(held.confirmation).used_at is not None
    w.clock.t += DAY  # occurrence suivante : la confirmation a servi une fois, il en faut une nouvelle
    assert w.tasks.run_due() == [(task.id, HELD)] and len(w.ran) == 1


def test_confirmation_is_single_use_bound_to_principal_action_params_and_expiry():
    w = world()
    store = w.engine.confirmations
    c = store.request("delegated:paiement:t1", "task:paiement.regler", {"amount": 30}, ttl=600)
    assert not store.consume(c.id, "delegated:paiement:t1", "task:paiement.regler", {"amount": 30})  # pas accordée
    with pytest.raises(PermissionError):
        store.approve(c.id, by="module:news")  # un module n'accorde jamais
    assert store.approve(c.id, by=VOICE)
    assert not store.consume(c.id, "delegated:paiement:t1", "task:paiement.regler", {"amount": 3000})
    assert not store.consume(c.id, "delegated:paiement:t2", "task:paiement.regler", {"amount": 30})
    assert not store.consume(c.id, "delegated:paiement:t1", "task:autre", {"amount": 30})
    assert store.consume(c.id, "delegated:paiement:t1", "task:paiement.regler", {"amount": 30})
    assert not store.consume(c.id, "delegated:paiement:t1", "task:paiement.regler", {"amount": 30})  # une fois
    late = store.request("delegated:paiement:t1", "task:paiement.regler", {}, ttl=60)
    store.approve(late.id, by=CONTROL)
    w.clock.t += 61
    assert not store.consume(late.id, "delegated:paiement:t1", "task:paiement.regler", {})
    text = str(w.engine.ledger.list(100))
    assert "3000" not in text  # seulement l'empreinte des paramètres


# --- Tâches programmées par vous / initiatives des modules ---------------------------------------------------

def test_user_tasks_and_module_initiatives_are_distinct_and_cannot_be_forged():
    w = world()
    w.tasks.register("news.digest", note(w, "digest"), Capability.NOTIFY, user=False, module=True)
    w.tasks.register("rappel.dire", note(w, "rappel"), Capability.NOTIFY)
    mine = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    initiative = w.tasks.gate("news").schedule("news.digest")
    assert (mine.origin, mine.principal) == (USER, f"delegated:rappel:{mine.id}")
    assert (initiative.origin, initiative.principal) == (MODULE, "module:news")
    with pytest.raises(PermissionError):
        w.tasks.schedule_for_user("rappel.dire", {}, by="module:news")  # un module ne se fait pas passer pour vous
    with pytest.raises(PermissionError):
        w.tasks.gate("watch").schedule("news.digest")  # ni pour un autre module
    with pytest.raises(PermissionError):
        w.tasks.gate("news").schedule("rappel.dire")
    with pytest.raises(TaskError):
        w.tasks.schedule_for_user("news.digest", {}, by=VOICE)
    with pytest.raises(TaskError):
        w.tasks.register("rappel.dire", note(w), Capability.READ)  # la capacité d'un type ne se redéclare pas
    assert not w.tasks.gate("watch").cancel(initiative.id)
    assert w.tasks.get(initiative.id).capability == "NOTIFY"


def test_suspension_holds_module_initiatives_without_consuming_attempts():
    w = world()
    w.tasks.register("news.digest", note(w, "digest"), Capability.NOTIFY, user=False, module=True)
    w.tasks.register("rappel.dire", note(w, "rappel"), Capability.NOTIFY)
    initiative = w.tasks.gate("news").schedule("news.digest")
    mine = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    assert w.engine.autonomy.state() is AutonomyState.SUSPENDED
    assert w.tasks.run_due() == [(mine.id, DONE)]
    held = w.tasks.get(initiative.id)
    assert held.state == PENDING and held.attempts == 0 and "autonomie suspendue" in held.last_error
    w.tasks.run_due()
    assert actions(w).count(f"task.hold:{initiative.id}") == 1  # une ligne par changement, pas par tour
    activate(w.engine)
    assert w.tasks.run_due() == [(initiative.id, DONE)]


# --- Arrêt d'urgence ----------------------------------------------------------------------------------------

def test_emergency_stop_during_a_queue_stops_the_rest_and_resumption_goes_through_suspended():
    w = world()
    activate(w.engine)

    def stop_everything(run):
        w.ran.append((run.task_id, "premier"))
        w.engine.autonomy.emergency_stop(VOICE, "essai")

    w.tasks.register("maison.premier", stop_everything, Capability.CONTROL_DEVICE)
    w.tasks.register("rappel.dire", note(w), Capability.NOTIFY)
    w.tasks.register("news.digest", note(w, "digest"), Capability.NOTIFY, user=False, module=True)
    first = w.tasks.schedule_for_user("maison.premier", {}, by=VOICE, run_at=NOW - 3)
    second = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE, run_at=NOW - 2)
    initiative = w.tasks.gate("news").schedule("news.digest", run_at=NOW - 1)
    assert w.tasks.run_due() == [(first.id, DONE)]
    assert w.tasks.get(second.id).state == PENDING and w.tasks.get(initiative.id).state == PENDING
    w.clock.t += 600
    assert w.tasks.run_due() == []  # même une tâche programmée par vous attend
    late = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)  # créée pendant l'arrêt : attend aussi
    assert w.tasks.run_due() == []
    with pytest.raises(PermissionError):
        w.engine.autonomy.change(AutonomyState.SUSPENDED, VOICE)  # la voix ne lève jamais l'arrêt
    back_to_suspended(w.engine)
    assert sorted(w.tasks.run_due()) == sorted([(second.id, DONE), (late.id, DONE)])
    assert w.tasks.get(initiative.id).state == PENDING  # suspendue : l'initiative attend encore
    activate(w.engine)
    assert w.tasks.run_due() == [(initiative.id, DONE)]


def test_unreadable_autonomy_state_runs_nothing(monkeypatch):
    w = world()
    w.tasks.register("rappel.dire", note(w), Capability.NOTIFY)
    w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    monkeypatch.setattr(w.engine.autonomy, "state", lambda: None)
    assert w.tasks.run_due() == [] and w.ran == []


def test_the_scheduler_holds_module_jobs_while_suspended_and_runs_programmed_ones():
    w = world()
    ran = []
    scheduler = Scheduler(clock=w.clock, halted=lambda: w.engine.suspended_for("delegated:scheduler"),
                          classify=classify_job, blocked=w.engine.blocked)
    scheduler.add("news:collect", 60, lambda: ran.append("collect"))  # lecture : continue
    scheduler.add("news:bulletins", 60, lambda: ran.append("bulletins"))  # vos heures réglées
    scheduler.add("watch:w1", 60, lambda: ran.append("watch"))  # votre surveillance
    scheduler.add("mystere:x", 60, lambda: ran.append("mystere"))  # inconnue : initiative qui notifie
    assert sorted(scheduler.tick()) == ["news:bulletins", "news:collect", "watch:w1"]
    assert "mystere" not in ran and {j["id"]: j["retenue"] for j in scheduler.status()}["mystere:x"]
    activate(w.engine)
    assert scheduler.tick() == ["mystere:x"]


# --- NEWS : bulletin programmé et initiative spontanée -------------------------------------------------------

def news_world(foundation, home=True, journal=False):
    from orion.news import NewsEngine, NewsSettings
    from tests.test_news import FEEDS, START, Channels, Clock as NewsClock, Web

    clock, web, channels, store = NewsClock(START), Web(), Channels(), Store()
    alerts = AlertRouter(store, AlertSettings(quiet_start="00:00", quiet_end="00:00", max_calls_per_day=10,
                                              min_call_interval_minutes=0.01),
                         presence=lambda: home, speak=channels.speak, call=channels.call, write=channels.write,
                         clock=clock, now=lambda: datetime.fromtimestamp(clock.t),
                         permit=foundation.permissions.decide)
    permits = foundation.permissions
    scheduler = Scheduler(clock, halted=lambda: permits.suspended_for("delegated:scheduler"), classify=classify_job,
                          blocked=permits.blocked)
    engine = NewsEngine(store, FEEDS, alerts=alerts, scheduler=scheduler, settings=NewsSettings(
        journal_enabled=journal), fetcher=web, clock=clock)  # journal : testé à part (lot P1)
    engine.start()
    return SimpleNamespace(clock=clock, web=web, channels=channels, alerts=alerts, scheduler=scheduler,
                           engine=engine)


def publish_grave(n):
    from tests.test_news import GRAVE, TWIN

    for feed, (title, summary) in (("https://afp.test/rss", GRAVE), ("https://lemonde.test/rss", TWIN)):
        n.web.feeds.setdefault(feed, []).append((title, f"{feed}/{abs(hash(title)) % 10**6}", summary,
                                                 n.clock.t - 600))


def test_a_scheduled_news_bulletin_runs_while_suspended_but_a_spontaneous_alert_is_refused():
    foundation = Foundation(Store())
    assert foundation.autonomy.state() is AutonomyState.SUSPENDED
    n = news_world(foundation)
    publish_grave(n)
    n.scheduler.tick()  # 7 h : collecte (lecture) ; l'alerte spontanée qui en sort est refusée
    spontaneous = [a for a in n.alerts.history(20, "news") if a["title"].startswith("Alerte")]
    assert spontaneous and spontaneous[0]["status"] == "bloquée"
    assert "autonomie suspendue" in str(spontaneous[0]["skipped"])
    assert n.channels.sent == []
    n.clock.t += 3600 + 60  # 8 h 01 : bulletin à l'heure que vous avez réglée
    assert "news:bulletins" in n.scheduler.tick()
    assert [kind for kind, _ in n.channels.sent] == ["voix"]
    bulletin = [a for a in n.alerts.history(20, "news") if a["title"].startswith("Bulletin")][0]
    assert bulletin["status"] == "envoyée"
    principals = {(e["principal"], e["decision"]) for e in foundation.ledger.list(100)
                  if e["action"] == "alert:news"}
    assert ("delegated:news:bulletins", "allow") in principals and ("module:news", "deny") in principals


def test_the_daily_journal_is_a_task_you_set_so_it_runs_while_suspended_and_stops_on_emergency():
    foundation = Foundation(Store())
    n = news_world(foundation, journal=True)
    publish_grave(n)
    n.scheduler.tick()
    n.clock.t += 3600 + 60  # 8 h 01 : journal de 07:30 rattrapé, bulletin de 8 h
    assert "news:journal" in n.scheduler.tick()
    journal = [a for a in n.alerts.history(20, "news") if a["title"].startswith("Journal")][0]
    assert journal["status"] == "envoyée"
    assert ("delegated:news:journal", "allow") in {(e["principal"], e["decision"]) for e in foundation.ledger.list(200)
                                                   if e["action"] == "alert:news"}
    other_foundation = Foundation(Store())
    other = news_world(other_foundation, journal=True)
    other_foundation.autonomy.emergency_stop(VOICE)
    other.clock.t += 3600 + 60
    assert other.scheduler.tick() == [] and other.channels.sent == []


def test_emergency_stop_also_stops_scheduled_news_bulletins():
    foundation = Foundation(Store())
    n = news_world(foundation)
    foundation.autonomy.emergency_stop(VOICE)
    n.clock.t += 3600 + 60
    assert n.scheduler.tick() == [] and n.channels.sent == []


def test_a_one_off_bulletin_you_schedule_is_a_persistent_task_that_runs_while_suspended():
    foundation = Foundation(Store())
    n = news_world(foundation)
    foundation.tasks.register("news.bulletin", lambda run: {"bulletin": n.engine.bulletin(run.params.get("slot"),
                                                                                          deliver=True,
                                                                                          origin=run.principal)},
                              Capability.NOTIFY)
    task = foundation.tasks.schedule_for_user("news.bulletin", {"slot": "18:00"}, by=VOICE)
    assert foundation.tasks.run_due() == [(task.id, DONE)]
    assert [kind for kind, _ in n.channels.sent] == ["voix"]
    assert any(e["principal"] == task.principal and e["action"] == "alert:news" and e["decision"] == "allow"
               for e in foundation.ledger.list(50))


def test_news_files_are_still_untouched():
    for path in (ROOT / "orion" / "news").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in ("permissions", "autonomy", "foundation", "running", "tasks"):
            assert word not in text, (path.name, word)


def test_an_alert_never_inherits_the_principal_of_the_task_that_triggered_it():
    from orion.platform.running import running_as

    with running_as("delegated:news:bulletins"):
        assert Alert("news", "t", "m").principal == "module:news"  # F3 : identité, jamais le contexte
        assert Alert("news", "t", "m", origin="delegated:watch:w1").principal == "delegated:watch:w1"


def test_a_spontaneous_news_alert_raised_during_a_scheduled_bulletin_stays_an_initiative():
    """Le bulletin programmé déclenche un traitement qui émet une alerte spontanée : elle reste « module:news » et
    est refusée pendant la suspension, le bulletin passe."""
    from tests.test_news import GRAVE

    foundation = Foundation(Store())
    n = news_world(foundation)
    publish_grave(n)
    original = n.engine.bulletin

    def bulletin_that_also_collects(*args, **kwargs):
        n.engine.collect()  # traitement déclenché par le bulletin : alerte spontanée possible
        return original(*args, **kwargs)

    n.engine.bulletin = bulletin_that_also_collects
    n.clock.t += 3600 + 60  # 8 h 01
    n.scheduler.remove("news:collect")
    n.scheduler.remove("news:journal")  # journal désactivé ici ; testé à part
    assert n.scheduler.tick() == ["news:bulletins"]
    history = {a["title"].split(" :")[0]: a["status"] for a in n.alerts.history(20, "news")}
    assert history["Alerte"] == "bloquée" and any(k.startswith("Bulletin") for k in history)
    assert [a["status"] for a in n.alerts.history(20, "news") if a["title"].startswith("Bulletin")] == ["envoyée"]
    assert GRAVE[0]


# --- Annonces vocales et veille -----------------------------------------------------------------------------

class Channel:
    name = "test"

    def __init__(self):
        self.sent = []

    def accepts(self, notification):
        return True

    def send(self, notification):
        self.sent.append(notification)

    def start(self):
        pass

    def stop(self):
        pass


def manager(foundation):
    from orion.notifications import NotificationManager

    m = NotificationManager()
    channel = Channel()
    m.register(channel)
    m.permit = foundation.permissions.decide
    return m, channel


def test_reminders_and_mail_announcements_go_through_the_engine():
    from orion.notifications import Notification

    foundation = Foundation(Store())
    m, channel = manager(foundation)
    assert m.notify(Notification("Rappel", "Sortir le linge.", "reminder")) == ["test"]  # programmé par vous
    assert m.notify(Notification("Mail", "Nouveau mail important.", "mail")) == ["test"]  # annonce réglée par vous
    assert m.notify(Notification("Échéance", "Facture à payer.", "mail",
                                 metadata={"principal": "module:mail"})) == []  # initiative : suspendue
    assert m.notify(Notification("X", "Y.", "inconnue")) == []  # source inconnue : initiative
    assert m.notify(Notification("X", "Y.", "inconnue", metadata={"principal": "user"})) == []  # jamais « user »
    assert channel.sent[0].metadata["principal"] == "delegated:reminder"
    foundation.autonomy.emergency_stop(VOICE)
    assert m.notify(Notification("Rappel", "Sortir le linge.", "reminder")) == []
    decisions = [(e["principal"], e["decision"]) for e in reversed(foundation.ledger.list(20))
                 if e["action"].startswith("notify:")]
    assert decisions == [("delegated:reminder", "allow"), ("delegated:mail", "allow"), ("module:mail", "deny"),
                         ("module:inconnue", "deny"), ("module:inconnue", "deny"), ("delegated:reminder", "deny")]


def test_without_the_foundation_notifications_are_unchanged():
    from orion.notifications import Notification, NotificationManager

    m = NotificationManager()
    channel = Channel()
    m.register(channel)
    assert m.notify(Notification("Échéance", "Facture.", "mail", metadata={"principal": "module:mail"})) == ["test"]


def test_emergency_stop_takes_priority_over_the_voice_queue():
    from orion.notifications import Notification, NotificationManager, VoiceNotificationChannel

    foundation = Foundation(Store())
    m = NotificationManager()
    voice = VoiceNotificationChannel()
    m.register(voice)
    m.permit = foundation.permissions.decide
    voice.still_allowed = lambda n: foundation.permissions.blocked(n.metadata["principal"], "NOTIFY")
    m.notify(Notification("Rappel", "Sortir le linge.", "reminder"))
    m.notify(Notification("Rappel", "Appeler le garage.", "reminder"))
    assert voice.pending() == 2
    foundation.autonomy.emergency_stop(VOICE)
    said = []
    voice.deliver(said.append)
    assert said == [] and voice.pending() == 0


def test_eve_watch_is_held_on_emergency_stop_and_published_after_resumption():
    from orion.events import EventBus
    from orion.scheduling.eve import UPCOMING, EveWatch

    foundation = Foundation(Store())
    bus, published = EventBus(), []
    bus.subscribe(UPCOMING, published.append)
    now = {"t": datetime(2026, 11, 2, 17, 59)}
    reminder = SimpleNamespace(id=7, message="Contrôle technique", expires_at=datetime(2026, 11, 3, 9, 0))
    eve = EveWatch(bus, SimpleNamespace(reminders=lambda: [reminder]), clock=lambda: now["t"])
    eve.permit = foundation.permissions.decide
    foundation.autonomy.emergency_stop(VOICE)
    now["t"] = datetime(2026, 11, 2, 18, 0, 30)
    assert eve.check() == 0 and published == []
    now["t"] = datetime(2026, 11, 2, 18, 5)
    assert eve.check() == 0
    back_to_suspended(foundation.permissions)
    now["t"] = datetime(2026, 11, 2, 18, 10)
    assert eve.check() == 1 and len(published) == 1
    assert eve.check() == 0  # une seule fois


# --- ORION Control ------------------------------------------------------------------------------------------

def test_control_api_lists_cancels_confirms_and_retries_tasks():
    foundation = Foundation(Store())
    ran = []
    foundation.tasks.register("paiement.regler", lambda run: ran.append(run.task_id), Capability.SPEND)
    task = foundation.tasks.schedule_for_user("paiement.regler", {"amount": 4321.987}, by=VOICE)
    foundation.tasks.run_due()
    status, body = foundation.admin("GET", ["tasks"], {}, None, "control@test")
    assert status == 200 and body["tasks"][0]["state"] == HELD and body["tasks"][0]["parameters"] == ["amount"]
    assert "4321" not in str(body["tasks"])  # aucune valeur de paramètre
    assert foundation.admin("GET", ["confirmations"], {}, None, "s")[1][0]["state"] == "en attente"
    assert foundation.admin("POST", ["tasks", task.id, "confirm"], {}, None, "s")[0] == 200
    foundation.tasks.run_due()
    assert ran == [task.id]
    detail = foundation.admin("GET", ["tasks", task.id], {}, None, "s")[1]
    assert [r["outcome"] for r in detail["runs"]] == ["en attente de confirmation", "réussie"]
    assert foundation.admin("POST", ["tasks", task.id, "cancel"], {}, None, "s")[0] == 409  # déjà faite
    assert foundation.admin("GET", ["tasks"], {"state": ["done"]}, None, "s")[1]["counts"] == {"done": 1}


def test_build_v3_classifies_jobs_and_registers_the_bulletin_task(tmp_path):
    from dataclasses import replace

    from orion.config import load_config
    from orion.platform.build import build_v3
    from orion.platform.foundation import build_foundation

    cfg = load_config(ROOT / "config.toml")
    cfg = replace(cfg, platform=replace(cfg.platform, enabled=True, database=tmp_path / "ops.db"),
                  modules=replace(cfg.modules, database=tmp_path / "v3.db"), news=replace(cfg.news, enabled=True,
                                                                                           feeds={}))
    foundation = build_foundation(cfg)
    modules = build_v3(cfg, foundation=foundation)
    try:
        assert "news.bulletin" in foundation.tasks.types()
        assert modules.scheduler.classify("news:bulletins") == ("delegated:news:bulletins", "NOTIFY")
        assert modules.scheduler.classify("news:collect") == ("module:news", "READ")
    finally:
        modules.stop()
        foundation.stop()


# --- Registre : limites du chaînage et signature préparée ---------------------------------------------------

KEY1, KEY2 = b"k" * 32, b"m" * 32


def rewrite_chain(path):
    """Ce que peut faire quelqu'un qui écrit dans la base : retirer les déclencheurs, modifier une ligne et
    recalculer toute la chaîne."""
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("DROP TRIGGER action_ledger_no_update")
    db.execute("UPDATE action_ledger SET decision = 'allow', reason = 'falsifié' WHERE id = 1")
    prev = "0" * 64
    for row in db.execute("SELECT * FROM action_ledger ORDER BY id").fetchall():
        digest = entry_hash(prev, dict(row))
        db.execute("UPDATE action_ledger SET prev_hash = ?, hash = ? WHERE id = ?", (prev, digest, row["id"]))
        prev = digest
    db.commit()
    db.close()


def test_hash_chain_alone_cannot_detect_a_full_rewrite_but_a_signature_does(tmp_path):
    path = tmp_path / "ops.db"
    plain = ActionLedger(Store(path))
    for i in range(3):
        plain.append("module:news", "NOTIFY", "alert:news", "deny", f"ligne {i}")
    rewrite_chain(path)
    assert ActionLedger(Store(path)).verify().ok  # limite documentée du chaînage seul

    signed_path = tmp_path / "signed.db"
    signed = ActionLedger(Store(signed_path), keyring=Keyring({"k1": KEY1}, "k1"))
    for i in range(3):
        signed.append("module:news", "NOTIFY", "alert:news", "deny", f"ligne {i}")
    assert signed.verify().ok
    rewrite_chain(signed_path)
    check = ActionLedger(Store(signed_path), keyring=Keyring({"k1": KEY1}, "k1")).verify()
    assert not check.ok and check.problem == "signature invalide" and check.broken_at == 1


def test_signature_rotation_unknown_keys_and_unsigned_rows(tmp_path):
    path = tmp_path / "ops.db"
    ActionLedger(Store(path)).append("user", "READ", "a", "allow")  # avant la signature : accepté
    ActionLedger(Store(path), keyring=Keyring({"k1": KEY1}, "k1")).append("user", "READ", "b", "allow")
    ActionLedger(Store(path), keyring=Keyring({"k1": KEY1, "k2": KEY2}, "k2")).append("user", "READ", "c", "allow")
    assert ActionLedger(Store(path), keyring=Keyring({"k1": KEY1, "k2": KEY2}, "k2")).verify().ok
    assert ActionLedger(Store(path), keyring=Keyring({"k2": KEY2}, "k2")).verify().problem == \
        "clé de signature inconnue"
    ActionLedger(Store(path)).append("user", "READ", "d", "allow")  # écrite sans clé après des lignes signées
    assert ActionLedger(Store(path), keyring=Keyring({"k1": KEY1, "k2": KEY2}, "k2")).verify().problem == \
        "ligne non signée après une ligne signée"
    with pytest.raises(ValueError):
        Keyring({"k": b"court"}, "k")
    assert "kkkk" not in repr(Keyring({"k1": KEY1}, "k1"))


def test_ledger_v1_database_is_migrated_without_touching_existing_rows(tmp_path):
    from orion.platform.ledger import MIGRATIONS

    path = tmp_path / "ops.db"
    store = Store(path)
    store.migrate("ledger", MIGRATIONS[:1])
    store.write("INSERT INTO action_ledger (at, principal, capability, action, decision, reason, request_id, detail, "
                "prev_hash, hash) VALUES (1.0, 'user', 'READ', 'a', 'allow', '', '', '{}', ?, ?)",
                ("0" * 64, entry_hash("0" * 64, {"at": 1.0, "principal": "user", "capability": "READ", "action": "a",
                                                  "decision": "allow", "reason": "", "request_id": "",
                                                  "detail": "{}"})))
    store.close()
    migrated = ActionLedger(Store(path))
    assert Store(path).version("ledger") == 2 and migrated.verify().ok
    migrated.append("user", "READ", "b", "allow")
    assert migrated.verify().count == 2


# --- F3 : annulation coopérative, effets extérieurs, bail renouvelé -----------------------------------------

def test_emergency_stop_during_a_running_task_is_reported_honestly_then_stops_it():
    foundation = Foundation(Store())
    started, release, steps = threading.Event(), threading.Event(), []

    def long_task(run):
        started.set()
        release.wait(5)
        run.checkpoint()  # l'arrêt d'urgence est vu ici
        steps.append("après le point de contrôle")

    foundation.tasks.register("maison.long", long_task, Capability.CONTROL_DEVICE)
    task = foundation.tasks.schedule_for_user("maison.long", {}, by=VOICE)
    worker = threading.Thread(target=foundation.tasks.run_due)
    worker.start()
    assert started.wait(5)
    stop = {t.name: t for t in foundation.tools()}["emergency_stop"]
    result = stop.run()
    assert result["still_running"] == 1
    assert "pas encore pu s'interrompre" in stop.say(result)  # jamais « tout est arrêté »
    assert foundation.tasks.get(task.id).state == CANCEL_PENDING
    release.set()
    worker.join(5)
    assert steps == [] and foundation.tasks.get(task.id).state == CANCELLED
    assert foundation.tasks.running() == []
    assert "Arrêt d'urgence enclenché" in stop.say(stop.run()) and "pas encore" not in stop.say(stop.run())


def test_permission_is_checked_again_before_each_external_effect():
    w = world()
    activate(w.engine)
    done = []

    def pay(run):
        run.effect(Capability.NOTIFY, action="paiement:prévenir")  # permis
        done.append("prévenu")
        run.effect(Capability.SPEND, {"amount": 40}, action="paiement:payer")  # sensible, non couvert
        done.append("payé")

    w.tasks.register("paiement.preparer", pay, Capability.WRITE_DATA)
    task = w.tasks.schedule_for_user("paiement.preparer", {}, by=VOICE, max_attempts=1)
    w.tasks.run_due()
    assert done == ["prévenu"] and w.tasks.get(task.id).state == FAILED
    assert "PermissionDenied" in w.tasks.get(task.id).last_error


def test_effect_is_refused_after_an_emergency_stop_mid_task():
    w = world()
    done = []

    def two_effects(run):
        run.effect(Capability.NOTIFY)
        done.append(1)
        w.engine.autonomy.emergency_stop(VOICE)
        run.effect(Capability.NOTIFY)
        done.append(2)

    w.tasks.register("rappel.dire", two_effects, Capability.NOTIFY)
    task = w.tasks.schedule_for_user("rappel.dire", {}, by=VOICE)
    w.tasks.run_due()
    assert done == [1] and w.tasks.get(task.id).state == CANCELLED


def test_heartbeat_keeps_a_long_task_from_being_taken_over(tmp_path):
    clock = Clock()
    a, b = world(tmp_path / "ops.db", clock, "a"), world(tmp_path / "ops.db", clock, "b")
    seen = {}

    def long_task(run):
        clock.t += 5  # bien au-delà du bail initial (1 s)
        time.sleep(1.0)  # le battement renouvelle le bail
        seen["reprises"] = b.tasks.recover()
        return {"ok": True}

    a.tasks.register("maison.long", long_task, Capability.CONTROL_DEVICE, timeout=1)
    b.tasks.register("maison.long", long_task, Capability.CONTROL_DEVICE, timeout=1)
    task = a.tasks.schedule_for_user("maison.long", {}, by=VOICE)
    assert a.tasks.run_due() == [(task.id, DONE)]
    assert seen["reprises"] == []  # encore active : jamais reprise par l'autre processus


def test_a_cancel_pending_task_of_a_dead_process_is_closed_as_unknown(tmp_path):
    w = world(tmp_path / "ops.db", instance="mort")

    def crash_after_cancel(run):
        w.tasks.cancel(run.task_id, by=CONTROL)
        raise Crash()

    w.tasks.register("maison.long", crash_after_cancel, Capability.CONTROL_DEVICE, timeout=60)
    task = w.tasks.schedule_for_user("maison.long", {}, by=VOICE)
    with pytest.raises(Crash):
        w.tasks.run_due()
    again = world(tmp_path / "ops.db", Clock(NOW + 120), instance="vivant")
    again.tasks.register("maison.long", note(again), Capability.CONTROL_DEVICE, timeout=60)
    assert again.tasks.recover() == [task.id]
    closed = again.tasks.get(task.id)
    assert closed.state == CANCELLED and "résultat inconnu" in closed.last_error and again.ran == []
