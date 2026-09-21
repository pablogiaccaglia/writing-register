"""Only spawn.py builds a model invocation.

The sandbox is a property of one function, and a second call site that forgets
one flag loses it silently. The docstring of the module this pattern comes from
says "so a new call site can't forget the sandbox" and has no test enforcing it.
This is that test."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
ALLOWED = {"spawn.py"}
BINARIES = {"claude", "codex", "claude-code"}


def _first_element_is_a_binary(node: ast.AST) -> str | None:
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value in BINARIES:
            return first.value
    return None


def spawn_literals(source: str) -> list[str]:
    # ast.walk visits a BinOp and its operands, so the same literal is seen
    # twice for the concatenation form. Unique, order preserved.
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        name = _first_element_is_a_binary(node)
        if name and name not in found:
            found.append(name)
        # ["claude"] + [...] is the same thing wearing a plus sign
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                name = _first_element_is_a_binary(side)
                if name and name not in found:
                    found.append(name)
    return found


def test_the_detector_does_not_catch_the_dynamic_forms():
    """The limit, written down rather than implied.

    This guard reads literals. A command built from a variable, appended to a
    list, interpolated into an f-string, or passed as a shell string is invisible
    to it. Those forms are rarer and uglier and a reviewer notices them; the
    literal is the one somebody writes without thinking. Widening the guard means
    tracking values through the module, which costs more than it returns at this
    size. The test's name says `only spawn.py builds a model invocation`, and
    what it actually enforces is `no other module contains a literal command`."""
    assert spawn_literals('subprocess.run("claude -p", shell=True)') == []
    assert spawn_literals('cmd = []\ncmd.append("claude")') == []
    assert spawn_literals('b = "claude"\ncmd = [b, "-p"]') == []
    assert spawn_literals('cmd = [f"{BIN}", "-p"]') == []


def test_the_detector_fires_on_the_forms_it_must_catch():
    """A guard nobody has seen fail is not a guard."""
    assert spawn_literals('cmd = ["claude", "-p"]') == ["claude"]
    assert spawn_literals('cmd = ("codex", "exec")') == ["codex"]
    assert spawn_literals('cmd = ["claude"] + flags') == ["claude"]
    assert spawn_literals('cmd = flags + ["claude"]') == ["claude"]
    assert spawn_literals('cmd = ["ls", "-l"]') == []


def test_only_spawn_builds_a_model_invocation():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in ALLOWED:
            continue
        found = spawn_literals(path.read_text(encoding="utf-8"))
        if found:
            offenders.append((path.relative_to(SRC).as_posix(), found))
    assert not offenders, (
        f"these modules build a model command outside spawn.py, which means "
        f"they do not get the sandbox: {offenders}")
