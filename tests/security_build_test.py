"""Regression checks for private build inputs and publication policies."""

import io
import json
import os
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

from scripts.check_runtime_image import check_media_tools, check_python_runtime
from scripts.scan_image import scan_image
from scripts.summarize_trivy_report import summarize_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_image_build_tools_and_isolated_build_use_patched_baselines():
    dockerfile = (PROJECT_ROOT / "dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.14-slim-trixie AS builder\n")
    assert "apt-get update &&" in dockerfile
    assert "apt-get upgrade -y &&" in dockerfile
    requirements = [
        Requirement(line)
        for line in (PROJECT_ROOT / "build-constraints.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    ]
    pins = {}
    for requirement in requirements:
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "=="
        pins[requirement.name] = Version(specifiers[0].version)
        assert str(requirement) not in dockerfile
    assert pins["pip"] >= Version("25.3")
    assert pins["setuptools"] >= Version("78.1.1")
    assert pins["wheel"] >= Version("0.46.2")
    assert {"build", "setuptools-scm"} <= pins.keys()
    assert "COPY pyproject.toml build-constraints.txt README.md ./" in dockerfile
    assert "--constraint build-constraints.txt pip setuptools wheel" in dockerfile
    assert "--build-constraint build-constraints.txt --wheel-dir /wheels ." in dockerfile
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "setuptools>=78.1.1" in project["build-system"]["requires"]
    assert "wheel>=0.46.2" in project["build-system"]["requires"]
    for item in project["build-system"]["requires"]:
        requirement = Requirement(item)
        assert pins[requirement.name] in requirement.specifier


def test_runtime_image_does_not_retain_build_tooling():
    dockerfile = (PROJECT_ROOT / "dockerfile").read_text(encoding="utf-8")
    builder, runtime = dockerfile.split("FROM python:3.14-slim-trixie AS runtime\n")
    assert "python -m pip wheel" in builder
    assert "COPY --from=builder /wheels /wheels" in runtime
    assert "--no-index --find-links=/wheels /wheels/ortflix_integrations-*.whl" in runtime
    assert "python -m pip uninstall --yes pip setuptools wheel" in runtime
    assert "rm -rf /wheels /usr/local/lib/python*/ensurepip" in runtime
    assert "--upgrade" not in runtime
    assert "build-constraints.txt" not in runtime
    assert 'ENTRYPOINT ["python", "-m", "bot"]' in runtime


def test_runtime_smoke_rejects_remaining_build_tools(monkeypatch):
    monkeypatch.setattr(
        "scripts.check_runtime_image.importlib.util.find_spec", lambda name: object()
    )
    with pytest.raises(RuntimeError, match="Build-only module remains"):
        check_python_runtime()


def test_runtime_smoke_rejects_missing_media_tools(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.check_runtime_image.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="Required media tool missing"):
        check_media_tools(tmp_path)


def test_runtime_smoke_uses_only_synthetic_media_and_safe_arguments(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr("scripts.check_runtime_image.shutil.which", lambda name: name)

    def run_command(*args):
        commands.append(args)
        if args[0] == "ffprobe":
            data = {"streams": [{"codec_type": "audio"}]}
        else:
            data = {"tracks": [{"properties": {"language": "eng"}}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode())

    monkeypatch.setattr("scripts.check_runtime_image._run_command", run_command)
    check_media_tools(tmp_path)
    assert [command[0] for command in commands] == ["ffmpeg", "ffprobe", "mkvpropedit", "mkvmerge"]
    assert "anullsrc=r=8000:cl=mono" in commands[0]
    assert str(tmp_path / "smoke.mkv") in commands[0]


@pytest.mark.parametrize(
    "filename,job",
    [("security.yml", "trivy-image"), ("publish-image.yml", "publish")],
)
def test_image_scan_requires_offline_runtime_smoke(filename, job):
    steps = workflow(filename)["jobs"][job]["steps"]
    smoke = next(
        step for step in steps if step.get("name") == "Check runtime without network or host media"
    )
    scan = next(step for step in steps if "trivy-action" in step.get("uses", ""))
    assert "docker run --rm --network none --entrypoint python" in smoke["run"]
    assert "-m scripts.check_runtime_image" in smoke["run"]
    assert "--volume" not in smoke["run"] and " -v " not in smoke["run"]
    assert "continue-on-error" not in smoke and "if" not in smoke
    assert steps.index(smoke) < steps.index(scan)


def test_ci_uses_central_build_tool_constraints():
    release_steps = workflow("release.yml")["jobs"]["build-python-package"]["steps"]
    install = next(
        step for step in release_steps if step.get("name") == "Install build dependencies"
    )
    assert "--constraint build-constraints.txt pip" in install["run"]
    assert "--constraint build-constraints.txt build" in install["run"]
    build = next(step for step in release_steps if step.get("name") == "Build package")
    assert build["env"] == {
        "PIP_CONSTRAINT": "${{ github.workspace }}/build-constraints.txt",
        "PIP_BUILD_CONSTRAINT": "${{ github.workspace }}/build-constraints.txt",
    }
    assert "python -m build --installer pip" in build["run"]
    lint = workflow("lint.yml")
    for job in ("ruff", "pytest"):
        steps = lint["jobs"][job]["steps"]
        install = next(step for step in steps if step.get("name", "").startswith("Install "))
        assert "--constraint build-constraints.txt pip" in install["run"]
        assert "--build-constraint build-constraints.txt" in install["run"]


def test_makefile_has_cross_platform_trivy_target():
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "DOCKER ?= docker" in makefile
    assert "PYTHON ?= python" in makefile
    assert "TRIVY ?= trivy" in makefile
    assert "trivy-image: docker-build" in makefile
    assert 'scripts/scan_image.py "$(IMAGE_NAME)" --trivy "$(TRIVY)"' in makefile


def test_local_image_scan_uses_ci_policy_and_summarizes_findings(capsys, tmp_path):
    commands = []

    def fake_run(command, check):
        commands.append(command)
        report_path = Path(command[command.index("--output") + 1])
        report_path.write_text(
            json.dumps(
                {
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {
                                    "Severity": "HIGH",
                                    "VulnerabilityID": "CVE-2026-12345",
                                    "PkgName": "sample-lib",
                                    "InstalledVersion": "1.2.3",
                                    "FixedVersion": "1.2.4",
                                }
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1)

    result = scan_image("ortflix-bot-addons:scan", trivy="trivy-test", run=fake_run)
    output = capsys.readouterr().out
    command = commands[0]
    assert result == 1
    assert command[:2] == ["trivy-test", "image"]
    assert command[command.index("--scanners") + 1] == "vuln,secret"
    assert command[command.index("--severity") + 1] == "CRITICAL,HIGH"
    assert command[command.index("--exit-code") + 1] == "1"
    assert command[-1] == "ortflix-bot-addons:scan"
    assert "CVE-2026-12345" in output
    assert "sample-lib 1.2.3 -> 1.2.4" in output
    assert "Trivy image scan (sanitized)" in output


def test_local_image_scan_returns_success_and_handles_missing_trivy(capsys):
    success = scan_image(
        "ortflix-bot-addons:scan",
        run=lambda command, check: subprocess.CompletedProcess(command, 0),
    )
    assert success == 0
    assert "Report unavailable or invalid" in capsys.readouterr().out

    def missing(command, check):
        raise FileNotFoundError(command[0])

    result = scan_image("ortflix-bot-addons:scan", trivy="missing-trivy", run=missing)
    captured = capsys.readouterr()
    assert result == 127
    assert "Trivy executable not found: missing-trivy" in captured.err


def workflow(name):
    return yaml.load(
        (PROJECT_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_scan_findings_and_errors_block_publication():
    security = workflow("security.yml")
    for job in security["jobs"].values():
        assert job.get("continue-on-error", "false") != "true"
        for step in job["steps"]:
            assert step.get("continue-on-error", "false") != "true"
            if "trivy-action" in step.get("uses", ""):
                assert step["with"]["exit-code"] == "1"
                assert step["with"]["ignore-unfixed"] == "false"
                assert step["with"]["scanners"] == "vuln,secret"
                assert step["with"]["format"] == "json"
                assert step["with"]["output"].startswith("${{ runner.temp }}")
    secret_scan = security["jobs"]["trufflehog"]["steps"][-1]
    assert (
        '--no-verification --fail --json > "$RUNNER_TEMP/history-secrets.json"'
        in secret_scan["run"]
    )
    for job_name, report_name in (
        ("trivy-fs", "repository-security.json"),
        ("trivy-image", "image-security.json"),
    ):
        steps = security["jobs"][job_name]["steps"]
        scanner_index = next(
            index for index, step in enumerate(steps) if "trivy-action" in step.get("uses", "")
        )
        summary = steps[scanner_index + 1]
        assert summary["if"] == "always()"
        assert "summarize_trivy_report.py" in summary["run"]
        assert (
            summary["env"]["SCAN_OUTCOME"]
            == f"${{{{ steps.{steps[scanner_index]['id']}.outcome }}}}"
        )
        assert report_name in summary["run"]
        assert '"$GITHUB_STEP_SUMMARY"' in summary["run"]


def test_trivy_summary_shows_vulnerability_metadata_but_not_payloads(tmp_path):
    secret_marker = "synthetic-secret-do-not-print"
    report = tmp_path / "trivy.json"
    report.write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "Target": "usr/local/app/config.py",
                        "Vulnerabilities": [
                            {
                                "Severity": "HIGH",
                                "VulnerabilityID": "CVE-2026-12345",
                                "PkgName": "example-package",
                                "InstalledVersion": "1.0.0",
                                "FixedVersion": "1.0.1",
                                "Description": secret_marker,
                            }
                        ],
                        "Secrets": [
                            {
                                "Severity": "CRITICAL",
                                "RuleID": "generic-api-key",
                                "Category": "Generic",
                                "StartLine": 42,
                                "Match": secret_marker,
                                "Code": {"Lines": [{"Content": secret_marker}]},
                                "Title": secret_marker,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = summarize_report(report, "image", "failure")
    assert "CVE-2026-12345" in summary
    assert "example-package 1.0.0 -> 1.0.1" in summary
    assert "rule=generic-api-key category=Generic at config.py:42" in summary
    assert "CRITICAL secret findings: 1" in summary
    assert secret_marker not in summary


def test_trivy_summary_includes_complete_current_sized_inventory(tmp_path):
    report = tmp_path / "trivy.json"
    findings = [
        {
            "Severity": "HIGH",
            "VulnerabilityID": "CVE-2026-12345",
            "PkgName": f"package-{index:03}",
            "InstalledVersion": "1.0",
        }
        for index in range(213)
    ]
    report.write_text(json.dumps({"Results": [{"Vulnerabilities": findings}]}), encoding="utf-8")
    summary = summarize_report(report, "image", "failure")
    assert "vulnerability findings: 213" in summary
    assert "Distinct vulnerability IDs: 1" in summary
    assert "package-212" in summary
    assert "Additional findings omitted" not in summary


def test_trivy_summary_bounds_large_inventory_and_secret_details(tmp_path):
    report = tmp_path / "trivy.json"
    vulnerability = {"Severity": "HIGH", "VulnerabilityID": "CVE-2026-12345"}
    secret = {"Severity": "HIGH", "RuleID": "generic-api-key", "Match": "never-print-marker"}
    report.write_text(
        json.dumps(
            {"Results": [{"Vulnerabilities": [vulnerability] * 501, "Secrets": [secret] * 31}]}
        ),
        encoding="utf-8",
    )
    summary = summarize_report(report, "image", "failure")
    assert summary.count("- HIGH CVE-2026-12345") == 500
    assert summary.count("rule=generic-api-key") == 30
    assert "Additional findings omitted from summary: 2" in summary
    assert "never-print-marker" not in summary


def test_trivy_summary_reports_unavailable_or_empty_reports_safely(tmp_path):
    missing = summarize_report(tmp_path / "missing.json", "image", "failure")
    assert "Report unavailable or invalid" in missing
    report = tmp_path / "empty.json"
    report.write_text('{"Results": []}', encoding="utf-8")
    empty = summarize_report(report, "image", "failure")
    assert "No HIGH/CRITICAL findings in the emitted report" in empty
    assert "inspect scanner diagnostics" in empty


@pytest.mark.parametrize(
    "filename,job,channel",
    [
        ("release.yml", "build-and-push-docker-image", "release"),
        ("edge.yml", "publish-edge", "edge"),
    ],
)
def test_publish_channels_require_checks_and_shared_scanned_build(filename, job, channel):
    publish = workflow(filename)["jobs"][job]
    assert set(publish["needs"]) == {"lint", "security"}
    assert publish["uses"] == "./.github/workflows/publish-image.yml"
    assert publish["with"]["channel"] == channel
    assert publish["permissions"] == {"contents": "read", "packages": "write"}


def test_scanned_candidate_is_pushed_without_rebuilding():
    steps = workflow("publish-image.yml")["jobs"]["publish"]["steps"]
    builds = [step for step in steps if "docker/build-push-action" in step.get("uses", "")]
    assert len(builds) == 1
    build = builds[0]
    assert build["with"]["load"] == "true"
    assert build["with"]["push"] == "false"
    scan = next(step for step in steps if "trivy-action" in step.get("uses", ""))
    assert scan["with"]["image-ref"] == "${{ steps.build.outputs.imageid }}"
    assert scan["with"]["exit-code"] == "1"
    assert scan.get("continue-on-error", "false") != "true"
    publish = steps[-1]
    assert publish["env"]["IMAGE_ID"] == scan["with"]["image-ref"]
    assert 'docker tag "$IMAGE_ID" "$tag"' in publish["run"]
    assert 'docker push "$tag"' in publish["run"]
    assert "build" not in publish["run"]
    assert steps.index(build) < steps.index(scan) < steps.index(publish)
    assert "if" not in scan and "if" not in publish


@pytest.mark.parametrize(
    "image_id,tags,expected_success",
    [
        ("sha256:synthetic", "ghcr.io/example/bot:first\nghcr.io/example/bot:second", True),
        ("", "ghcr.io/example/bot:first", False),
        ("sha256:synthetic", "", False),
    ],
)
def test_publication_shell_only_tags_and_pushes_scanned_id(
    tmp_path, image_id, tags, expected_success
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Requires Bash for GitHub Actions shell validation")
        return
    commands = tmp_path / "docker-commands.txt"
    script = tmp_path / "publish.sh"
    publish = workflow("publish-image.yml")["jobs"]["publish"]["steps"][-1]
    script.write_text(
        'docker() { printf "%s\\n" "$*" >> "$AUDIT_COMMAND_LOG"; }\n' + publish["run"],
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(IMAGE_ID=image_id, IMAGE_TAGS=tags, AUDIT_COMMAND_LOG=commands.as_posix())
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", script.as_posix()],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is expected_success
    if expected_success:
        assert commands.read_text(encoding="utf-8").splitlines() == [
            "tag sha256:synthetic ghcr.io/example/bot:first",
            "push ghcr.io/example/bot:first",
            "tag sha256:synthetic ghcr.io/example/bot:second",
            "push ghcr.io/example/bot:second",
        ]
    else:
        assert not commands.exists()


def test_docker_context_uses_runtime_allowlist():
    rules = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert rules == [
        "**",
        "!pyproject.toml",
        "!build-constraints.txt",
        "!README.md",
        "!bot.py",
        "!app_config.py",
        "!__init__.py",
        "!config",
        "config/**",
        "!config/bot_settings.yaml",
        "!integrations",
        "integrations/**",
        "!integrations/*.py",
        "!scripts",
        "scripts/**",
        "!scripts/*.py",
    ]


@pytest.mark.skipif(
    os.getenv("RUN_DOCKER_BUILD_TESTS") != "1",
    reason="Requires explicitly enabled disposable Docker engine",
)
def test_docker_image_excludes_private_fixture_files(tmp_path):
    assert shutil.which("docker"), "Docker CLI is required when RUN_DOCKER_BUILD_TESTS=1"
    included = [
        "bot.py",
        "app_config.py",
        "__init__.py",
        "pyproject.toml",
        "build-constraints.txt",
        "README.md",
        "config/bot_settings.yaml",
    ]
    included += ["integrations/handler.py", "scripts/action.py"]
    excluded = [
        ".env",
        "scripts/.env",
        "scripts/credentials.json",
        "config/bot_settings.local.yaml",
        "config/private.yaml",
        "integrations/private.json",
        "tests/private.py",
        "private.py",
    ]
    for name in included + excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-private-marker\n", encoding="utf-8")
    shutil.copyfile(PROJECT_ROOT / ".dockerignore", tmp_path / ".dockerignore")
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY . /context/\n", encoding="utf-8")
    image_id = None
    container_id = None
    try:
        build = subprocess.run(
            ["docker", "build", "--quiet", str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        image_id = build.stdout.strip().splitlines()[-1]
        container = subprocess.run(
            ["docker", "create", image_id, "/not-executed"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_id = container.stdout.strip()
        archive = subprocess.run(
            ["docker", "export", container_id], check=True, capture_output=True, timeout=30
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as filesystem:
            names = {entry.name for entry in filesystem}
        for name in included:
            assert f"context/{name}" in names
        for name in excluded:
            assert f"context/{name}" not in names
    finally:
        if container_id:
            subprocess.run(
                ["docker", "rm", "-f", container_id], check=True, capture_output=True, timeout=30
            )
        if image_id:
            subprocess.run(
                ["docker", "image", "rm", image_id], check=True, capture_output=True, timeout=30
            )
