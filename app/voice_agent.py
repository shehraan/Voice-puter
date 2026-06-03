"""Voice entrypoint: push-to-talk -> VAD -> whisper.cpp STT -> agent loop.

    python -m app.voice_agent              # push-to-talk loop
    python -m app.voice_agent --once       # single capture
    python -m app.voice_agent --wav FILE   # transcribe a wav and run (mic-free testing)

Push-to-talk is used first for reliability. The same observe->plan->act loop runs once a
transcript is produced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.audio.stt import STTUnavailable, WhisperCpp
from app.audio.vad import SileroVAD
from app.core.config import AUDIO_DIR, load_config
from app.core.loop import run_command
from app.core.trace import Trace


def _run_transcript(text: str, cfg) -> int:
    text = text.strip()
    if not text:
        print("empty transcript; nothing to do")
        return 1
    print(f"transcript: {text!r}")
    trace = Trace(transcript=text)
    ok = run_command(text, cfg, trace, confirm=lambda r: False)
    path = trace.save()
    print(f"result: {trace.result}  trace: {path}")
    return 0 if ok else 1


def _transcribe_wav(wav: Path, cfg) -> str:
    vad = SileroVAD(cfg.audio.vad_model_path, cfg.audio.sample_rate)
    try:
        import soundfile as sf

        audio, _ = sf.read(str(wav), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        ratio = vad.speech_ratio(audio)
        print(f"vad speech ratio: {ratio:.2f}")
    except Exception:
        pass
    return WhisperCpp(cfg.audio).transcribe(wav)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice-driven Windows UI automation agent.")
    parser.add_argument("--wav", help="transcribe this wav and run once (no mic needed)")
    parser.add_argument("--once", action="store_true", help="single push-to-talk capture then exit")
    parser.add_argument("--no-demo", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.no_demo:
        cfg.visual_demo_mode = False
    if args.yes:
        cfg.auto_confirm = True

    if args.wav:
        try:
            text = _transcribe_wav(Path(args.wav), cfg)
        except STTUnavailable as exc:
            print(f"STT unavailable: {exc}", file=sys.stderr)
            return 2
        return _run_transcript(text, cfg)

    from app.audio.capture import record_while_held, save_wav

    stt = WhisperCpp(cfg.audio)
    if not stt.available():
        print("whisper.cpp not set up. Run: python scripts/fetch_models.py", file=sys.stderr)
        return 2

    while True:
        audio = record_while_held(cfg.audio)
        if len(audio) < cfg.audio.sample_rate // 2:
            print("(too short; try again)")
            if args.once:
                return 1
            continue
        wav = save_wav(AUDIO_DIR / "ptt.wav", audio, cfg.audio.sample_rate)
        try:
            text = stt.transcribe(wav)
        except STTUnavailable as exc:
            print(f"STT error: {exc}", file=sys.stderr)
            return 2
        _run_transcript(text, cfg)
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
