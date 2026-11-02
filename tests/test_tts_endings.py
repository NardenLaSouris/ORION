"""Fin des phrases de Piper (orion/tts/piper.py, ``soften_ending``) : une phrase coupée en pleine voyelle s'éteint
au lieu de s'arrêter net ; une phrase qui s'éteint déjà n'est pas touchée. Aucun modèle chargé."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.tts.piper import ENDING_EXTEND_MS, soften_ending  # noqa: E402

RATE = 22050


def vowel(seconds: float, amplitude: float = 8000.0) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * 180 * t + 0.3)).astype(np.int16)


def dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean((x.astype(np.float32) / 32768) ** 2)) + 1e-9)


def test_voiced_ending_is_extended_and_fades_out():
    audio = vowel(0.5)  # « ...on » coupé net : pleine voyelle jusqu'au dernier échantillon
    out = soften_ending(audio, RATE)
    extend = RATE * ENDING_EXTEND_MS // 1000
    assert len(out) == len(audio) + extend
    assert np.array_equal(out[: len(audio)], audio)  # rien de la phrase n'est modifié
    assert abs(int(out[len(audio)]) - int(audio[-1])) <= 1  # jointure continue : aucun clic
    assert dbfs(audio[-RATE // 100:]) > -25  # coupé en pleine voyelle...
    assert dbfs(out[-RATE // 100:]) < dbfs(audio[-RATE // 100:]) - 20  # ... et désormais éteint
    assert abs(int(out[-1])) <= 2


def test_ending_that_already_fades_is_left_untouched():
    audio = vowel(0.5)
    audio[-RATE // 20:] = 0  # 50 ms de silence final : la phrase s'éteint d'elle-même
    assert soften_ending(audio, RATE) is audio


def test_quiet_ending_is_left_untouched():
    audio = np.concatenate([vowel(0.5), vowel(0.05, amplitude=50)])
    assert soften_ending(audio, RATE) is audio


def test_too_short_audio_is_left_untouched():
    audio = vowel(0.03)
    assert soften_ending(audio, RATE) is audio
