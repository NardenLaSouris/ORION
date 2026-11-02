"""Outils vocaux de WATCH : créer (après confirmation), lister, mettre en pause, reprendre, supprimer (après
confirmation). La surveillance est désignée par son numéro dans la liste (« la deuxième surveillance »)."""

from __future__ import annotations

from orion.tools.base import Param, Risk, Tool, ToolError
from orion.watch.engine import WatchEngine
from orion.watch.interpret import interpret
from orion.watch.models import COMPLETED, EXPIRED, FAILED, PAUSED, TRIGGERED, WatchError

STATE_LABELS = {"ACTIVE": "active", PAUSED: "en pause", TRIGGERED: "déclenchée", COMPLETED: "terminée",
                EXPIRED: "expirée", FAILED: "en échec"}


def watch_tools(engine: WatchEngine) -> list[Tool]:
    def ordered():
        return sorted(engine.list(), key=lambda w: w.created_at)

    def pick(position: int):
        watches = ordered()
        if not 1 <= position <= len(watches):
            raise ToolError("watch_not_found", f"Il n'y a pas de surveillance numéro {position}.")
        return watches[position - 1]

    def understood(text: str) -> str:
        try:
            interpret(text, None)
        except WatchError as exc:
            if engine.llm is None or not engine.llm.usable:  # le LLM tentera sa chance à la création
                raise ToolError("invalid_parameters", str(exc)) from exc
        return text

    def create(request: str) -> dict:
        try:
            watch = engine.create({"request": request})
        except WatchError as exc:
            raise ToolError("invalid_parameters", str(exc)) from exc
        return {"name": watch.name, "position": len(ordered())}

    def listing() -> dict:
        return {"watches": [{"position": i, "name": w.name, "state": STATE_LABELS.get(w.state, w.state)}
                            for i, w in enumerate(ordered(), 1)]}

    def listed(result: dict) -> str:
        items = result["watches"]
        if not items:
            return "Vous n'avez aucune surveillance, monsieur."
        return "Vos surveillances : " + " ; ".join(f"{w['position']}, {w['name']}, {w['state']}" for w in items) + "."

    def change(action):
        def run(position: int) -> dict:
            watch = pick(position)
            try:
                updated = action(watch.id)
            except ValueError as exc:  # passage d'état interdit (surveillance terminée…)
                raise ToolError("invalid_parameters", f"Impossible pour cette surveillance ({exc}).") from exc
            return {"name": watch.name, "state": STATE_LABELS.get(getattr(updated, "state", ""), "supprimée")}
        return run

    def describe(position: int) -> str:
        try:
            return pick(position).name
        except ToolError:
            return f"numéro {position}"

    def question(p: dict) -> str:
        try:
            name = interpret(p["request"], None).name
        except WatchError:
            name = p["request"][:100]
        return f"Voulez-vous que je crée cette surveillance : {name} ?"

    position = Param(int, "numéro de la surveillance dans la liste", minimum=1, maximum=50)
    request = Param(str, "demande", max_length=500, hidden=True, resolve=lambda text: text, check=understood)
    return [
        Tool("create_watch", "Crée une surveillance d'une information extérieure : prix ou bonne affaire d'un "
             "produit, retour en stock, événement de jeu (Pokémon GO), annonce officielle ou sortie.",
             {"request": request}, {"name": "surveillance", "position": "numéro"}, Risk.CONFIRMATION_REQUIRED, create,
             question=question,
             say=lambda r: f"C'est noté, monsieur. Surveillance numéro {r['position']} : {r['name']}."),
        Tool("list_watches", "Liste les surveillances et leur état.", {}, {"watches": "surveillances"}, Risk.SAFE,
             listing, say=listed),
        Tool("pause_watch", "Met une surveillance en pause.", {"position": position}, {"name": "surveillance"},
             Risk.SAFE, change(engine.pause), say=lambda r: f"Surveillance en pause : {r['name']}."),
        Tool("resume_watch", "Reprend une surveillance en pause ou en échec.", {"position": position},
             {"name": "surveillance"}, Risk.SAFE, change(engine.resume),
             say=lambda r: f"Surveillance reprise : {r['name']}."),
        Tool("delete_watch", "Supprime une surveillance.", {"position": position}, {"name": "surveillance"},
             Risk.CONFIRMATION_REQUIRED, change(engine.delete),
             question=lambda p: f"Voulez-vous vraiment supprimer la surveillance {describe(p['position'])} ?",
             say=lambda r: f"Surveillance supprimée : {r['name']}."),
    ]
