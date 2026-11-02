"""Au nom de qui s'exécute le code en cours (CORE FOUNDATION F2) : le planificateur et le service de tâches fixent le
principal de la tâche le temps de son exécution, pour le journal et le diagnostic.

F3 : ce contexte ne sert **jamais** à classer une alerte ou une notification ; leur origine vient de l'identité et du
type réel de ce qui les émet (``Alert.origin``), pas de la tâche qui les a déclenchées.
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

