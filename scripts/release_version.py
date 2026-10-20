"""Compare release tags and derive prerelease-aware edge package versions."""

import argparse
import subprocess
import sys

from packaging.version import Version


def parse_tag(tag: str) -> Version:
    if not tag.startswith("v"):
        raise ValueError("Release tags must start with v.")
    version = Version(tag[1:])
    if (
        len(version.release) != 3
        or version.epoch
        or version.local is not None
        or version.is_devrelease
        or version.is_postrelease
    ):
        raise ValueError("Use vMAJOR.MINOR.PATCH with an optional alpha, beta or rc suffix.")
    return version


def require_newer_tag(tag: str, existing: list[str]) -> None:
    candidate = parse_tag(tag)
    for previous in existing:
        if previous == tag:
            continue
        try:
            version = parse_tag(previous)
        except ValueError:
            continue
        if candidate <= version:
            raise ValueError("Release tag must be newer than every existing release version.")


def edge_version(tag: str, count: int) -> str:
    if count < 0:
        raise ValueError("Commit count must not be negative.")
    major, minor, patch = parse_tag(tag).release
    return f"{major}.{minor}.{patch + 1}.dev{count}"


def git_output(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Reject release tags that are not strictly newer")
    check.add_argument("tag", help="Release tag to compare against repository tags")
    commands.add_parser("edge", help="Print the next-patch development version from Git history")
    args = parser.parse_args()
    try:
        if args.command == "check":
            require_newer_tag(args.tag, git_output("tag", "--list", "v*").splitlines())
        else:
            try:
                tag = git_output(
                    "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*.[0-9]*.[0-9]*"
                )
            except subprocess.CalledProcessError:
                tag = "v0.1.0"
            try:
                count = git_output("rev-list", "--count", f"{tag}..HEAD")
            except subprocess.CalledProcessError:
                count = git_output("rev-list", "--count", "HEAD")
            print(edge_version(tag, int(count)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Version resolution failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
