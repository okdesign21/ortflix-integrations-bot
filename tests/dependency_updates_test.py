"""Offline checks for scheduled dependency proposals."""

import gzip
import hashlib
import io
import json

import pytest

from scripts import update_dependencies
from scripts.update_dependencies import (
    latest_base_digest,
    latest_python_version,
    latest_rpm_versions,
    parse_rpm_versions,
    runtime_pins,
    update_build_tools,
    update_runtime,
)

DOCKERFILE = (
    "FROM opensuse/tumbleweed@sha256:" + "a" * 64 + " AS base\n"
    "RUN zypper install python314-base=3.14.8-1.1 ffmpeg-8=8.1.3-3.1\n"
    "ARG APP_VERSION=0.0.0\n"
)


def test_runtime_proposal_is_idempotent_and_keeps_package_streams():
    pins = runtime_pins(DOCKERFILE)
    assert update_runtime(DOCKERFILE, "sha256:" + "a" * 64, pins) == DOCKERFILE
    updated = update_runtime(
        DOCKERFILE,
        "sha256:" + "b" * 64,
        {"python314-base": "3.14.9-1.1", "ffmpeg-8": "8.1.4-1.1"},
    )
    assert "python314-base=3.14.9-1.1" in updated
    assert "ffmpeg-8=8.1.4-1.1" in updated
    assert "ARG APP_VERSION=0.0.0" in updated
    assert update_runtime(updated, "sha256:" + "b" * 64, runtime_pins(updated)) == updated


@pytest.mark.parametrize("candidate", [None, "8.1.2-1.1", "8.1.4~rc1-1.1"])
def test_runtime_proposal_rejects_missing_older_or_unsupported_editions(candidate):
    pins = runtime_pins(DOCKERFILE)
    pins["ffmpeg-8"] = candidate
    with pytest.raises(ValueError):
        update_runtime(DOCKERFILE, "sha256:" + "a" * 64, pins)


def test_rpm_metadata_selects_requested_native_packages_and_requires_all():
    stream = io.BytesIO(
        b'<metadata xmlns="http://linux.duke.edu/metadata/common">'
        b"<package><name>ffmpeg-8</name><arch>x86_64</arch>"
        b'<version epoch="0" ver="8.1.3" rel="3.1"/></package>'
        b"<package><name>ffmpeg-8</name><arch>aarch64</arch>"
        b'<version epoch="0" ver="8.1.4" rel="1.1"/></package></metadata>'
    )
    assert parse_rpm_versions(stream, {"ffmpeg-8"}) == {"ffmpeg-8": "8.1.3-3.1"}
    stream.seek(0)
    with pytest.raises(ValueError, match="missing upstream"):
        parse_rpm_versions(stream, {"ffmpeg-8", "python314-base"})


def test_python_updates_skip_prereleases_yanked_and_incompatible_versions(monkeypatch):
    metadata = {
        "releases": {
            "1.0": [{"requires_python": ">=3.11"}],
            "1.1": [{"requires_python": ">=3.11"}],
            "1.2": [{"yanked": True}],
            "2.0": [{"requires_python": ">=3.14"}],
            "3.0rc1": [{}],
        }
    }
    assert str(latest_python_version(metadata, ["3.11", "3.14"])) == "1.1"
    monkeypatch.setattr("scripts.update_dependencies.download", lambda url: json.dumps(metadata))
    updated = update_build_tools("# policy\nexample==1.0\n", ["3.11", "3.14"])
    assert updated == "# policy\nexample==1.1\n"
    assert update_build_tools(updated, ["3.11", "3.14"]) == updated


def test_base_digest_is_bound_to_the_official_amd64_manifest(monkeypatch):
    payload = json.dumps(
        {"manifests": [{"platform": {"architecture": "amd64", "os": "linux"}}]}
    ).encode()

    def fetch(url, headers=None):
        if url.startswith("https://auth.docker.io/token?"):
            return b'{"token": "synthetic-test-token"}'
        assert url == "https://registry-1.docker.io/v2/opensuse/tumbleweed/manifests/latest"
        assert headers["Authorization"] == "Bearer synthetic-test-token"
        return payload

    monkeypatch.setattr(update_dependencies, "download", fetch)
    assert latest_base_digest() == "sha256:" + hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("valid_checksum", [False, True])
def test_repository_metadata_checksum_is_verified(monkeypatch, valid_checksum):
    payload = gzip.compress(
        b'<metadata xmlns="http://linux.duke.edu/metadata/common">'
        b"<package><name>ffmpeg-8</name><arch>x86_64</arch>"
        b'<version epoch="0" ver="8.1.3" rel="3.1"/></package></metadata>'
    )
    checksum = hashlib.sha256(payload).hexdigest() if valid_checksum else "0" * 64
    metadata = (
        '<repomd xmlns="http://linux.duke.edu/metadata/repo"><data type="primary">'
        '<location href="repodata/primary.xml.gz"/>'
        f'<checksum type="sha256">{checksum}</checksum></data></repomd>'
    ).encode()
    monkeypatch.setattr(
        update_dependencies,
        "download",
        lambda url: metadata if url.endswith("repomd.xml") else payload,
    )
    if valid_checksum:
        assert latest_rpm_versions({"ffmpeg-8"}) == {"ffmpeg-8": "8.1.3-3.1"}
    else:
        with pytest.raises(ValueError, match="checksum mismatch"):
            latest_rpm_versions({"ffmpeg-8"})


@pytest.mark.parametrize("failure", [False, True])
def test_unchanged_or_failed_updates_do_not_write_files(tmp_path, monkeypatch, capsys, failure):
    dockerfile = tmp_path / "dockerfile"
    dockerfile.write_text(DOCKERFILE, encoding="utf-8")
    modified = dockerfile.stat().st_mtime_ns
    monkeypatch.setattr(update_dependencies, "__file__", str(tmp_path / "scripts" / "update.py"))
    monkeypatch.setattr(
        update_dependencies.sys, "argv", ["update.py", "--group", "runtime", "--apply"]
    )
    monkeypatch.setattr(update_dependencies, "latest_base_digest", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr(
        update_dependencies,
        "latest_rpm_versions",
        lambda names: {} if failure else runtime_pins(DOCKERFILE),
    )
    assert update_dependencies.main() == (1 if failure else 0)
    assert dockerfile.read_text(encoding="utf-8") == DOCKERFILE
    assert dockerfile.stat().st_mtime_ns == modified
    output = capsys.readouterr()
    assert (
        "no update was applied" in output.err if failure else "No dependency changes" in output.out
    )
