"""`wr` works when installed as a package, away from any clone (2026-09-21).

Until now wr found the humanizer skill and the shipped voices relative to its
own clone, so it worked only from an editable install inside one. The plugin
can be installed straight from GitHub, and its hooks need `wr` on the PATH, so
`wr` has to install in one command too (`uv tool install git+...`, pipx, pip).
This test builds a real wheel, installs it into a fresh virtual environment
outside the clone, and checks that the skill and the example voice load from
the package itself.
"""
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Building a wheel fetches the build backend; the test is skipped offline.
pytestmark = pytest.mark.skipif(os.environ.get("WR_OFFLINE") == "1", reason="needs the network to build a wheel")


def _run(cmd, **kw):
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    assert result.returncode == 0, f"{cmd}\n{result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    # Built from a copy, because setuptools writes build/ beside the source.
    source = tmp_path_factory.mktemp("source") / "writing-register"
    shutil.copytree(REPO, source, ignore=shutil.ignore_patterns(
        ".git", ".venv", "build", "*.egg-info", "__pycache__", ".pytest_cache", "private"))
    wheels = tmp_path_factory.mktemp("wheels")
    _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(source)])
    [wheel] = wheels.glob("writing_register-*.whl")
    env_dir = tmp_path_factory.mktemp("env")
    venv.create(env_dir, with_pip=True)
    python = env_dir / "bin" / "python"
    _run([str(python), "-m", "pip", "install", "-q", "--no-deps", str(wheel)])
    return env_dir


def _in_env(env_dir, code, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env["WR_CONFIG"] = str(tmp_path / "config.toml")
    return _run([str(env_dir / "bin" / "python"), "-c", code], cwd=tmp_path, env=env)


def test_the_package_does_not_import_from_the_clone(installed, tmp_path):
    where = _in_env(installed, "import writing_register; print(writing_register.__file__)", tmp_path)
    assert str(REPO) not in where and "site-packages" in where


def test_the_humanizer_skill_ships_in_the_package(installed, tmp_path):
    out = _in_env(installed, "from writing_register.humanize import load_skill; "
                             "from writing_register.patterns import card; "
                             "s = load_skill(); print(len(s) > 10000, 'Not X but Y' in card())", tmp_path)
    assert out.split() == ["True", "True"]


def test_the_example_voice_resolves_by_name_from_the_package(installed, tmp_path):
    (tmp_path / "config.toml").write_text('voice = "technical-colleague"\n')
    out = _in_env(installed, "from writing_register import voice, config; "
                             "c = config.load_config(); print(voice.read_core(c.voice).splitlines()[0])", tmp_path)
    assert out.strip() == "# Voice: Technical colleague"


def test_the_wr_command_is_installed(installed, tmp_path):
    (tmp_path / "config.toml").write_text('voice = "technical-colleague"\n')
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["WR_CONFIG"] = str(tmp_path / "config.toml")
    out = _run([str(installed / "bin" / "wr"), "voice", "--core"], cwd=tmp_path, env=env)
    assert out.startswith("# Voice: Technical colleague")
