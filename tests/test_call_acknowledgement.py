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

from test_call import FakeCalls  # noqa: E402
from test_tools import PlannerLLM, routes, run_agent  # noqa: E402

ACKED = "C'est noté, monsieur. Je ne vous rappellerai pas à ce sujet."


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
