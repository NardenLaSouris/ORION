"""Synthèse vocale locale avec Piper."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from piper import PiperVoice, SynthesisConfig

PUNCTUATION = ".,;:!?"
# Fin de phrase : la voix ORION s'arrête souvent encore voisée (« ORION. », « disposition. » : le « on » final est
# coupé net, 60 à 75 dB dans les 10 dernières ms, alors que « demain. » s'éteint vers 40 dB). Mesuré dans la sortie
# du modèle lui-même, avant tout transport (le rééchantillonnage de l'appel ne retient que 0,7 ms).
ENDING_PROBE_MS = 10
ENDING_VOICED_DBFS = -40.0  # au-dessus : la phrase finit en pleine voyelle
ENDING_EXTEND_MS = 40


def soften_ending(audio: np.ndarray, rate: int, extend_ms: int = ENDING_EXTEND_MS) -> np.ndarray:
    """Phrase coupée en pleine voyelle : prolongée de ``extend_ms`` par son propre miroir (même timbre, aucune
    discontinuité à la jointure), qui s'éteint en demi-cosinus. Une phrase qui s'éteint déjà est rendue telle quelle."""
    probe, extend = max(1, rate * ENDING_PROBE_MS // 1000), max(1, rate * extend_ms // 1000)
    if audio.size < 2 * extend:
        return audio
    level = float(np.sqrt(np.mean((audio[-probe:].astype(np.float32) / 32768.0) ** 2)))
    if level < 10 ** (ENDING_VOICED_DBFS / 20):
        return audio
    fade = 0.5 * (1.0 + np.cos(np.pi * np.arange(extend) / extend))
    tail = (audio[-extend:][::-1].astype(np.float32) * fade).astype(np.int16)
    return np.concatenate([audio, tail])


def parse_voice_spec(spec: str, voices_dir: Path) -> tuple[Path, str]:
    name, _, speaker = spec.partition(":")
    name = name.removesuffix(".onnx")
    return voices_dir / f"{name}.onnx", speaker


class PiperTTS:
    def __init__(
        self,
        voice: Path,
        length_scale: float = 1.0,
        volume: float = 1.0,
        speaker: str = "",
        pronunciations: dict[str, str] | None = None,
        soften_endings: bool = True,
    ):
        if not voice.exists():
            raise FileNotFoundError(
                f"Voix Piper introuvable : {voice}. Lancez `python scripts/download_models.py`."
            )
        self._voice = PiperVoice.load(voice)
        self._config = SynthesisConfig(
            length_scale=length_scale, volume=volume, speaker_id=self._speaker_id(speaker)
        )
        self._lexicon = [
            (re.compile(rf"\b{re.escape(word)}\b([{re.escape(PUNCTUATION)}]?)", re.IGNORECASE), phonemes)
            for word, phonemes in (pronunciations or {}).items()
        ]
        self.sample_rate = self._voice.config.sample_rate
        self._soften_endings = soften_endings
        self.voice_name = voice.stem + (f":{speaker}" if self._config.speaker_id is not None else "")

    def _speaker_id(self, speaker: str) -> int | None:
        if self._voice.config.num_speakers <= 1 or not speaker:
            return None
        ids = self._voice.config.speaker_id_map
        if speaker in ids:
            return ids[speaker]
        if speaker.isdigit() and int(speaker) < self._voice.config.num_speakers:
            return int(speaker)
        raise ValueError(f"Locuteur « {speaker} » inconnu ; disponibles : {', '.join(ids)}")

    def prepare(self, text: str) -> str:
        for pattern, phonemes in self._lexicon:
            text = pattern.sub(lambda m, phonemes=phonemes: f"[[ {phonemes}{m.group(1)} ]]", text)
        return text

    def phonemes(self, text: str) -> str:
        return " ".join("".join(sentence) for sentence in self._voice.phonemize(self.prepare(text)))

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        chunks = [c.audio_int16_array for c in self._voice.synthesize(self.prepare(text), self._config)]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        if self._soften_endings:
            audio = soften_ending(audio, self.sample_rate)
        return audio, self.sample_rate
