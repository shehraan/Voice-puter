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
    # "ollama" (default) or "openai" — set AGENT_PLANNER_PROVIDER or pass --openai to run_text
    provider: str = field(
        default_factory=lambda: os.environ.get("AGENT_PLANNER_PROVIDER", "ollama").strip().lower()
    )
    base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    model: str = field(default_factory=lambda: os.environ.get("AGENT_PLANNER_MODEL", "qwen2.5:14b-instruct"))
    temperature: float = 0.2
    request_timeout_s: float = 60.0
    # OpenAI Responses API (GPT-5.x reasoning models)
    openai_base_url: str = field(
        default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    openai_api_key: str | None = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: os.environ.get("AGENT_OPENAI_MODEL", "gpt-5.4"))
    openai_reasoning_effort: str = field(
        default_factory=lambda: os.environ.get("AGENT_OPENAI_REASONING_EFFORT", "high").strip().lower()
    )
    openai_max_output_tokens: int = field(
        default_factory=lambda: int(os.environ.get("AGENT_OPENAI_MAX_OUTPUT_TOKENS", "16000"))
    )
    openai_request_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("AGENT_OPENAI_REQUEST_TIMEOUT_S", "180"))
    )
    consensus_agents: int = field(
        default_factory=lambda: int(os.environ.get("AGENT_CONSENSUS_AGENTS", "3"))
    )
    consensus_min_votes: int = field(
        default_factory=lambda: int(os.environ.get("AGENT_CONSENSUS_MIN_VOTES", "2"))
    )
    consensus_min_confidence: float = field(
        default_factory=lambda: float(os.environ.get("AGENT_CONSENSUS_MIN_CONFIDENCE", "0.5"))
    )
    consensus_enabled: bool = field(
        default_factory=lambda: os.environ.get("AGENT_CONSENSUS_ENABLED", "1").strip().lower()
        not in ("0", "false", "no")
    )


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
    observe_max_controls: int = 280
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


def _load_dotenv() -> None:
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def load_config() -> Config:
    _load_dotenv()
    cfg = Config()
    cfg.ensure_dirs()
    return cfg
