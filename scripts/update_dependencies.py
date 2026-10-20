"""Propose coordinated native-runtime or Python build-tool updates."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shlex
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

REPOSITORY = "https://download.opensuse.org/tumbleweed/repo/oss/"
BASE_IMAGE = re.compile(r"(?m)^FROM opensuse/tumbleweed@sha256:[0-9a-f]{64} AS base$")
RPM_PIN = re.compile(r"([a-z][A-Za-z0-9+_.-]*)=([0-9][A-Za-z0-9+_.~-]*)")
RPM_EDITION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:\+git[0-9]+\.[a-z0-9]+)?-[0-9]+(?:\.[0-9]+)*")


def download(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def runtime_pins(dockerfile: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for token in shlex.split(dockerfile.replace("\\\n", " "), comments=True):
        match = RPM_PIN.fullmatch(token)
        if match:
            name, edition = match.groups()
            if name in pins:
                raise ValueError("Duplicate runtime pin: " + name)
            pins[name] = edition
    if not pins or not BASE_IMAGE.search(dockerfile):
        raise ValueError("Expected a pinned openSUSE base and RPM packages.")
    return pins


def edition_key(edition: str) -> tuple[Version, Version]:
    """Only automate stable numeric/git editions with numeric RPM revisions."""
    if not RPM_EDITION.fullmatch(edition):
        raise ValueError("RPM edition requires manual review: " + edition)
    version, revision = edition.rsplit("-", 1)
    return Version(version), Version(revision)


def latest_base_digest() -> str:
    token = json.loads(
        download(
            "https://auth.docker.io/token?service=registry.docker.io"
            "&scope=repository:opensuse/tumbleweed:pull"
        )
    )["token"]
    payload = download(
        "https://registry-1.docker.io/v2/opensuse/tumbleweed/manifests/latest",
        {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.oci.image.index.v1+json, "
            "application/vnd.docker.distribution.manifest.list.v2+json",
        },
    )
    entries = json.loads(payload).get("manifests", [])
    if not any(
        entry.get("platform", {}).get("architecture") == "amd64"
        and entry.get("platform", {}).get("os") == "linux"
        for entry in entries
    ):
        raise ValueError("Upstream base manifest does not offer Linux amd64.")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def parse_rpm_versions(stream, names: set[str]) -> dict[str, str]:
    namespace = "{http://linux.duke.edu/metadata/common}"
    versions: dict[str, str] = {}
    for _, element in ET.iterparse(stream, events=("end",)):
        if element.tag != namespace + "package":
            continue
        name = element.findtext(namespace + "name")
        if name in names and element.findtext(namespace + "arch") in {"x86_64", "noarch"}:
            version = element.find(namespace + "version")
            if version is None or version.get("epoch", "0") != "0":
                raise ValueError("Missing version or unsupported RPM epoch: " + name)
            edition = version.attrib["ver"] + "-" + version.attrib["rel"]
            if name not in versions or edition_key(edition) > edition_key(versions[name]):
                versions[name] = edition
        element.clear()
    missing = names - versions.keys()
    if missing:
        raise ValueError("Runtime packages missing upstream: " + ", ".join(sorted(missing)))
    return versions


def latest_rpm_versions(names: set[str]) -> dict[str, str]:
    namespace = {"repo": "http://linux.duke.edu/metadata/repo"}
    metadata = ET.fromstring(download(REPOSITORY + "repodata/repomd.xml"))
    primary = metadata.find("repo:data[@type='primary']", namespace)
    if primary is None:
        raise ValueError("Repository has no primary package metadata.")
    location = primary.find("repo:location", namespace)
    checksum = primary.find("repo:checksum", namespace)
    if location is None or checksum is None:
        raise ValueError("Repository metadata lacks location or checksum.")
    href = location.attrib["href"]
    if not href.startswith("repodata/") or ".." in href:
        raise ValueError("Unexpected repository metadata location.")
    payload = download(REPOSITORY + href)
    algorithm = checksum.get("type")
    if algorithm not in {"sha256", "sha512"}:
        raise ValueError("Repository metadata requires a SHA-256 or SHA-512 checksum.")
    if hashlib.new(algorithm, payload).hexdigest() != checksum.text:
        raise ValueError("Repository metadata checksum mismatch.")
    if href.endswith(".zst"):
        try:
            from compression import zstd
        except ImportError:
            raise ValueError("Runtime metadata updates require Python 3.14 or newer.") from None
        with zstd.ZstdFile(io.BytesIO(payload)) as stream:
            return parse_rpm_versions(stream, names)
    if href.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            return parse_rpm_versions(stream, names)
    raise ValueError("Unsupported repository metadata compression.")


def update_runtime(dockerfile: str, digest: str, versions: dict[str, str]) -> str:
    pins = runtime_pins(dockerfile)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Invalid upstream image digest.")
    result = BASE_IMAGE.sub("FROM opensuse/tumbleweed@" + digest + " AS base", dockerfile)
    for name, current in pins.items():
        candidate = versions.get(name)
        if candidate is None or edition_key(candidate) < edition_key(current):
            raise ValueError("Missing or older upstream RPM candidate: " + name)
        pattern = r"(?<![A-Za-z0-9_.+-])" + re.escape(name + "=" + current) + r"(?=\s|$)"
        result, count = re.subn(pattern, name + "=" + candidate, result)
        if count != 1:
            raise ValueError("Ambiguous runtime pin: " + name)
    return result


def latest_python_version(metadata: dict, python_versions: list[str]) -> Version:
    candidates = []
    for value, files in metadata["releases"].items():
        try:
            version = Version(value)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease or version.local is not None:
            continue
        if any(
            not artifact.get("yanked", False)
            and all(
                Version(python) in SpecifierSet(artifact.get("requires_python") or "")
                for python in python_versions
            )
            for artifact in files
        ):
            candidates.append(version)
    if not candidates:
        raise ValueError("No stable compatible Python package release is available.")
    return max(candidates)


def update_build_tools(constraints: str, python_versions: list[str]) -> str:
    lines = []
    for line in constraints.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        requirement = Requirement(line)
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==":
            raise ValueError("Build tools must use exact version pins.")
        current = Version(specifiers[0].version)
        metadata = json.loads(download(f"https://pypi.org/pypi/{requirement.name}/json"))
        candidate = latest_python_version(metadata, python_versions)
        if candidate < current:
            raise ValueError("No compatible non-downgrade build-tool update: " + requirement.name)
        lines.append(line if candidate == current else f"{requirement.name}=={candidate}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("runtime", "build-tools"), required=True)
    parser.add_argument("--apply", action="store_true", help="Write proposed updates locally")
    parser.add_argument(
        "--python-version", action="append", help="Required build interpreter version"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / ("dockerfile" if args.group == "runtime" else "build-constraints.txt")
    try:
        original = path.read_text(encoding="utf-8")
        if args.group == "runtime":
            updated = update_runtime(
                original, latest_base_digest(), latest_rpm_versions(set(runtime_pins(original)))
            )
        else:
            updated = update_build_tools(original, args.python_version or ["3.11", "3.14"])
        if updated == original:
            print("No dependency changes.")
        elif args.apply:
            path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"Updated {path.name}; CI and manual merge are required.")
        else:
            print(f"Updates available for {path.name}; rerun with --apply to write them.")
    except (OSError, ValueError, KeyError, ET.ParseError, urllib.error.URLError) as error:
        print(
            f"Dependency update failed ({type(error).__name__}); no update was applied.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
