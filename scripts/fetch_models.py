"""Fetch local model assets for the voice front-end.

Downloads into ./models:
  - silero_vad.onnx        (Silero VAD, ~2 MB)
  - ggml-base.en.bin       (whisper.cpp English model, ~150 MB)
  - whisper-cli.exe + dlls (whisper.cpp Windows binary, from a release zip)

Each asset is best-effort; failures are reported but do not abort the others. Override
versions/URLs with the constants below if upstream asset names change.

    python scripts/fetch_models.py
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import httpx

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

SILERO_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
WHISPER_RELEASES_API = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
WHISPER_BIN_ASSET = "whisper-bin-x64.zip"


def _resolve_whisper_zip_url() -> str | None:
    try:
        data = httpx.get(WHISPER_RELEASES_API, follow_redirects=True, timeout=60).json()
        for asset in data.get("assets", []):
            if asset.get("name") == WHISPER_BIN_ASSET:
                return asset["browser_download_url"]
    except Exception as exc:
        print(f"  ! could not resolve latest whisper release: {exc}")
    return None


def _download(url: str, dest: Path) -> bool:
    print(f"downloading {url}")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=1 << 16):
                    f.write(chunk)
        print(f"  -> {dest} ({dest.stat().st_size} bytes)")
        return True
    except Exception as exc:
        print(f"  ! failed: {exc}")
        return False


def _fetch_whisper_binary() -> bool:
    url = _resolve_whisper_zip_url()
    if not url:
        print("  ! no whisper binary asset found")
        return False
    print(f"downloading {url}")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=300)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        exe_found = None
        for name in zf.namelist():
            base = Path(name).name
            if not base:
                continue
            if base.lower().endswith((".exe", ".dll")):
                with zf.open(name) as src, open(MODELS_DIR / base, "wb") as out:
                    out.write(src.read())
                if base.lower() in ("whisper-cli.exe", "main.exe"):
                    exe_found = base
        if exe_found == "main.exe" and not (MODELS_DIR / "whisper-cli.exe").exists():
            (MODELS_DIR / "whisper-cli.exe").write_bytes((MODELS_DIR / "main.exe").read_bytes())
            exe_found = "whisper-cli.exe (copied from main.exe)"
        print(f"  -> extracted binary: {exe_found}")
        return exe_found is not None
    except Exception as exc:
        print(f"  ! failed: {exc}")
        return False


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    ok &= _download(SILERO_URL, MODELS_DIR / "silero_vad.onnx")
    ok &= _download(WHISPER_MODEL_URL, MODELS_DIR / "ggml-base.en.bin")
    ok &= _fetch_whisper_binary()
    print("\ndone." if ok else "\ndone with some failures (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
