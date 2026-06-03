"""Silero VAD via ONNX Runtime.

Frame-level speech probability used to trim silence and detect speech start/end. The
model file (models/silero_vad.onnx) is fetched by scripts/fetch_models.py. If the model
is missing, the VAD degrades to an energy-based fallback so the rest of the pipeline
still works.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_WINDOW = 512  # samples per frame at 16 kHz (Silero v5 requirement)


class SileroVAD:
    def __init__(self, model_path: Path, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.available = False
        self._session = None
        self._state = None
        if model_path.exists():
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                self._session = ort.InferenceSession(str(model_path), sess_options=opts,
                                                      providers=["CPUExecutionProvider"])
                self.available = True
            except Exception:
                self.available = False
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def _prob_onnx(self, frame: np.ndarray) -> float:
        inputs = {
            "input": frame.reshape(1, -1).astype(np.float32),
            "state": self._state,
            "sr": np.array(self.sample_rate, dtype=np.int64),
        }
        out, new_state = self._session.run(None, inputs)
        self._state = new_state
        return float(out[0][0])

    @staticmethod
    def _prob_energy(frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-9))
        return min(1.0, rms / 0.05)

    def speech_prob(self, frame: np.ndarray) -> float:
        """Probability that a 512-sample (16 kHz) frame contains speech."""
        if len(frame) < _WINDOW:
            frame = np.pad(frame, (0, _WINDOW - len(frame)))
        elif len(frame) > _WINDOW:
            frame = frame[:_WINDOW]
        if self.available:
            try:
                return self._prob_onnx(frame)
            except Exception:
                return self._prob_energy(frame)
        return self._prob_energy(frame)

    def speech_ratio(self, audio: np.ndarray) -> float:
        """Fraction of frames in an utterance that look like speech (0..1)."""
        self.reset()
        if len(audio) == 0:
            return 0.0
        probs = []
        for i in range(0, len(audio) - _WINDOW + 1, _WINDOW):
            probs.append(self.speech_prob(audio[i:i + _WINDOW]))
        return float(np.mean([p > 0.5 for p in probs])) if probs else 0.0
