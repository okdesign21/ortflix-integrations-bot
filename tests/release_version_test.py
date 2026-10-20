"""Release ordering and edge versions never require publication or a registry."""

import pytest

from scripts.release_version import edge_version, parse_tag, require_newer_tag


@pytest.mark.parametrize("tag", ["v1.2.3", "v1.2.3-beta.1", "v1.2.3-alpha.2", "v1.2.3rc1"])
def test_edge_versions_increment_the_numeric_patch_for_stable_and_prerelease_tags(tag):
    assert edge_version(tag, 7) == "1.2.4.dev7"


def test_final_release_is_newer_than_its_prereleases():
    require_newer_tag("v1.2.3", ["v1.2.2", "v1.2.3-alpha.1", "v1.2.3-beta.1", "v1.2.3rc2"])
    require_newer_tag("v1.2.3-beta.2", ["v1.2.3-alpha.1", "v1.2.3-beta.1"])


@pytest.mark.parametrize("tag", ["v1.2.3-beta.1", "v1.2.2", "v1.2.3rc1"])
def test_release_gate_rejects_versions_below_an_existing_final_release(tag):
    with pytest.raises(ValueError, match="newer"):
        require_newer_tag(tag, ["v1.2.3"])


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3.dev1", "v1.2.3+local", "v1.2.3.post1"])
def test_release_tags_reject_unsupported_version_shapes(tag):
    with pytest.raises(ValueError):
        parse_tag(tag)
