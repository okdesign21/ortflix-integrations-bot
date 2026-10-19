"""Media containment checks use temporary files, never native media tools."""

import importlib
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from integration_utils import validate_media_path  # noqa: E402


def test_media_path_accepts_regular_file(tmp_path):
    media = tmp_path / "episode.MKV"
    media.touch()
    assert validate_media_path(str(media), [str(tmp_path)], {"mkv"}) == str(media.resolve())


@pytest.mark.parametrize("raw_path", ["", "episode.mkv", "-episode.mkv", "../episode.mkv"])
def test_media_path_rejects_non_absolute_input(tmp_path, raw_path):
    with pytest.raises(ValueError, match="absolute"):
        validate_media_path(raw_path, [str(tmp_path)], {"mkv"})


@pytest.mark.parametrize(
    "kind", ["outside", "prefix", "directory", "missing", "extension", "traversal"]
)
def test_media_path_rejects_unsafe_inputs(tmp_path, kind):
    root = tmp_path / "library"
    root.mkdir()
    paths = {
        "outside": tmp_path / "outside.mkv",
        "prefix": tmp_path / "library-other" / "episode.mkv",
        "directory": root / "directory.mkv",
        "missing": root / "missing.mkv",
        "extension": root / "episode.txt",
        "traversal": root / ".." / "outside.mkv",
    }
    candidate = paths[kind]
    if kind == "directory":
        candidate.mkdir()
    elif kind not in {"missing", "traversal"}:
        candidate.parent.mkdir(exist_ok=True)
        candidate.touch()
    with pytest.raises(ValueError):
        validate_media_path(str(candidate), [str(root)], {"mkv"})


@pytest.mark.parametrize("roots", [[], ["relative"], [""], "not-a-list"])
def test_media_path_rejects_invalid_roots(tmp_path, roots):
    media = tmp_path / "episode.mkv"
    media.touch()
    with pytest.raises(ValueError):
        validate_media_path(str(media), roots, {"mkv"})


@pytest.mark.parametrize("directory", [False, True])
def test_media_path_rejects_symlink_escape(tmp_path, directory):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.mkv"
    if directory:
        folder = tmp_path / "outside"
        folder.mkdir()
        outside = folder / "episode.mkv"
    outside.touch()
    link = root / ("folder" if directory else "episode.mkv")
    try:
        link.symlink_to(outside.parent if directory else outside, target_is_directory=directory)
    except OSError:
        pytest.skip("Creating symlinks requires platform permissions")
    with pytest.raises(ValueError, match="outside"):
        validate_media_path(str(link / "episode.mkv" if directory else link), [str(root)], {"mkv"})


def test_media_path_rejects_unreadable_files(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.touch()
    monkeypatch.setattr("integration_utils.os.access", lambda *args: False)
    with pytest.raises(ValueError, match="readable regular file"):
        validate_media_path(str(media), [str(tmp_path)], {"mkv"})


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
@pytest.mark.parametrize("allowed", [True, False])
def test_webhook_audio_checks_containment_before_tools(
    service, allowed, tmp_path, monkeypatch, capsys
):
    module = importlib.import_module(f"audio_fix_{service}")
    root = tmp_path / "library"
    root.mkdir()
    media = (root if allowed else tmp_path) / "episode.mkv"
    media.touch()
    monkeypatch.setenv(f"{service}_eventtype", "Download")
    monkeypatch.setenv(
        f"{service}_{'series' if service == 'sonarr' else 'movie'}_originallanguage", "en"
    )
    path_variable = "sonarr_episodefile_path" if service == "sonarr" else "radarr_moviefile_path"
    monkeypatch.setenv(path_variable, str(media))
    monkeypatch.setattr(module, "TV_DIRS" if service == "sonarr" else "MOVIES_DIRS", [str(root)])
    tools = Mock(return_value=True if service == "sonarr" else [])
    monkeypatch.setattr(module, "_fix_mkv" if service == "sonarr" else "audio_streams", tools)
    analyze = Mock()
    monkeypatch.setattr(module, "plex_analyze_item", analyze)
    monkeypatch.setattr(module, "telegram_post", Mock())
    if service == "sonarr" and allowed:
        module.main()
    else:
        with pytest.raises(SystemExit) as error:
            (module.main if service == "sonarr" else module.run_radarr_mode)()
        assert error.value.code == (0 if allowed else 1)
    if allowed:
        assert tools.call_args.args[0] == str(media.resolve())
    else:
        tools.assert_not_called()
        analyze.assert_not_called()
        output = capsys.readouterr()
        assert "outside allowed media roots" in output.err
        assert str(media) not in output.err
