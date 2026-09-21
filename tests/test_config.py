"""The voice is off unless a person turns it on in their own configuration.

2026-09-14: other people must not get one person's voice by default; whoever
wants a voice sets it explicitly in config, on their own machine."""
import pytest

from writing_register import config as c


def _config(tmp_path, monkeypatch, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(path))
    return path


def _voices(tmp_path, monkeypatch, **files):
    d = tmp_path / "voices"
    d.mkdir()
    for name, body in files.items():
        (d / f"{name}.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(c, "VOICES_DIR", d)
    return d


def test_no_config_file_means_no_voice():
    assert c.load_config().voice is None


def test_a_voice_name_resolves_to_the_voice_shipped_in_the_clone(tmp_path, monkeypatch):
    d = _voices(tmp_path, monkeypatch, colleague="# Voice\n")
    _config(tmp_path, monkeypatch, 'voice = "colleague"\n')
    assert c.load_config().voice == d / "colleague.md"


@pytest.mark.parametrize("value", ["none", "None", ""])
def test_voice_none_or_empty_means_no_voice(tmp_path, monkeypatch, value):
    _config(tmp_path, monkeypatch, f'voice = "{value}"\n')
    assert c.load_config().voice is None


def test_a_relative_voice_path_is_read_from_the_config_folder(tmp_path, monkeypatch):
    (tmp_path / "mine.md").write_text("# Mine\n", encoding="utf-8")
    _config(tmp_path, monkeypatch, 'voice = "./mine.md"\n')
    assert c.load_config().voice == tmp_path / "mine.md"


def test_a_home_relative_voice_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "v.md").write_text("# V\n", encoding="utf-8")
    _config(tmp_path, monkeypatch, 'voice = "~/v.md"\n')
    assert c.load_config().voice == tmp_path / "v.md"


def test_an_unknown_voice_name_is_an_error_that_lists_the_known_ones(tmp_path, monkeypatch):
    _voices(tmp_path, monkeypatch, colleague="# Voice\n")
    _config(tmp_path, monkeypatch, 'voice = "colleagu"\n')
    with pytest.raises(c.ConfigError, match="colleague"):
        c.load_config()


def test_a_voice_path_that_does_not_exist_is_an_error(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, 'voice = "./gone.md"\n')
    with pytest.raises(c.ConfigError, match="gone.md"):
        c.load_config()


def test_an_unknown_key_is_an_error_not_silence(tmp_path, monkeypatch):
    """A typo such as `vocie` must not quietly mean "no voice"."""
    _config(tmp_path, monkeypatch, 'vocie = "colleague"\n')
    with pytest.raises(c.ConfigError, match="vocie"):
        c.load_config()


def test_malformed_toml_is_an_error_naming_the_file(tmp_path, monkeypatch):
    path = _config(tmp_path, monkeypatch, 'voice = "colleague\n')
    with pytest.raises(c.ConfigError, match=str(path.name)):
        c.load_config()


def test_the_default_location_follows_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.delenv("WR_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert c.config_path() == tmp_path / "writing-register" / "config.toml"


def test_without_xdg_the_default_location_is_dot_config(tmp_path, monkeypatch):
    monkeypatch.delenv("WR_CONFIG")
    # conftest points XDG_CONFIG_HOME at an empty directory, which is what keeps
    # the machine's git ignore file out of the suite; this test is about the
    # path used when the variable is absent, so it removes it here.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert c.config_path() == tmp_path / ".config" / "writing-register" / "config.toml"


def test_the_core_is_the_text_above_the_marker():
    text = f"# Voice\n\nRule one.\n\n{c.CORE_MARKER}\n\n## Evidence\n\nLong.\n"
    assert c.voice_core(text) == "# Voice\n\nRule one.\n"


def test_a_voice_without_the_marker_is_all_core():
    assert c.voice_core("# Voice\n\nRule one.\n") == "# Voice\n\nRule one.\n"


def test_no_auto_setting_means_nothing_runs_automatically(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, 'voice = "none"\n')
    assert c.load_config().auto == frozenset()


def test_auto_lists_what_wr_rewrites_automatically(tmp_path, monkeypatch):
    """Since 2026-09-15: markdown files, commit messages and PR descriptions."""
    _config(tmp_path, monkeypatch, 'auto = ["markdown", "commit", "pr"]\n')
    assert c.load_config().auto == {"markdown", "commit", "pr"}


def test_an_unknown_auto_value_is_an_error(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, 'auto = ["markdown", "chat"]\n')
    with pytest.raises(c.ConfigError, match="chat"):
        c.load_config()


def test_auto_must_be_a_list(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, 'auto = "markdown"\n')
    with pytest.raises(c.ConfigError, match="auto"):
        c.load_config()


def test_the_shipped_voices_parse_and_have_a_core():
    for f in sorted(c.VOICES_DIR.glob("*.md")):
        assert c.voice_core(f.read_text(encoding="utf-8")).strip(), f.name
