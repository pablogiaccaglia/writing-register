"""Copy the humanizer skill and the shipped voices into the built package.

wr reads them from the repository root in a clone. A wheel has no repository
root, so the build copies vendor/humanizer/ and voice/ into
writing_register/_data/, where writing_register.resources finds them. Everything
else about the package is declared in pyproject.toml.

In a git checkout only the files git tracks are copied, so a voice being
drafted in voice/ never ships; the target is cleared first, so a file deleted
from the source does not linger from an earlier build (audit, 2026-09-22).
"""
import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
SHIPPED = (Path("vendor") / "humanizer", Path("voice"))


def _files(rel: Path) -> list[Path]:
    """The files under `rel` to ship: the tracked ones in a git checkout,
    otherwise every file (an sdist has no git metadata)."""
    if (HERE / ".git").exists():
        out = subprocess.run(["git", "ls-files", "-z", "--", str(rel)], cwd=HERE,
                             capture_output=True, check=True).stdout
        return [Path(p) for p in out.decode().split("\0") if p]
    return [p.relative_to(HERE) for p in (HERE / rel).rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]


class BuildWithData(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "writing_register" / "_data"
        shutil.rmtree(target, ignore_errors=True)
        for rel in SHIPPED:
            files = _files(rel)
            if not files:
                raise SystemExit(f"{HERE / rel} has no files; the package needs it")
            for f in files:
                dest = target / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / f, dest)


setup(cmdclass={"build_py": BuildWithData})
