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
from scripts.summarize_trivy_report import summarize_report, validate_runtime_inventory
from scripts.update_dependencies import runtime_pins

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_image_build_tools_and_isolated_build_use_patched_baselines():
    dockerfile = (PROJECT_ROOT / "dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM opensuse/tumbleweed@sha256:")
    assert "zypper --non-interactive install --no-recommends" in dockerfile
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


def test_media_runtime_uses_vendor_packages_and_preserves_inventory():
    dockerfile = (PROJECT_ROOT / "dockerfile").read_text(encoding="utf-8")
    base, stages = dockerfile.split("FROM base AS builder\n")
    pins = runtime_pins(dockerfile)
    for package, minimum in {
        "python314-base": "3.14.8",
        "ffmpeg-8": "8.1.3",
        "mkvtoolnix": "102.0",
        "libxml2-16": "2.15.4",
    }.items():
        assert package + "=" in base
        assert Version(pins[package].rsplit("-", 1)[0]) >= Version(minimum)
    assert "ca-certificates" in pins
    assert "FROM base AS runtime" in stages
    for obsolete in ("apt-get", "media-builder", "ffmpeg.deb", "build_ffmpeg.sh"):
        assert obsolete not in dockerfile
    for inventory in ("/var/lib/rpm", "/usr/lib/sysimage/rpm"):
        assert inventory not in dockerfile


