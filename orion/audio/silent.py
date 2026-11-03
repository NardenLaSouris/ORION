"""Audio absent (``[audio] mode = "none"``) : environnement de test sans micro ni haut-parleur.

La source rend du silence au rythme réel (le mot de réveil ne se déclenche jamais, la boucle d'ORION continue de
traiter les demandes écrites entre deux blocs) ; la sortie ne joue rien. Rien n'est ouvert : aucun périphérique,
aucun agent distant, aucun jeton.
"""

from __future__ import annotations

import time

import numpy as np


class SilentSource:
    silent = True  # la boucle d'ORION ne cherche pas le mot de réveil (aucun calcul inutile sur le mini-PC)

    def __init__(self, sample_rate: int, frame_samples: int, sleep=time.sleep):
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self._sleep = sleep
        self._frame = np.zeros(frame_samples, dtype=np.int16)

    def read(self) -> np.ndarray | None:
        self._sleep(self.frame_samples / self.sample_rate)
        return self._frame

    def flush(self) -> None:
        pass


class NullSink:
    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        pass
