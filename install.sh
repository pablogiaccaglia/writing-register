#!/usr/bin/env bash
# Install writing-register: the wr command, and how to load the Claude Code plugin.
#
# The package is installed in editable mode into a virtual environment inside
# this clone, because wr reads the humanizer skill and the voice from here.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HOME}/.local/bin"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required and was not found."; exit 1
fi

echo "1. The wr command"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || python3 -m venv "$HERE/.venv"
# A virtual environment made by uv has no pip; ensurepip adds it.
"$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null
"$PY" -m pip install --quiet -e "$HERE"
mkdir -p "$BIN"
ln -sf "$HERE/.venv/bin/wr" "$BIN/wr"
echo "   linked $BIN/wr"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "   $BIN is not on your PATH; add it to your shell profile." ;;
esac
command -v claude >/dev/null 2>&1 \
  || echo "   wr humanize needs the claude command line tool, which was not found."

echo
echo "2. The Claude Code plugin"
echo "   In Claude Code, run:"
echo
echo "     /plugin marketplace add $HERE"
echo "     /plugin install writing-register@writing-register"
echo
echo "   Then restart Claude Code."
echo
echo "3. Optional: a voice and the automatic rewrites"
echo "   Both go in ~/.config/writing-register/config.toml."
echo "   See \"Setting it up\" in $HERE/README.md."
echo
echo "4. In each repository you use it in"
echo "   Add *.refused.md to .gitignore, then from the repository root:"
echo
echo "     wr humanize --dry-run docs/SOME_DOC.md"
