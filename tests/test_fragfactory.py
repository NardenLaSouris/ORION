"""Module Frag Factory d'ORION V3 (feature/fragfactory).

**Tests simulés** : Frag Factory est remplacé par ``FakeFragFactory``, qui reproduit son API d'intégration (file
d'événements, statuts, idempotence) en mémoire. Ils valident la logique d'ORION : synchronisation, notifications,
rapport, résolution des clips, confirmations. La chaîne réelle (Frag Factory → agent → module) est vérifiée par
``tests/test_fragfactory_e2e.py``, avec les vrais serveurs.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime

import pytest

from orion.fragfactory import protocol
from orion.fragfactory.agent_action import fragfactory_action
from orion.fragfactory.engine import FragFactoryModule, FragFactorySettings, Unavailable
from orion.fragfactory.link import LinkClient, LinkError
from orion.fragfactory.references import evidence, reference
from orion.fragfactory.tools import fragfactory_tools
from orion.platform.alerts import AlertRouter, AlertSettings
from orion.platform.scheduler import Scheduler
from orion.platform.store import Store
from orion.tools import ToolRegistry
from orion.tools.base import ToolError
from orion.tools.core import CONFIRM, DONE, ToolCore
from orion.tools.permissions import PermissionManager


class Clock:
    def __init__(self, t: float = datetime(2026, 11, 4, 3, 0).timestamp()):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Channels:
    def __init__(self):
        self.spoken, self.written = [], []

    def speak(self, text):
        self.spoken.append(text)
        return True

    def write(self, text):
        self.written.append(text)
        return True


def clip(cid, title, tier="pepite", status="PENDING_REVIEW", comedy=0.94, rendered=True):
    return {"id": cid, "title": title, "tier": tier, "tier_label": {"pepite": "PÉPITE", "bon": "BON CLIP",
                                                                      "a_trier": "À TRIER"}[tier],
            "status": status, "categories": ["teamkill", "comedy"], "category_labels": ["Teamkill", "Humour"],
            "scores": {"comedy": comedy}, "duration_s": 24.0, "summary": "Fou rire collectif (3 pistes)",
            "rendered": rendered, "versions": [{"version": 1}] if rendered else []}


class FakeFragFactory:
    """API d'intégration de Frag Factory, simulée (statuts, idempotence, file d'événements)."""

    def __init__(self):
        self.up = True
        self.events: list[dict] = []
        self.acked = 0
        self.clips: dict[int, dict] = {}
        self.decisions: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.reports: dict[str, dict] = {}
        self.versions: list[dict] = []

    def emit(self, type_, resource_id, payload):
        seq = len(self.events) + 1
        self.events.append({"seq": seq, "id": f"{seq:032x}", "type": type_, "occurred_at": "2026-11-04T03:00:00+00:00",
                            "resource": {"type": "clip", "id": str(resource_id)}, "schema_version": 1,
                            "correlation_id": f"clip:{resource_id}", "payload": payload})

    def ready(self, c, render_id=None, version=1):
        self.clips[c["id"]] = c
        self.emit("fragfactory.clip.ready_for_review", c["id"],
                  {**c, "clip_id": c["id"], "render_id": render_id or c["id"] * 10, "version": version})

    def __call__(self, op, args):
        protocol.validate(op, args)  # même contrôle que l'agent
        self.calls.append((op, dict(args)))
        if not self.up:
            raise ToolError("device_unreachable", "Le PC ne répond pas.")
        if op == "events":
            evs = [e for e in self.events if e["seq"] > args.get("after", 0) and e["seq"] > self.acked]
            return {"events": evs[:args.get("limit", 50)], "pending": len(evs)}
        if op == "ack":
            self.acked = max(self.acked, args["upto"])
            return {"acked": 1}
        if op == "status":
            return {"ok": True, "jobs": {"analyze:queued": 2}, "running": None,
                    "pending_reviews": sum(1 for c in self.clips.values() if c["status"] == "PENDING_REVIEW")}
        if op == "clips":
            items = list(self.clips.values())[::-1]
            if args.get("status"):
                items = [c for c in items if c["status"] == args["status"]]
            if args.get("tier"):
                items = [c for c in items if c["tier"] == args["tier"]]
            if args.get("q"):
                items = [c for c in items if all(w in c["title"].lower() for w in args["q"].split())]
            return {"items": items[:args.get("limit", 20)]}
        if op == "clip":
            if args["clip_id"] not in self.clips:
                raise ToolError("unknown_clip", "Clip introuvable")
            return self.clips[args["clip_id"]]
        if op == "decide":
            if args["request_id"] in self.decisions:
                return {**self.decisions[args["request_id"]], "replayed": True}
            c = self.clips[args["clip_id"]]
            if args.get("expected_status") and args["expected_status"] != c["status"]:
                raise ToolError("status_changed", "Le clip a changé entre-temps.")
            if c["status"] not in ("PENDING_REVIEW", "REVISION_REQUESTED"):
                raise ToolError("not_allowed", f"Impossible depuis le statut « {c['status']} »")
            c["status"] = "APPROVED" if args["action"] == "approve" else "REJECTED"
            self.decisions[args["request_id"]] = {"id": c["id"], "status": c["status"]}
            return self.decisions[args["request_id"]]
        if op == "revision":
            self.clips[args["clip_id"]]["status"] = "REVISION_REQUESTED"
            return {"status": "REVISION_REQUESTED", "automatic": ["end_later"], "job": 7}
        if op == "style":
            return {"rendered": True, "version": 1, "configured": {"effects": [
                {"effect": "zoom sur le pic", "value": "×1.22 pendant 1.2 s"}]},
                "measured": {"cuts_per_min": 4.0, "zoom_count": 1, "subtitles": {"presence": 0.8}},
                "comparison": {"available": True, "habitual": ["Zooms"], "missing": ["Sous-titres en majuscules"],
                               "unusual": []},
                "structure": {"why_selected": ["Je tue un coéquipier (TheJu → Mate)"]},
                "uncertainties": ["mesures estimées"]}
        if op == "recommend":
            return {"proposals": [{"number": 1, "title": "Version nerveuse", "intent": "nerveux",
                                   "rationale": "Recadrage serré.", "base_profile": "humour_naturel",
                                   "overrides": {"layout": "crop_killfeed"}, "target_duration_s": 15.0,
                                   "unsupported": [{"effect": "Transitions animées entre plans",
                                                    "alternative": "coupe des temps morts"}]}]}
        if op == "version":
            self.versions.append(args)
            self.clips[args["clip_id"]]["status"] = "PENDING_REVIEW"
            return {"job": 99, "replayed": False}
        if op == "report":
            key = args["request_key"]
            if key not in self.reports:
                day = key.removeprefix("orion-report-")
                self.reports[key] = {"id": len(self.reports) + 1, "period_start": args.get("since"),
                                     "period_end": f"{day}T07:00:00+00:00", "spoken": "Voici le bilan de Frag "
                                     "Factory pour cette nuit. 2 clip(s) détecté(s)."}
            return self.reports[key]
        if op == "reports":
            return {"items": list(self.reports.values())[-1:]}
        if op == "stats":
            return {"clips": 3, "tiers": {"pepite": 1, "bon": 1, "a_trier": 1}, "statuses": {"APPROVED": 1},
                    "funniest": {"title": "Le teamkill de trop"}, "profiles_used": {"humour_naturel": 2}}
        if op == "history":
            return {"items": [{"status": "done", "filename": "partie.mkv", "clips": 3}]}
        if op == "open_clip":
            return {"opened": True}
        raise AssertionError(op)


class World:
    def __init__(self, home=True, store=None, ff=None, clock=None, settings=None):
        self.clock = clock or Clock()
        self.store = store or Store()
        self.ff = ff or FakeFragFactory()
        self.channels = Channels()
        self.home = home
        self.alerts = AlertRouter(self.store, AlertSettings(quiet_start="00:00", quiet_end="00:00"),
                                  presence=lambda: self.home, speak=self.channels.speak, write=self.channels.write,
                                  clock=self.clock, now=lambda: datetime.fromtimestamp(self.clock.t))
        self.module = FragFactoryModule(self.store, self.ff, alerts=self.alerts, scheduler=Scheduler(self.clock),
                                        settings=settings or FragFactorySettings(), clock=self.clock,
                                        now=lambda: datetime.fromtimestamp(self.clock.t))
        self.registry = ToolRegistry()
        for tool in fragfactory_tools(self.module):
            self.registry.register(tool)
        self.core = ToolCore(self.registry, PermissionManager())

    def ask(self, tool, text, **params):
        """Demande comme le ferait l'agent : paramètres masqués lus dans les mots de la demande."""
        data = {"tool": tool, "parameters": dict(params)}
        for key, p in self.registry.get(tool).parameters.items():
            if p.hidden and p.resolve is not None:
                value = p.resolve(text)
                if value is not None:
                    data["parameters"][key] = value
        return self.core.submit(data)


