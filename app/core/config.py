"""Central configuration.

All tunables for the agent live here so the rest of the code stays free of magic
numbers. Paths are resolved relative to the repository root.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "runtime"
TRACES_DIR = RUNTIME_DIR / "traces"
AUDIO_DIR = RUNTIME_DIR / "audio"
MODELS_DIR = REPO_ROOT / "models"
SELECTOR_CACHE_PATH = RUNTIME_DIR / "selector_cache.json"


@dataclass
class VisualTiming:
    """Human-visible pauses so the desktop demo does not look like a hidden script."""

    after_launch_ms: int = 600
    after_focus_ms: int = 150
    after_action_ms: int = 250
    type_char_delay_s: float = 0.012


@dataclass
class PlannerConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    model: str = field(default_factory=lambda: os.environ.get("AGENT_PLANNER_MODEL", "qwen2.5:7b-instruct"))
    temperature: float = 0.2
    request_timeout_s: float = 60.0


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    block_ms: int = 32
    ring_seconds: float = 30.0
    vad_threshold: float = 0.5
    vad_min_silence_ms: int = 600
    push_to_talk_key: str = "ctrl+alt+space"
    vad_model_path: Path = MODELS_DIR / "silero_vad.onnx"
    whisper_binary: Path = field(
        default_factory=lambda: Path(os.environ.get("WHISPER_BIN", str(MODELS_DIR / "whisper-cli.exe")))
    )
    whisper_model: Path = field(
        default_factory=lambda: Path(os.environ.get("WHISPER_MODEL", str(MODELS_DIR / "ggml-base.en.bin")))
    )


@dataclass
class LoopConfig:
    max_iterations: int = 8
    repair_budget: int = 3
    observe_max_controls: int = 40
    observe_max_depth: int = 12
    wait_default_ms: int = 4000
    wait_poll_ms: int = 150


@dataclass
class Config:
    visual_demo_mode: bool = True
    auto_confirm: bool = False  # when True, confirmation-required actions proceed (tests/headless)
    timing: VisualTiming = field(default_factory=VisualTiming)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)

    def ensure_dirs(self) -> None:
        for d in (RUNTIME_DIR, TRACES_DIR, AUDIO_DIR):
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    cfg = Config()
    cfg.ensure_dirs()
    return cfg
