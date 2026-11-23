"""The connector starts no process, and the test run makes sure of it"""

import ast
import os
import subprocess
import webbrowser
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "start",
    [
        lambda: subprocess.run(["true"], check=False),
        lambda: os.system("true"),
        lambda: os.popen("true"),
        lambda: webbrowser.open("https://example.fi"),
    ],
)
def test_a_process_start_fails_the_test(start: Callable[[], object]) -> None:
    with pytest.raises(RuntimeError, match="started a process"):
        start()


#: browser.py: ruff's ban is off for the whole file, so this test holds the rule there
BROWSER = Path(__file__).parent.parent / "src" / "odmdm_connector" / "browser.py"
#: The modules browser.py may import, and the only things of webbrowser it may use
BROWSER_IMPORTS = frozenset({"html", "logging", "os", "sys", "webbrowser", "pathlib"})
BROWSER_OPENS = frozenset({"open", "Error"})


def _starts(name: str) -> bool:
    """Whether os.<name> starts a process"""
    return name in {"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "startfile"} or (
        name.startswith(("exec", "spawn"))
    )


def _breaches(source: str) -> list[str]:
    """What the source does beyond opening a browser, read without importing it"""
    tree = ast.parse(source)
    found: list[str] = []
    #: The names os and webbrowser are bound to, as imported
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in BROWSER_IMPORTS:
                    found.append(f"import {alias.name}")
                if alias.name in ("os", "webbrowser"):
                    bound[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if module.split(".")[0] not in BROWSER_IMPORTS:
                found.append(f"from {module} import")
            for alias in node.names:
                if (module == "os" and _starts(alias.name)) or (
                    module == "webbrowser" and alias.name not in BROWSER_OPENS
                ):
                    found.append(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.append("__import__")
    used = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
            module = bound[node.value.id]
            if (module == "os" and _starts(node.attr)) or (module == "webbrowser" and node.attr not in BROWSER_OPENS):
                found.append(f"{module}.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in bound and id(node) not in used:
            # Handed on whole, as to getattr, the module could reach anything in it
            found.append(f"{bound[node.id]} itself")
    return found


def test_browser_py_only_opens_a_browser() -> None:
    assert _breaches(BROWSER.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess",
        "import multiprocessing.pool",
        "import pty",
        "import importlib",
        "from . import store",
        "from .os import system",
        "from asyncio import create_subprocess_exec",
        "__import__('subprocess')",
        "import os\nos.posix_spawn('x', [], {})",
        "import os\nos.fork()",
        "import os\nos.execv('x', [])",
        "import os\nos.spawnlp(0, 'x')",
        "import os as o\no.system('x')",
        "import os\ngetattr(os, 'system')('x')",
        "from os import popen",
        "from os import execvp",
        "import webbrowser\nwebbrowser.get('x')",
        "import webbrowser\nwebbrowser.open_new_tab('x')",
        "from webbrowser import BackgroundBrowser",
    ],
)
def test_the_guard_on_browser_py_finds_a_process_start(source: str) -> None:
    assert _breaches(source)


def test_the_guard_on_browser_py_lets_what_it_needs_through() -> None:
    allowed = (
        "import html, logging, os, sys, webbrowser\nfrom pathlib import Path\n"
        "os.open('x', os.O_WRONLY)\nos.environ.get('DISPLAY')\nwebbrowser.open('x')\nexcept_ = webbrowser.Error\n"
    )
    assert _breaches(allowed) == []
