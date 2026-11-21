"""packaging/manifest.py: connector.json, the contract with Deploy App's MDM page"""

import hashlib
import json
import re
from importlib.metadata import version
from pathlib import Path

import manifest
import pytest

RELEASE = version("odmdm-connector")


def test_the_manifest_names_the_mdms_built_in_and_each_file_in_a_fixed_order(tmp_path: Path) -> None:
    for name in (f"odmdm-connector-{RELEASE}.pyz", f"odmdm-connector-{RELEASE}-windows-x64.exe"):
        (tmp_path / name).write_bytes(name.encode())
    (tmp_path / manifest.NAME).write_text("the one written before", encoding="utf-8")

    assert manifest.main([str(tmp_path)]) == 0

    written = json.loads((tmp_path / manifest.NAME).read_text(encoding="utf-8"))
    assert written == {
        "version": RELEASE,
        "mdms": [{"name": "fleet", "title": "Fleet"}],
        "files": [
            {
                "os": system,
                "arch": arch,
                "file": name,
                "size": len(name),
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
            }
            for system, arch, name in (
                ("windows", "x64", f"odmdm-connector-{RELEASE}-windows-x64.exe"),
                ("any", "any", f"odmdm-connector-{RELEASE}.pyz"),
            )
        ],
    }
    assert all(re.fullmatch(r"[A-Za-z0-9._+-]{1,120}", item["file"]) for item in written["files"])


@pytest.mark.parametrize(
    "name", ["odmdm-connector-0.0.1+1-windows-x64.exe", "odmdm-connector-macos-arm64.zip", "notes.txt", "a folder"]
)
def test_a_file_that_is_not_the_connector_s_is_named_never_skipped(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / f"odmdm-connector-{RELEASE}.pyz").write_bytes(b"pyz")
    if name == "a folder":
        (tmp_path / name).mkdir()
    else:
        (tmp_path / name).write_bytes(b"other")

    assert manifest.main([str(tmp_path)]) == 1

    assert name in capsys.readouterr().err
    assert not (tmp_path / manifest.NAME).exists()