# --- références : le clip est lu dans les mots ---------------------------------------------------------------------
@pytest.mark.parametrize("text, ref", [
    ("ORION, valide le dernier clip.", "latest"), ("Accepte la dernière pépite.", "latest:pepite"),
    ("Refuse le clip du teamkill.", "search:teamkill"), ("Fais-moi revoir le deuxième clip.", "ordinal:2"),
    ("Valide le clip numéro 42", "id:42"), ("Valide-le.", "context"),
    ("Montre-moi les clips qui attendent ma validation.", None), ("Combien de clips sont en attente ?", None)])
def test_clip_reference_is_read_from_the_words(text, ref):
    assert reference(text) == ref


def test_an_identifier_proposed_by_the_model_is_rejected():
    assert evidence("id:42", "Valide le clip numéro 42")
    assert not evidence("id:99", "Valide le dernier clip")  # le LLM ne peut pas viser un autre clip


# --- 1 à 7 : communication -----------------------------------------------------------------------------------------
def test_1_connection_and_status():
    w = World()
    w.ff.clips[1] = clip(1, "Le teamkill de trop")
    assert "1 clip(s) à valider" in w.ask("fragfactory_status", "état de frag factory").result.result["text"]


def test_2_invalid_authentication_is_reported_not_hidden():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(json.dumps({"detail": "Jeton de liaison refusé"}).encode()))
    client = LinkClient(token="x" * 40, opener=opener)
    with pytest.raises(LinkError) as exc:
        client.call("status")
    assert exc.value.http == 401 and "jeton" in exc.value.message.lower()


