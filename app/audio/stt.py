"""Speech-to-text via a whisper.cpp command-line binary.

Shells out to whisper-cli (fetched by scripts/fetch_models.py) on a 16 kHz mono WAV and
returns the transcript. whisper.cpp is preferred for fast local execution
(architecture.md STT section).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.core.config import AudioConfig


class STTUnavailable(RuntimeError):
    pass


class WhisperCpp:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg

    def available(self) -> bool:
        return self.cfg.whisper_binary.exists() and self.cfg.whisper_model.exists()

    def transcribe(self, wav_path: Path) -> str:
        if not self.available():
            raise STTUnavailable(
                f"whisper binary or model missing. Expected {self.cfg.whisper_binary} and "
                f"{self.cfg.whisper_model}. Run: python scripts/fetch_models.py"
            )
        cmd = [
            str(self.cfg.whisper_binary),
            "-m", str(self.cfg.whisper_model),
            "-f", str(wav_path),
            "-l", "en",
            "-nt",            # no timestamps
            "--no-prints",    # suppress progress noise where supported
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise STTUnavailable(f"whisper-cli failed: {proc.stderr.strip()[:300]}")
        return self._clean(proc.stdout)

    @staticmethod
    def _clean(raw: str) -> str:
        lines = []
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("[") or s.startswith("whisper_"):
                continue
            lines.append(s)
        text = " ".join(lines)
        text = re.sub(r"\[(?:BLANK_AUDIO|SOUND|MUSIC|NOISE)\]", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()
        return text