def test_runtime_image_does_not_retain_build_tooling():
    dockerfile = (PROJECT_ROOT / "dockerfile").read_text(encoding="utf-8")
    builder, runtime = dockerfile.split("FROM base AS runtime\n")
    assert "python -m pip wheel" in builder
    assert "--no-index --find-links=/wheels --prefix=/install" in builder
    assert "COPY --from=builder /install/ /usr/" in runtime
    assert "rm -rf /usr/lib/python*/ensurepip" in builder
    for build_only in ("python314-pip", "/build-env", "/wheels"):
        assert build_only not in runtime
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
            data = {"streams": [{"codec_type": "audio", "tags": {"language": "eng"}}]}
        else:
            data = {"tracks": [{"properties": {"language": "eng"}}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode())

    monkeypatch.setattr("scripts.check_runtime_image._run_command", run_command)
    check_media_tools(tmp_path)
    assert [command[0] for command in commands] == [
        "ffmpeg",
        "ffprobe",
        "mkvpropedit",
        "mkvmerge",
        "ffmpeg",
        "ffmpeg",
        "ffprobe",
    ]
    assert "anullsrc=r=8000:cl=mono" in commands[0]
    assert str(tmp_path / "smoke.mkv") in commands[0]
    assert "alac" in commands[4]
    assert "copy" in commands[5] and "language=eng" in commands[5]
    assert str(tmp_path / "tagged.mp4") in commands[6]


def test_runtime_smoke_rejects_missing_mp4_language_tag(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.check_runtime_image.shutil.which", lambda name: name)

    def run_command(*args):
        if args[0] == "ffprobe":
            data = {"streams": [{"codec_type": "audio"}]}
        else:
            data = {"tracks": [{"properties": {"language": "eng"}}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode())

    monkeypatch.setattr("scripts.check_runtime_image._run_command", run_command)
    with pytest.raises(RuntimeError, match="MP4 stream-copy language edit was not applied"):
        check_media_tools(tmp_path)


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
    assert scan["env"]["TRIVY_LIST_ALL_PKGS"] == "true"
    coverage = next(step for step in steps if "--require-native-runtime" in step.get("run", ""))
    assert steps.index(scan) < steps.index(coverage)


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


def runtime_inventory():
    return {
        "Metadata": {"OS": {"Family": "opensuse-tumbleweed"}},
        "Results": [
            {
                "Class": "os-pkgs",
                "Type": "opensuse-tumbleweed",
                "Packages": [
                    {"Name": name}
                    for name in ("python314-base", "ffmpeg-8", "mkvtoolnix", "libxml2-16")
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "missing", [None, "python314-base", "ffmpeg-8", "mkvtoolnix", "libxml2-16"]
)
def test_runtime_inventory_requires_native_os_and_media_packages(tmp_path, missing):
    data = runtime_inventory()
    if missing:
        packages = data["Results"][0]["Packages"]
        data["Results"][0]["Packages"] = [item for item in packages if item["Name"] != missing]
    report = tmp_path / "image.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    if missing:
        with pytest.raises(ValueError, match="incomplete"):
            validate_runtime_inventory(report)
    else:
        validate_runtime_inventory(report)


@pytest.mark.parametrize("data", [{}, {"Metadata": {"OS": {"Family": "arch"}}}])
def test_runtime_inventory_rejects_missing_or_unsupported_os(tmp_path, data):
    report = tmp_path / "image.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="expected native runtime OS"):
        validate_runtime_inventory(report)


def test_local_image_scan_rejects_missing_report(capsys):
    result = scan_image(
        "ortflix-bot-addons:scan",
        run=lambda command, check: subprocess.CompletedProcess(command, 0),
    )
    assert result == 1
    assert "Native inventory report unavailable" in capsys.readouterr().err


def test_local_image_scan_returns_success_and_handles_missing_trivy(capsys):
    def clean_scan(command, check):
        assert "--list-all-pkgs" in command
        report = Path(command[command.index("--output") + 1])
        report.write_text(json.dumps(runtime_inventory()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    success = scan_image(
        "ortflix-bot-addons:scan",
        run=clean_scan,
    )
    assert success == 0
    assert "No HIGH/CRITICAL findings" in capsys.readouterr().out

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


def test_dependency_maintenance_keeps_approval_and_nightly_scans():
    assert workflow("security.yml")["on"]["schedule"] == [{"cron": "0 3 * * *"}]
    approval = workflow("dependabot-auto-approve.yaml")
    job = approval["jobs"]["approve"]
    assert "github.actor == 'dependabot[bot]'" in job["if"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in job["if"]
    assert approval["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert len(job["steps"]) == 1
    script = job["steps"][0]["with"]["script"]
    assert "event: 'APPROVE'" in script
    assert "enablePullRequestAutoMerge" not in script
    assert "merge(" not in script


def test_weekly_updates_reuse_branches_and_dispatch_ci_without_automerge():
    maintenance = workflow("dependency-updates.yml")
    assert maintenance["on"]["schedule"] == [{"cron": "0 4 * * 1"}]
    assert set(maintenance["on"]) == {"schedule", "workflow_dispatch"}
    job = maintenance["jobs"]["propose"]
    assert job["strategy"]["matrix"]["group"] == ["runtime", "build-tools"]
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] == "false"
    pr = next(step for step in job["steps"] if step.get("id") == "pr")
    assert pr["with"]["branch"] == "${{ env.UPDATE_BRANCH }}"
    assert "branch-suffix" not in pr["with"]
    assert pr["with"]["add-paths"] == (
        "${{ matrix.group == 'runtime' && 'dockerfile' || 'build-constraints.txt' }}"
    )
    dispatch = job["steps"][-1]
    assert "'created'" in dispatch["if"] and "'updated'" in dispatch["if"]
    assert "gh workflow run lint.yml" in dispatch["run"]
    assert "gh workflow run security.yml" in dispatch["run"]
    assert '-f history_base="$HISTORY_BASE" -f history_head="$HISTORY_HEAD"' in dispatch["run"]
    assert dispatch["env"]["HISTORY_HEAD"] == "${{ steps.pr.outputs.pull-request-head-sha }}"
    history = workflow("security.yml")["jobs"]["trufflehog"]
    assert "workflow_dispatch" in history["if"]
    assert "inputs.history_base" in history["if"] and "inputs.history_head" in history["if"]
    assert history["steps"][0]["with"]["ref"] == (
        "${{ github.event.pull_request.head.sha || inputs.history_head || github.sha }}"
    )
    assert "^[0-9a-f]{40}$" in history["steps"][-1]["run"]
    dependabot = yaml.load(
        (PROJECT_ROOT / ".github" / "dependabot.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert {entry["package-ecosystem"] for entry in dependabot["updates"]} == {
        "pip",
        "github-actions",
    }


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


@pytest.mark.parametrize(
    "base,head,checkout,valid",
    [
        ("a" * 40, "b" * 40, "b" * 40, True),
        ("not-a-commit", "b" * 40, "b" * 40, False),
        ("a" * 40, "", "b" * 40, False),
        ("a" * 40, "b" * 40, "c" * 40, False),
    ],
)
def test_history_scan_fails_closed_before_scanner_for_invalid_ranges(
    tmp_path, base, head, checkout, valid
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Requires Bash for history-scan guard validation")
    script = tmp_path / "history.sh"
    log = tmp_path / "scanner-called"
    command = workflow("security.yml")["jobs"]["trufflehog"]["steps"][-1]["run"]
    script.write_text(
        'git() { if [[ "$1" == rev-parse ]]; then printf "%s\\n" "$CHECKOUT_SHA"; fi; }\n'
        'docker() { printf "called\\n" >> "$HISTORY_LOG"; }\n' + command,
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        BASE_COMMIT=base,
        HEAD_COMMIT=head,
        CHECKOUT_SHA=checkout,
        HISTORY_LOG=log.as_posix(),
        RUNNER_TEMP=tmp_path.as_posix(),
    )
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", script.as_posix()],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is valid
    assert log.exists() is valid


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
    summary = next(
        step for step in steps if step.get("name") == "Verify native runtime scan coverage"
    )
    assert summary["if"] == "always()"
    assert summary["env"]["SCAN_OUTCOME"] == "${{ steps.candidate_scan.outcome }}"
    assert '--outcome "$SCAN_OUTCOME"' in summary["run"]


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
        "!config/arr_webhooks.yaml",
        "!config/tautulli_watched_body.json",
        "!integrations",
        "integrations/**",
        "!integrations/*.py",
        "!scripts",
        "scripts/**",
        "!scripts/*.py",
        "scripts/update_dependencies.py",
        "scripts/release_version.py",
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
        "scripts/update_dependencies.py",
        "scripts/release_version.py",
        "config/bot_settings.local.yaml",
        "config/private.yaml",
        "integrations/private.json",
        "docker/private.env",
        "docker/build_ffmpeg.sh",
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