def test_2b_agent_relay_refuses_unknown_operations_and_free_paths():
    action = fragfactory_action(LinkClient(token="x" * 40, opener=lambda *a, **k: None))
    with pytest.raises(ToolError):
        action.run(op="status", args={"path": "/../../etc"})
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("delete_everything", {})
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("version", {"clip_id": 1, "request_id": "abcdefgh", "overrides": {"music": True}})
    with pytest.raises(ValueError):
        LinkClient("http://192.168.1.50:8742", token="x" * 40)  # Frag Factory doit être sur le PC de l'agent


def test_3_orion_stopped_then_back_gets_everything_in_order():
    ff = FakeFragFactory()
    ff.ready(clip(1, "Premier"))
    ff.ready(clip(2, "Second", tier="bon"))
    w = World(ff=ff)  # ORION démarre après coup : Frag Factory a gardé ses événements
    summary = w.module.sync()
    assert summary["received"] == 2 and ff.acked == 2
    assert "2 nouveaux clips" in w.channels.spoken[0]


def test_4_frag_factory_unavailable_is_reported_and_nothing_is_lost():
    w = World()
    w.ff.ready(clip(1, "Premier"))
    w.ff.up = False
    with pytest.raises(Unavailable):
        w.module.sync()
    assert w.module.status()["connected"] is False
    outcome = w.ask("fragfactory_status", "état de frag factory")
    assert outcome.result.success is False and outcome.result.error == "fragfactory_unavailable"
    assert w.ff.acked == 0  # rien d'acquitté : rien de perdu


def test_5_reconnection_after_a_failure():
    w = World()
    w.ff.ready(clip(1, "Premier"))
    w.ff.up = False
    sched = Scheduler(w.clock)
    sched.add("fragfactory:sync", 30, w.module.sync)
    sched.tick(w.clock.t)
    job = sched.status()[0]
    assert job["failures"] == 1  # attente doublée par le planificateur : nouvel essai plus tard
    w.ff.up = True
    w.clock.t += 3600
    sched.tick(w.clock.t)
    assert w.ff.acked == 1 and w.module.status()["connected"] is True


def test_6_pending_events_are_recovered_after_an_orion_restart():
    store, ff = Store(), FakeFragFactory()
    w1 = World(store=store, ff=ff)
    ff.ready(clip(1, "Premier"))
    w1.module.sync()
    ff.ready(clip(2, "Second"))
    w2 = World(store=store, ff=ff)  # redémarrage : même base, même curseur
    assert w2.module.sync()["received"] == 1 and w2.module.status()["cursor"] == 2


