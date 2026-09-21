"""Copy the humanizer skill and the shipped voices into the built package.

wr reads them from the repository root in a clone. A wheel has no repository
root, so the build copies vendor/humanizer/ and voice/ into
writing_register/_data/, where writing_register.resources finds them. Everything
else about the package is declared in pyproject.toml.
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
SHIPPED = (Path("vendor") / "humanizer", Path("voice"))


class BuildWithData(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "writing_register" / "_data"
        for rel in SHIPPED:
            source = HERE / rel
            if not source.is_dir():
                raise SystemExit(f"{source} is missing; the package needs it")
            shutil.copytree(source, target / rel, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))


setup(cmdclass={"build_py": BuildWithData})
