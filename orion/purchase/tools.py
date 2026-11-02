"""Outil vocal de PURCHASE : seulement l'arrêt global (« arrête tous les achats automatiques »). Arrêter est toujours
sûr ; reprendre, créer un mandat ou confirmer un achat se fait dans ORION Control, jamais à la voix."""

from __future__ import annotations

from orion.purchase.engine import PurchaseEngine
from orion.tools.base import Risk, Tool


def purchase_tools(engine: PurchaseEngine) -> list[Tool]:
    return [Tool("stop_purchases", "Arrête immédiatement tous les achats automatiques et les commandes non payées.",
                 {}, {"stopped": "arrêt"}, Risk.SAFE,
                 lambda: {"stopped": engine.set_stopped(True, "utilisateur (voix)")},
                 say=lambda r: "C'est fait, monsieur : plus aucun achat automatique. Vous pourrez les reprendre "
                               "dans ORION Control.")]
