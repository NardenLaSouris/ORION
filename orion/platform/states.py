"""Machines d'état explicites : chaque module déclare ses états et les passages permis, le reste est refusé.

Un passage non prévu lève ``InvalidTransition`` au lieu de laisser un élément dans un état incohérent (leçon de V2 :
des attributs indépendants remis à zéro à des endroits différents ont caché un défaut pendant des semaines).
"""

from __future__ import annotations

from typing import Iterable, Mapping


class InvalidTransition(ValueError):
    pass


class StateMachine:
    def __init__(self, transitions: Mapping[str, Iterable[str]], initial: str):
        self.transitions = {state: frozenset(targets) for state, targets in transitions.items()}
        unknown = {t for targets in self.transitions.values() for t in targets} - set(self.transitions)
        if unknown:
            raise ValueError(f"États cibles non déclarés : {sorted(unknown)}")
        if initial not in self.transitions:
            raise ValueError(f"État initial inconnu : {initial}")
        self.initial = initial

    @property
    def states(self) -> frozenset[str]:
        return frozenset(self.transitions)

    def final(self, state: str) -> bool:
        return not self.transitions.get(state)

    def can(self, current: str, target: str) -> bool:
        return target in self.transitions.get(current, ())

    def check(self, current: str, target: str) -> str:
        if current not in self.transitions:
            raise InvalidTransition(f"État inconnu : {current}")
        if not self.can(current, target):
            raise InvalidTransition(f"Passage interdit : {current} -> {target}")
        return target
