"""The connector needs Python and nothing else, and runs from inside a zip file

So it goes into a network without internet as one file, see README.rst. Its one dependency, keyring,
reaches the system's keychain for the MDM token, is imported in store.py alone, and the connector
runs without it.
"""

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "src" / "odmdm_connector"
MODULES = sorted(PACKAGE.rglob("*.py"))
#: The one module that may import something else, and what
ALLOWED = {PACKAGE / "store.py": {"keyring"}}


def _nodes(path: Path) -> list[ast.AST]:
    return list(ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))))


def test_every_module_is_checked() -> None:
    assert PACKAGE / "app.py" in MODULES
    assert PACKAGE / "testing" / "contract.py" in MODULES


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.relative_to(PACKAGE).as_posix())
def test_runtime_code_imports_only_the_standard_library(path: Path) -> None:
    imported: set[str] = set()
    for node in _nodes(path):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert imported - sys.stdlib_module_names - {"odmdm_connector"} - ALLOWED.get(path, set()) == set()


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.relative_to(PACKAGE).as_posix())
def test_a_shipped_file_is_read_through_importlib_resources(path: Path) -> None:
    """Never beside __file__, which is no directory when the package runs from a zip"""
    assert "__file__" not in {node.id for node in _nodes(path) if isinstance(node, ast.Name)}


def test_keyring_is_imported_only_when_the_keychain_is_asked_for() -> None:
    """Never when the module is, so that the connector runs without keyring"""
    body = ast.parse((PACKAGE / "store.py").read_text(encoding="utf-8")).body
    imported = {alias.name for node in body if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in body if isinstance(node, ast.ImportFrom)}

    assert not {name for name in imported if name.split(".")[0] == "keyring"}