def test_7_duplicate_events_are_never_announced_twice():
    w = World()
    w.ff.ready(clip(1, "Premier"))
    w.module.sync()
    w.ff.acked = 0  # Frag Factory renvoie le même événement (accusé perdu)
    w.module.sync()
    assert len(w.channels.spoken) == 1


# --- 8 à 13 : notifications ------------------------------------------------------------------------------------------
def test_8_clip_detected_is_kept_without_announcement():
    w = World()
    w.ff.emit("fragfactory.clip.detected", 1, {"title": "Détecté"})
    w.module.sync()
    assert w.channels.spoken == [] and w.channels.written == []
    assert w.module.admin("GET", ["events"], {}, None)[1][0]["type"] == "fragfactory.clip.detected"


def test_9_10_render_completed_notifies_with_real_data():
    w = World()
    w.ff.ready(clip(1, "Le teamkill de trop"))
    w.module.sync()
    text = w.channels.spoken[0]
    for part in ("Titre : Le teamkill de trop", "Catégories : Teamkill, Humour", "Durée : 24 secondes",
                 "Classement : PÉPITE", "Score humoristique : 94/100", "Fou rire collectif",
                 "disponible pour validation"):
        assert part in text
    assert w.alerts.history(10, "fragfactory")[0]["level"] == "normale"


def test_11_voice_only_when_enabled_otherwise_written():
    w = World()
    w.module.update_settings({"voice_enabled": False})
    w.ff.ready(clip(1, "Premier"))
    w.module.sync()
    assert w.channels.spoken == [] and len(w.channels.written) == 1


def test_11b_quiet_hours_are_respected():
    w = World()
    w.alerts = AlertRouter(w.store, AlertSettings(quiet_start="00:00", quiet_end="23:59"), presence=lambda: True,
                           speak=w.channels.speak, write=w.channels.write, clock=w.clock,
                           now=lambda: datetime.fromtimestamp(w.clock.t))
    w.module._alerts = w.alerts  # noqa: SLF001
    w.ff.ready(clip(1, "Premier"))
    w.module.sync()
    assert w.channels.spoken == []  # heures calmes : jamais de voix


def test_12_several_clips_are_announced_together_not_simultaneously():
    w = World()
    for i in range(5):
        w.ff.ready(clip(i + 1, f"Clip {i + 1}", tier="pepite" if i < 2 else "bon"))
    w.module.sync()
    assert len(w.channels.spoken) == 1
    text = w.channels.spoken[0]
    assert "5 nouveaux clips" in text and "2 pépites" in text and "et 2 autre(s)" in text


def test_12b_low_tiers_are_kept_silently_by_default():
    w = World()
    w.ff.ready(clip(1, "À trier", tier="a_trier"))
    w.module.sync()
    assert w.channels.spoken == []


def test_13_notification_recovered_after_restart_is_announced_once():
    store, ff = Store(), FakeFragFactory()
    ff.ready(clip(1, "Premier"))
    w = World(store=store, ff=ff)
    w.ff.up = False
    with pytest.raises(Unavailable):
        w.module.sync()
    w2 = World(store=store, ff=ff)
    ff.up = True
    w2.module.sync()
    w2.module.sync()
    assert len(w2.channels.spoken) == 1


# --- 14 à 21 : validation ----------------------------------------------------------------------------------------------
def test_14_presentation_of_a_clip():
    w = World()
    w.ff.clips[3] = clip(3, "Le teamkill de trop")
    out = w.ask("fragfactory_get_clip", "parle-moi du clip numéro 3")
    assert out.status == DONE and "Le teamkill de trop" in out.result.result["text"]


def test_15_playing_a_clip_opens_it_on_the_pc():
    w = World()
    w.ff.clips[3] = clip(3, "Le teamkill de trop")
    out = w.ask("fragfactory_play_clip", "montre-moi le clip numéro 3")
    assert out.status == DONE and ("open_clip", {"clip_id": 3}) in w.ff.calls


def test_16_explicit_approval_names_the_clip_and_is_confirmed():
    w = World()
    w.ff.ready(clip(1, "Ancien"))
    w.ff.ready(clip(2, "Le teamkill de trop"))
    w.module.sync()
    out = w.ask("fragfactory_approve_clip", "ORION, valide le dernier clip.")
    assert out.status == CONFIRM and "Le teamkill de trop" in out.question and "clip 2" in out.question
    assert w.ff.clips[2]["status"] == "PENDING_REVIEW"  # rien n'est fait avant votre réponse
    done = w.core.answer("oui")
    assert done.status == DONE and w.ff.clips[2]["status"] == "APPROVED"
    assert "pas publié" in done.result.result["text"]


