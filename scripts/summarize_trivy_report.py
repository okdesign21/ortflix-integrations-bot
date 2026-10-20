"""Render a sanitized Trivy summary without exposing matched secret content."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_VULNERABILITY_DETAILS = 500
MAX_SECRET_DETAILS = 30
_SAFE_TEXT = re.compile(r"[^A-Za-z0-9_.+:@/-]")
RUNTIME_OS = "opensuse-tumbleweed"
RUNTIME_PACKAGES = frozenset({"python314-base", "ffmpeg-8", "mkvtoolnix", "libxml2-16"})


def validate_runtime_inventory(report_path: Path) -> None:
    """Reject a clean-looking scan that missed the native runtime packages."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Native inventory report unavailable or invalid.") from None
    metadata = report.get("Metadata") if isinstance(report, dict) else None
    operating_system = metadata.get("OS") if isinstance(metadata, dict) else None
    if not isinstance(operating_system, dict) or operating_system.get("Family") != RUNTIME_OS:
        raise ValueError("Trivy did not recognize the expected native runtime OS.")
    results = report.get("Results")
    if not isinstance(results, list):
        raise ValueError("Trivy report has no valid native package results.")
    packages = {
        package.get("Name")
        for result in results
        if isinstance(result, dict)
        and result.get("Class") == "os-pkgs"
        and result.get("Type") == RUNTIME_OS
        for package in result.get("Packages") or []
        if isinstance(package, dict) and isinstance(package.get("Name"), str)
    }
    missing = RUNTIME_PACKAGES - packages
    if missing:
        raise ValueError("Trivy native inventory is incomplete: " + ", ".join(sorted(missing)))


def _safe_text(value: Any, limit: int = 100) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = _SAFE_TEXT.sub("_", value)[:limit]
    return cleaned or "unknown"


def _collect_vulnerabilities(results: list[Any]) -> list[tuple[str, str, str, str, str]]:
    vulnerabilities: list[tuple[str, str, str, str, str]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for finding in result.get("Vulnerabilities") or []:
            if not isinstance(finding, dict):
                continue
            severity = _safe_text(finding.get("Severity")).upper()
            if severity not in {"CRITICAL", "HIGH"}:
                continue
            vulnerabilities.append(
                (
                    severity,
                    _safe_text(finding.get("VulnerabilityID")),
                    _safe_text(finding.get("PkgName")),
                    _safe_text(finding.get("InstalledVersion")),
                    _safe_text(finding.get("FixedVersion")),
                )
            )
    return sorted(vulnerabilities)


def _collect_secrets(results: list[Any]) -> list[tuple[str, str, str, str, int | None]]:
    secrets: list[tuple[str, str, str, str, int | None]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for finding in result.get("Secrets") or []:
            if not isinstance(finding, dict):
                continue
            severity = _safe_text(finding.get("Severity")).upper()
            if severity not in {"CRITICAL", "HIGH"}:
                continue
            line = finding.get("StartLine")
            secrets.append(
                (
                    severity,
                    _safe_text(finding.get("RuleID")),
                    _safe_text(finding.get("Category")),
                    _safe_text(Path(_safe_text(result.get("Target"))).name),
                    line if isinstance(line, int) and line > 0 else None,
                )
            )
    return sorted(secrets)


def _render_findings(
    vulnerabilities: list[tuple[str, str, str, str, str]],
    secrets: list[tuple[str, str, str, str, int | None]],
    outcome: str,
) -> list[str]:
    summary = [
        f"HIGH/CRITICAL vulnerability findings: {len(vulnerabilities)}",
        f"HIGH/CRITICAL secret findings: {len(secrets)} (matched values and source text omitted)",
    ]
    if vulnerabilities:
        summary.append(
            f"Distinct vulnerability IDs: {len({finding[1] for finding in vulnerabilities})}"
        )
        summary.append("### Vulnerabilities")
        summary.extend(
            f"- {severity} {vulnerability_id}: {package} {installed} -> {fixed}"
            for severity, vulnerability_id, package, installed, fixed in vulnerabilities[
                :MAX_VULNERABILITY_DETAILS
            ]
        )
    if secrets:
        summary.append("### Secret findings (values omitted)")
        summary.extend(
            f"- {severity} rule={rule_id} category={category} at {target}"
            f"{f':{line}' if line else ''}"
            for severity, rule_id, category, target, line in secrets[:MAX_SECRET_DETAILS]
        )
    omitted = (
        len(vulnerabilities)
        + len(secrets)
        - min(len(vulnerabilities), MAX_VULNERABILITY_DETAILS)
        - min(len(secrets), MAX_SECRET_DETAILS)
    )
    if omitted:
        summary.append(f"Additional findings omitted from summary: {omitted}")
    if not vulnerabilities and not secrets:
        summary.append("No HIGH/CRITICAL findings in the emitted report.")
        if outcome != "success":
            summary.append(
                "The scan failed without reportable findings; inspect scanner diagnostics."
            )
    return summary


def summarize_report(report_path: Path, kind: str, outcome: str) -> str:
    """Summarize scan metadata; never render secret match, code, or descriptions."""
    header = [f"## Trivy {kind} scan (sanitized)", f"Scan step outcome: {_safe_text(outcome)}"]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "\n".join(header + ["Report unavailable or invalid; inspect scanner diagnostics."])

    results = report.get("Results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        return "\n".join(
            header + ["Report has no valid results list; inspect scanner diagnostics."]
        )

    vulnerabilities = _collect_vulnerabilities(results)
    secrets = _collect_secrets(results)
    return "\n".join(header + _render_findings(vulnerabilities, secrets, outcome))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--kind", choices=("repository", "image"), required=True)
    parser.add_argument("--outcome", default="unknown")
    parser.add_argument(
        "--require-native-runtime",
        action="store_true",
        help="Fail unless the image report inventories the expected OS and native media packages",
    )
    args = parser.parse_args()
    print(summarize_report(args.report, args.kind, args.outcome))
    if args.require_native_runtime:
        try:
            validate_runtime_inventory(args.report)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print("Native runtime inventory verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
