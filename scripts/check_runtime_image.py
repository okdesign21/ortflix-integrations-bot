"""Check a runtime image offline using synthetic configuration and tiny media."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUILD_MODULES = ("pip", "setuptools", "wheel", "build", "setuptools_scm", "ensurepip")


def check_python_runtime() -> None:
    for name in BUILD_MODULES:
        if importlib.util.find_spec(name) is not None:
            raise RuntimeError(f"Build-only module remains in runtime: {name}")
    os.environ.update(
        TELEGRAM_TOKEN="123456:runtime-smoke-placeholder",
        TELEGRAM_CHAT_ID="12345",
        OVERSEERR_API_KEY="runtime-smoke-placeholder",
        WEBHOOK_TOKEN="runtime-smoke-placeholder",
    )
    for name in ("bot", "aiohttp", "requests", "plexapi", "yaml", "uvicorn"):
        importlib.import_module(name)


def _run_command(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=True, capture_output=True, timeout=30)


def check_media_tools(directory: Path) -> None:
    for tool in ("ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Required media tool missing: {tool}")
    media = str(directory / "smoke.mkv")
    _run_command(
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=8000:cl=mono",
        "-t",
        "0.1",
        "-c:a",
        "pcm_s16le",
        media,
    )
    probe = _run_command("ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", media)
    streams = json.loads(probe.stdout).get("streams", [])
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise RuntimeError("Synthetic media contains no audio stream")
    _run_command("mkvpropedit", media, "--edit", "track:a1", "--set", "language=eng")
    tracks = json.loads(_run_command("mkvmerge", "-J", media).stdout).get("tracks", [])
    if not any(track.get("properties", {}).get("language") == "eng" for track in tracks):
        raise RuntimeError("Synthetic media language edit was not applied")


def main() -> int:
    try:
        print("Checking runtime imports and absence of build tools...", flush=True)
        check_python_runtime()
        print("Checking synthetic media probe and metadata edit...", flush=True)
        with tempfile.TemporaryDirectory(prefix="ortflix-runtime-smoke-") as directory:
            check_media_tools(Path(directory))
    except Exception as error:
        print(
            f"Runtime smoke check failed ({type(error).__name__}); no raw output printed.",
            file=sys.stderr,
        )
        return 1
    print("Runtime image smoke check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