def test_16b_a_new_clip_arriving_during_the_question_does_not_change_the_target():
    w = World()
    w.ff.ready(clip(1, "Celui annoncé"))
    out = w.ask("fragfactory_approve_clip", "valide le dernier clip")
    w.ff.ready(clip(2, "Arrivé pendant la question"))
    w.core.answer("oui")
    assert w.ff.clips[1]["status"] == "APPROVED" and w.ff.clips[2]["status"] == "PENDING_REVIEW"
    assert out.status == CONFIRM


def test_17_explicit_rejection():
    w = World()
    w.ff.clips[5] = clip(5, "Le clip du teamkill raté")
    out = w.ask("fragfactory_reject_clip", "Refuse le clip du teamkill.")
    assert out.status == CONFIRM
    assert w.core.answer("oui").status == DONE and w.ff.clips[5]["status"] == "REJECTED"


def test_18_revision_request():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    out = w.ask("fragfactory_request_revision", "il manque la réaction à la fin du clip numéro 5")
    assert out.status == CONFIRM
    assert w.core.answer("oui").status == DONE and w.ff.clips[5]["status"] == "REVISION_REQUESTED"
    revision = next(args for op, args in w.ff.calls if op == "revision")
    assert "end_later" in revision["items"]


def test_19_an_ambiguous_answer_never_validates():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    w.ask("fragfactory_approve_clip", "valide le clip numéro 5")
    again = w.core.answer("euh peut-être")
    assert again is None or again.status == CONFIRM
    assert w.ff.clips[5]["status"] == "PENDING_REVIEW"
    assert not any(op == "decide" for op, _ in w.ff.calls)


def test_19b_an_ambiguous_reference_asks_which_one():
    w = World()
    w.ff.clips[1] = clip(1, "Teamkill sur Mirage")
    w.ff.clips[2] = clip(2, "Teamkill sur Inferno")
    out = w.ask("fragfactory_approve_clip", "valide le clip du teamkill")
    assert out.result.success is False and out.result.error == "ambiguous" and "Lequel" in out.result.message
    assert not any(op == "decide" for op, _ in w.ff.calls)
    out2 = w.ask("fragfactory_approve_clip", "valide le deuxième clip")  # la liste vient d'être présentée
    # la liste dite était « 1. Inferno ; 2. Mirage » (plus récent d'abord) : le deuxième est Mirage
    assert "1. Teamkill sur Inferno" in out.result.message
    assert out2.status == CONFIRM and "Mirage" in out2.question and "clip 1" in out2.question


def test_20_an_already_approved_clip_is_not_validated_again():
    w = World()
    w.ff.clips[5] = clip(5, "Déjà validé", status="APPROVED")
    out = w.ask("fragfactory_approve_clip", "valide le clip numéro 5")
    # refusé tout de suite, sans question de confirmation inutile
    assert out.result.success is False and out.result.error == "already_decided" and "déjà validé" in out.result.message
    assert not any(op == "decide" for op, _ in w.ff.calls)


def test_20b_status_changed_between_question_and_answer_is_refused():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    assert w.ask("fragfactory_approve_clip", "valide le clip numéro 5").status == CONFIRM
    w.ff.clips[5]["status"] = "REJECTED"  # décidé ailleurs (interface Frag Factory) pendant la question
    result = w.core.answer("oui").result
    assert result.success is False  # une erreur n'est jamais présentée comme une validation
    assert w.ff.clips[5]["status"] == "REJECTED"


def test_21_unknown_identifier():
    w = World()
    out = w.ask("fragfactory_approve_clip", "valide le clip numéro 999")
    assert out.result.success is False and out.result.error == "not_found"


def test_communication_error_is_never_a_validation():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    w.ask("fragfactory_approve_clip", "valide le clip numéro 5")
    w.ff.up = False
    result = w.core.answer("oui").result
    assert result.success is False and result.error == "fragfactory_unavailable"
    assert w.module.admin("GET", ["actions"], {}, None)[1][0]["outcome"] == "injoignable"


