"""Regression checks for private build inputs and publication policies."""

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
