"""Microphone capture with a push-to-talk hotkey.

Push-to-talk first for reliability (architecture.md audio service). Records 16 kHz mono
float32 audio while the PTT key is held, into a bounded ring buffer, and writes a WAV for
the STT stage.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from app.core.config import AudioConfig


def save_wav(path: Path, audio: np.ndarray, sample_rate: int) -> Path:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.astype(np.float32), sample_rate, subtype="PCM_16")
    return path


def record_while_held(cfg: AudioConfig, key: str | None = None, max_seconds: float | None = None) -> np.ndarray:
    """Block until the PTT key is pressed, record while held, return mono float32 audio."""
    import keyboard
    import sounddevice as sd

    key = key or cfg.push_to_talk_key
    max_seconds = max_seconds or cfg.ring_seconds
    frames: list[np.ndarray] = []

    print(f"hold [{key}] and speak...")
    keyboard.wait(key)
    print("recording...")

    def _cb(indata, _frames, _time, _status):
        frames.append(indata.copy().reshape(-1))

    with sd.InputStream(samplerate=cfg.sample_rate, channels=cfg.channels, dtype="float32", callback=_cb):
        start = time.time()
        while keyboard.is_pressed(key) and (time.time() - start) < max_seconds:
            sd.sleep(20)
    print("done.")
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(frames).astype(np.float32)
