import json

import pytest

from aistudio import paths
from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import ValidationError


def test_home_dir_uses_env(aistudio_home):
    assert paths.home_dir() == aistudio_home


def test_home_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AISTUDIO_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    assert paths.home_dir() == tmp_path / ".aistudio"


def test_profile_precedence(aistudio_home, monkeypatch):
    assert paths.resolve_profile_name() == "default"
    paths.set_default_profile("fromconfig")
    assert paths.resolve_profile_name() == "fromconfig"
    monkeypatch.setenv("AISTUDIO_PROFILE", "fromenv")
    assert paths.resolve_profile_name() == "fromenv"
    assert paths.resolve_profile_name("fromcli") == "fromcli"


@pytest.mark.parametrize("name", ["../x", "a/b", "", ".hidden", "x" * 65, "a..b", "sp ace"])
def test_rejects_unsafe_profile_names(aistudio_home, name):
    with pytest.raises(ValidationError):
        paths.get_profile(name)


def test_profile_paths(aistudio_home):
    p = paths.get_profile("work")
    assert p.root == aistudio_home / "profiles" / "work"
    assert p.storage_state == p.root / "storage_state.json"
    assert p.session == p.root / "session.json"
    assert p.context == p.root / "context.json"
    assert p.jobs == p.root / "jobs.json"
    assert p.models == p.root / "models.json"
    assert p.browser_json == p.root / "browser.json"
    assert p.chrome_dir == p.root / "chrome"
    assert not p.root.exists()
    assert p.ensure().root.is_dir()


def test_list_profiles(aistudio_home):
    assert paths.list_profiles() == []
    paths.get_profile("b").ensure()
    paths.get_profile("a").ensure()
    assert paths.list_profiles() == ["a", "b"]


def test_clear_default_profile(aistudio_home):
    paths.set_default_profile("work")
    paths.clear_default_profile("other")
    assert paths.load_config()["default_profile"] == "work"
    paths.clear_default_profile("work")
    assert "default_profile" not in paths.load_config()


def test_write_private_json_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "dir" / "secret.json"
    write_private_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(target.parent.glob("*.tmp"))
    write_private_json(target, {"a": 2})
    assert read_json(target) == {"a": 2}


def test_corrupt_config_is_a_validation_error_naming_the_file(aistudio_home):
    aistudio_home.mkdir(parents=True)
    (aistudio_home / "config.json").write_text("{oops")
    with pytest.raises(ValidationError) as info:
        paths.resolve_profile_name()
    assert "config.json" in info.value.message


def _mode(path):
    import stat

    return stat.S_IMODE(path.stat().st_mode)


def test_aistudio_folders_are_private(aistudio_home):
    from aistudio.paths import get_profile, home_dir

    home_dir().mkdir(parents=True, exist_ok=True)
    home_dir().chmod(0o755)  # e.g. created earlier by an older version
    profile = get_profile("x").ensure()
    assert [_mode(p) for p in (home_dir(), home_dir() / "profiles", profile.root)] == [0o700] * 3


def test_private_json_lands_in_a_private_folder(tmp_path):
    from aistudio._fs import write_private_json

    write_private_json(tmp_path / "sub" / "f.json", {"a": 1})
    assert _mode(tmp_path / "sub") == 0o700
