"""Outils montrés au planificateur LLM (6 novembre 2026).

Le Katana sert le modèle avec 4 096 jetons par demande (OLLAMA_NUM_PARALLEL=2 sur 8 192) ; le prompt complet des
141 outils (environ 7 300 jetons) était tronqué par le début, sans avertissement : présentation et première moitié
des outils perdues. Le planificateur ne reçoit plus que les outils en rapport avec la demande, dans un budget qui
tient ; les vérifications, elles, portent toujours sur tous les outils."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orion.tools.base import Param, Risk, Tool  # noqa: E402
from orion.tools.planner import PROMPT_BUDGET, plan, planner_prompt, prompt_tokens, scoped_registry  # noqa: E402
from orion.tools.registry import ToolRegistry  # noqa: E402

from test_tools import make_core  # noqa: E402

DOMAINS = {
    "watch": "Surveillance de prix ou d'événement : crée, liste, met en pause ou supprime une surveillance de produit",
    "news": "Actualités : bulletin, journal du matin, détails sur un sujet de l'actualité suivie par les sources",
    "mail": "Courrier électronique : lit, résume, archive ou supprime un mail de la boîte de réception",
    "agenda": "Agenda et rendez-vous : événements du jour, de la semaine, créneaux libres dans le calendrier",
    "missions": "Missions personnelles : plan en plusieurs étapes, progression, validation et annulation",
    "call": "Règles d'appel téléphonique : crée, décrit, active ou supprime une règle pour les appels entrants",
}


def tool(name: str, description: str, **params: str) -> Tool:
    return Tool(name, description, {k: Param(str, v, required=False) for k, v in params.items()}, {"text": "réponse"},
                Risk.SAFE, lambda **_: {"text": "ok"})


def big_registry() -> ToolRegistry:
    """Registre de la taille de celui en service : outils réels du PC, Discord, lumières, puis ~120 outils de modules."""
    registry = make_core().registry
    for t in (tool("discord_join", "Discord : rejoint le salon vocal nommé (change de salon si besoin).",
                   salon="nom du salon vocal tel qu'il a été dit", serveur="nom du serveur, seulement s'il est dit"),
              tool("discord_mute", "Discord : coupe mon micro (mute)."),
              tool("light_on", "Allume les lumières d'une pièce (ou toutes).", room="pièce nommée"),
              tool("set_scene", "Lumières : met une ambiance (lecture, nuit, détente) dans une pièce.",
                   scene="nom de l'ambiance", room="pièce nommée"),
              tool("set_brightness", "Lumières : règle la luminosité en pourcentage.", brightness="niveau dit")):
        registry.register(t)
    for domain, description in DOMAINS.items():
        for i in range(20):
            registry.register(tool(f"{domain}_action_{i}", f"{description} (variante {i}).",
                                   valeur=f"valeur dite par l'utilisateur pour {domain}",
                                   precision="précision facultative, seulement si elle est dite"))
    return registry


def names(registry: ToolRegistry) -> set[str]:
    return {t.name for t in registry.list()}


def test_the_full_registry_is_over_the_budget_and_each_request_gets_a_prompt_that_fits():
    registry = big_registry()
    assert prompt_tokens(registry) > 2 * PROMPT_BUDGET  # comme en service : le prompt complet serait tronqué
    for text in ("Connecte moi en vocal sur le Zombie", "On n'y voit rien ici", "Lance Fortnite",
                 "Qu'est-ce que j'ai à l'agenda demain ?", "Mets l'ambiance lecture dans la chambre"):
        scoped = scoped_registry(registry, text)
        assert 0 < len(scoped) < len(registry) and prompt_tokens(scoped) <= PROMPT_BUDGET, text


def test_the_tools_of_the_request_are_always_kept():
    registry = big_registry()
    assert "discord_join" in names(scoped_registry(registry, "Connecte moi en vocal sur le Zombie"))
    assert "light_on" in names(scoped_registry(registry, "On n'y voit rien ici"))  # demande indirecte
    assert "open_application" in names(scoped_registry(registry, "Lance Fortnite"))
    assert "set_scene" in names(scoped_registry(registry, "Mets l'ambiance lecture dans la chambre"))
    assert "set_volume" in names(scoped_registry(registry, "C'est beaucoup trop fort"))
    after = scoped_registry(registry, "Et à 20 % ?", previous="Mets la luminosité de la chambre à 50 %")
    assert "set_brightness" in names(after)  # suite d'une demande : la précédente compte aussi


def test_an_unrelated_request_shows_no_light_or_sound_action():
    """5 novembre 2026 : « Surnomme le serveur… » (non reconnu) devenu « ambiance lecture » sur toutes les lumières."""
    registry = big_registry()
    scoped = names(scoped_registry(registry, "Surnomme le machin des copains en bidule pour faire simple"))
    assert not scoped & {"set_scene", "light_on", "set_brightness", "set_volume", "mute_volume"}


def test_a_small_registry_is_shown_whole_and_the_prompt_is_unchanged():
    registry = make_core().registry
    assert scoped_registry(registry, "bonjour") is registry
    assert "set_volume" in planner_prompt(scoped_registry(registry, "bonjour"))


class SchemaLLM:
    def __init__(self, answer):
        self.answer, self.schemas, self.prompts = answer, [], []

    def chat_json(self, messages, schema):
        self.schemas.append(schema)
        self.prompts.append(messages[0].content)
        return self.answer


def test_the_llm_sees_only_the_scoped_tools_and_the_core_still_checks_the_proposal():
    registry = big_registry()
    llm = SchemaLLM({"type": "tool_call", "tool": "discord_join", "parameters": {"salon": "général"}})
    data = plan(llm, "Connecte moi en vocal dans le salon général", registry)
    assert data == {"type": "tool_call", "tool": "discord_join", "parameters": {"salon": "général"}}
    offered = {o["properties"]["tool"]["const"] for o in llm.schemas[0]["anyOf"][1:-1]}
    assert "discord_join" in offered and "watch_action_3" not in offered and "set_scene" not in offered
    assert len(llm.prompts[0]) / 3.4 <= PROMPT_BUDGET
    outside = SchemaLLM({"type": "tool_call", "tool": "watch_action_3", "parameters": {}})
    assert plan(outside, "Connecte moi en vocal dans le salon général", registry) is None  # outil non montré


def test_nothing_related_means_no_call_to_the_llm():
    registry = big_registry()
    llm = SchemaLLM({"type": "tool_call", "tool": "set_scene", "parameters": {"scene": "lecture"}})
    assert plan(llm, "Zorglub patate fromage", registry) is None and llm.schemas == []
