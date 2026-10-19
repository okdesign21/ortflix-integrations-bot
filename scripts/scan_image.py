"""Scan a locally built image with the CI Trivy policy and print safe findings."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Protocol

try:
    from .summarize_trivy_report import summarize_report
except ImportError:  # Running as `python scripts/scan_image.py`.
    from summarize_trivy_report import summarize_report


class CommandResult(Protocol):
    returncode: int


CommandRunner = Callable[..., CommandResult]


def scan_image(
    image: str,
    *,
    trivy: str = "trivy",
    run: CommandRunner = subprocess.run,
) -> int:
    """Return nonzero when Trivy fails or reports a blocking finding."""
    with tempfile.TemporaryDirectory(prefix="ortflix-trivy-") as temp_dir:
        report = Path(temp_dir) / "image-security.json"
        command = [
            trivy,
            "image",
            "--scanners",
            "vuln,secret",
            "--severity",
            "CRITICAL,HIGH",
            "--ignore-unfixed=false",
            "--exit-code",
            "1",
            "--format",
            "json",
            "--output",
            str(report),
            image,
        ]
        try:
            result = run(command, check=False)
        except FileNotFoundError:
            print(
                f"Trivy executable not found: {trivy}. Install Trivy or set TRIVY to its path.",
                file=sys.stderr,
            )
            return 127

        outcome = "success" if result.returncode == 0 else "failure"
        print(summarize_report(report, "image", outcome))
        return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Image tag to scan")
    parser.add_argument("--trivy", default="trivy", help="Trivy executable (default: trivy)")
    args = parser.parse_args()
    return scan_image(args.image, trivy=args.trivy)


if __name__ == "__main__":
    raise SystemExit(main())
