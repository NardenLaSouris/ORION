"""Au nom de qui s'exécute le code en cours (CORE FOUNDATION F2) : le planificateur et le service de tâches fixent le
principal de la tâche le temps de son exécution ; une alerte émise pendant ce temps par le même module part sous ce
principal (un bulletin NEWS que vous avez programmé reste une tâche programmée, une alerte spontanée de NEWS reste
une initiative du module).

Le principal est fixé par la plateforme, d'après sa propre classification des tâches : un module ne le choisit pas.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

RUNNING_AS: contextvars.ContextVar[str] = contextvars.ContextVar("orion_running_as", default="")


@contextmanager
def running_as(principal: str) -> Iterator[None]:
    token = RUNNING_AS.set(principal or "")
    try:
        yield
    finally:
        RUNNING_AS.reset(token)


def current() -> str:
    return RUNNING_AS.get()


def principal_for(source: str) -> str:
    """Principal de la tâche en cours si elle appartient au module ``source`` (``delegated:news:…`` ou
    ``module:news`` pour la source ``news``), sinon "" ."""
    running = current()
    parts = running.split(":")
    return running if len(parts) >= 2 and parts[1] == source else ""
