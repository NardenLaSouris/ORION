"""« C'est noté » dit pendant un appel d'une règle (orion/call/rules.py) : l'agent acquitte cet appel-là.

Régression (journal du 19 et du 28 octobre 2026) : l'appel décroché était repris par la conversation puis oublié
(``_in_call`` remis à None avant la première phrase) ; « C'est noté » partait au LLM, la règle finissait « décroché
sans accusé » et un résumé était envoyé par message.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from test_call import FakeCalls  # noqa: E402
from test_tools import PlannerLLM, routes, run_agent  # noqa: E402

from orion.agent import ACKED  # noqa: E402


class FakeRules:
    """Règles d'appel réduites à l'accusé : l'appel ``call_id`` attend « c'est noté »."""

    def __init__(self, call_id: str):
        self.call_id, self.acknowledged = call_id, []

    def awaiting_ack(self, call_id):
        return {"call": call_id} if call_id == self.call_id and not self.acknowledged else None

    def acknowledge(self, call_id=None):
        if call_id == self.call_id and not self.acknowledged:
            self.acknowledged.append(call_id)
            return True
        return False


def test_cest_note_during_a_rule_call_acknowledges_that_call_and_hangs_up():
    calls = FakeCalls(reason="Demain : rendez-vous chez le dentiste.", answers=3)
    rules = FakeRules(calls.call.id)
    llm = PlannerLLM(reply="Très bien, monsieur.")
    spoken, events = run_agent(["C'est noté.", "Autre chose ?"], llm, calls=calls, call_rules=rules)
    assert "Dites « c'est noté »" in spoken[0]  # la consigne est donnée au décroché
    assert rules.acknowledged == [calls.call.id]
    assert spoken[1] == ACKED
    assert "llm" not in routes(events)  # jamais une conversation libre à la place de l'accusé
    assert not any(kind == "user" and text == "Autre chose ?" for kind, text in events)  # l'appel se termine
    assert calls.finished == 1


def test_merci_orion_also_acknowledges_the_call():
    calls = FakeCalls(reason="Pensez à sortir les poubelles.", answers=3)
    rules = FakeRules(calls.call.id)
    spoken, _ = run_agent(["Merci Orion."], PlannerLLM(reply="Avec plaisir."), calls=calls, call_rules=rules)
    assert rules.acknowledged == [calls.call.id] and spoken[1] == ACKED


def test_unrelated_sentence_during_the_call_is_not_an_acknowledgement():
    calls = FakeCalls(reason="Pensez à sortir les poubelles.", answers=1)
    rules = FakeRules(calls.call.id)
    spoken, events = run_agent(["Quel temps fait-il ?"], PlannerLLM(reply="Il fait beau."), calls=calls,
                               call_rules=rules)
    assert rules.acknowledged == [] and ACKED not in spoken


def test_cest_note_outside_a_call_acknowledges_nothing():
    rules = FakeRules("aucun-appel")
    spoken, _ = run_agent(["C'est noté."], PlannerLLM(reply="Très bien."), call_rules=rules)
    assert rules.acknowledged == [] and ACKED not in spoken


# --- « C'est noté » mal transcrit au téléphone (journal des 28 octobre et 2 novembre 2026) ---------------------

QUESTION = "Pardon, je vous ai mal entendu. Est-ce noté ? Dites oui ou non."


@pytest.mark.parametrize("heard", ["C'est nous.", "et notez.", "Sénoté.", "Ces notés", "C'est noter."])
def test_close_mishearings_acknowledge_the_call(heard):
    calls = FakeCalls(reason="Pensez au test.", answers=3)
    rules = FakeRules(calls.call.id)
    spoken, events = run_agent([heard], PlannerLLM(reply="?"), calls=calls, call_rules=rules, confidence=-0.8)
    assert rules.acknowledged == [calls.call.id] and spoken[1] == ACKED
    assert "appel:accusé" in routes(events)


@pytest.mark.parametrize("heard", ["Synthesit.", "et stay nutty.", "Saluté."])
def test_unclear_short_reply_gets_a_yes_no_question_then_yes_acknowledges(heard):
    calls = FakeCalls(reason="Pensez au test.", answers=3)
    rules = FakeRules(calls.call.id)
    spoken, events = run_agent([heard, "Oui."], PlannerLLM(reply="?"), calls=calls, call_rules=rules, confidence=-0.96)
    assert spoken[1] == QUESTION and spoken[2] == ACKED
    assert rules.acknowledged == [calls.call.id]
    assert "llm" not in routes(events)  # jamais une réponse au hasard (« Je ne connais pas cette expression »)


def test_no_after_the_question_does_not_acknowledge():
    calls = FakeCalls(reason="Pensez au test.", answers=3)
    rules = FakeRules(calls.call.id)
    spoken, _ = run_agent(["Saluté.", "Non."], PlannerLLM(reply="?"), calls=calls, call_rules=rules, confidence=-0.96)
    assert spoken[1] == QUESTION and spoken[2] == "Très bien, monsieur. Je vous écoute."
    assert rules.acknowledged == []


def test_question_is_asked_only_once():
    calls = FakeCalls(reason="Pensez au test.", answers=4)
    rules = FakeRules(calls.call.id)
    spoken, _ = run_agent(["Saluté.", "Synthesit.", "Synthesit."], PlannerLLM(reply="Pardon ?"), calls=calls,
                          call_rules=rules, confidence=-0.96)
    assert spoken.count(QUESTION) == 2 and spoken[1] == QUESTION and spoken[2] != QUESTION  # jamais deux de suite
    assert rules.acknowledged == []


@pytest.mark.parametrize("heard", ["C'est pas noté.", "C'est nul.", "Non."])
def test_negations_and_other_words_never_acknowledge(heard):
    calls = FakeCalls(reason="Pensez au test.", answers=2)
    rules = FakeRules(calls.call.id)
    run_agent([heard], PlannerLLM(reply="Bien."), calls=calls, call_rules=rules, confidence=-0.3)
    assert rules.acknowledged == []


def test_clear_request_during_the_call_is_answered_normally():
    calls = FakeCalls(reason="Pensez au test.", answers=2)
    rules = FakeRules(calls.call.id)
    spoken, events = run_agent(["Quel temps fait-il ?"], PlannerLLM(reply="Il fait beau."), calls=calls,
                               call_rules=rules, confidence=-0.2)
    assert QUESTION not in spoken and rules.acknowledged == []


def test_whisper_expects_cest_note_only_while_the_call_awaits_it():
    calls = FakeCalls(reason="Pensez au test.", answers=3)
    rules = FakeRules(calls.call.id)
    seen = []
    run_agent(["Quel temps fait-il ?", "C'est noté."], PlannerLLM(reply="Il fait beau."), calls=calls,
              call_rules=rules, confidence=-0.2, hotwords_log=seen)
    assert seen == ["C'est noté., base", "C'est noté., base"]
    seen.clear()
    run_agent(["Quelle heure est-il ?"], PlannerLLM(reply="Midi."), hotwords_log=seen)  # hors appel : inchangé
    assert seen == ["base"]


def test_sounds_like_acknowledgement_thresholds():
    from orion.call.rules import sounds_like_acknowledgement as like
    assert like("C'est nous") and like("et notez") and like("c'est noté monsieur")
    assert not like("C'est nul") and not like("C'est bon") and not like("C'est pas noté")
    assert not like("Rappelle-moi plus tard") and not like("Salut") and not like("")


@pytest.mark.parametrize("heard", ["Steam. C'est noté.", "Bon ben c'est noté.", "Euh, c'est nous.", "Oui oui, et notez."])
def test_cest_note_at_the_end_after_noise_acknowledges_directly(heard):
    calls = FakeCalls(reason="Pensez au test.", answers=3)
    rules = FakeRules(calls.call.id)
    spoken, _ = run_agent([heard], PlannerLLM(reply="?"), calls=calls, call_rules=rules, confidence=-0.9)
    assert rules.acknowledged == [calls.call.id] and spoken[1] == ACKED  # sans question


def test_ending_rule_keeps_its_limits():
    from orion.call.rules import sounds_like_acknowledgement as like
    assert like("Steam. C'est noté.") and like("Bon ben c'est noté")
    assert not like("Steam. Ce n'est pas noté.") and not like("Non, c'est pas noté")
    assert not like("Je le ferai demain matin, c'est noté")  # six mots : une vraie phrase, pas un accusé
    assert not like("Rappelle-moi ce soir c'est nul") and not like("Quel temps fait-il demain")