# --- 22 à 27 : style -------------------------------------------------------------------------------------------------------
def test_22_23_24_style_analysis_description_and_comparison():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    text = w.ask("fragfactory_analyze_clip_style", "quel est le style de montage du clip numéro 5 ?").result.result["text"]
    assert "zoom sur le pic" in text and "Comme vos références : Zooms" in text
    assert "Absent par rapport à vos habitudes : Sous-titres en majuscules" in text and "estimations" in text


def test_25_proposal_never_renders_by_itself():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    text = w.ask("fragfactory_recommend_editing_style", "propose un montage plus nerveux pour le clip numéro 5")\
        .result.result["text"]
    assert "Version nerveuse" in text and "non disponible" in text
    assert not any(op == "version" for op, _ in w.ff.calls)
    assert ("recommend", {"clip_id": 5, "intent": "nerveux"}) in w.ff.calls


def test_26_27_applying_a_proposal_creates_a_new_version_after_confirmation():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop", status="APPROVED")
    w.ask("fragfactory_recommend_editing_style", "propose un montage plus nerveux pour le clip numéro 5")
    out = w.ask("fragfactory_request_render", "produis la version 1 du clip numéro 5", proposition=1)
    assert out.status == CONFIRM and "Version nerveuse" in out.question
    assert w.core.answer("oui").status == DONE
    assert w.ff.versions[0]["overrides"] == {"layout": "crop_killfeed"}
    assert w.ff.clips[5]["status"] == "PENDING_REVIEW"  # la nouvelle version revient en validation


def test_render_without_a_prior_proposal_is_refused():
    w = World()
    w.ff.clips[5] = clip(5, "Le teamkill de trop")
    out = w.ask("fragfactory_request_render", "produis la version 1 du clip numéro 5", proposition=1)
    assert out.status == CONFIRM
    assert w.core.answer("oui").result.error == "no_proposal"


# --- 28 à 32 : rapports ---------------------------------------------------------------------------------------------------
def test_28_29_report_is_generated_after_8am_with_frag_factory_numbers():
    w = World()
    w.clock.t = datetime(2026, 11, 4, 7, 59).timestamp()
    assert w.module.report_due() is None
    w.clock.t = datetime(2026, 11, 4, 8, 0).timestamp()
    out = w.module.report_due()
    assert out["day"] == "2026-11-04" and "2 clip(s) détecté(s)" in w.channels.spoken[-1]
    assert w.channels.spoken[-1].startswith("Bonjour Monsieur.")


def test_30_no_duplicate_report():
    w = World()
    w.clock.t = datetime(2026, 11, 4, 8, 0).timestamp()
    w.module.report_due()
    w.clock.t += 600
    assert w.module.report_due() is None and len(w.channels.spoken) == 1


def test_31_report_caught_up_after_orion_was_stopped_and_periods_chain():
    w = World()
    w.clock.t = datetime(2026, 11, 4, 14, 30).timestamp()  # ORION arrêté à 8 h, redémarré l'après-midi
    assert w.module.report_due()["day"] == "2026-11-04"
    w.clock.t = datetime(2026, 11, 5, 8, 1).timestamp()
    w.module.report_due()
    second = [a for op, a in w.ff.calls if op == "report"][-1]
    assert second["since"] == "2026-11-04T07:00:00+00:00"  # pas de trou, pas de chevauchement


def test_32_report_retried_when_frag_factory_was_down():
    w = World()
    w.clock.t = datetime(2026, 11, 4, 8, 0).timestamp()
    w.ff.up = False
    with pytest.raises(Unavailable):
        w.module.report_due()
    w.ff.up = True
    w.clock.t += 120
    assert w.module.report_due()["day"] == "2026-11-04"


def test_spoken_report_on_demand():
    w = World()
    w.ff("report", {"request_key": "orion-report-2026-11-04"})
    text = w.ask("fragfactory_get_nightly_report", "fais-moi le bilan de la nuit dernière").result.result["text"]
    assert "bilan de Frag Factory" in text


def test_settings_are_validated_and_persisted():
    store = Store()
    w = World(store=store)
    w.module.update_settings({"report_time": "07:15", "notify_tiers": ["pepite"]})
    with pytest.raises(ValueError):
        w.module.update_settings({"report_time": "25:00"})
    assert World(store=store).module.settings.report_time == "07:15"


def test_publication_is_never_a_voice_tool():
    names = {t.name for t in fragfactory_tools(World().module)}
    assert len(names) == 14 and not any("publish" in n for n in names)
