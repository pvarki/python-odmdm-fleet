"""Write DIR/connector.json for the connector's files in DIR: what Deploy App's MDM page offers

    uv run python packaging/manifest.py DIR

DIR holds what a deployment serves beside its UI as mdm-connector/: the files packaging/build.py
packed, one per system, and the one-file Python build. The manifest says the connector's version,
the MDMs built into it, read from what the package registers, and each file with its size and
SHA-256, in a fixed order. A file in DIR that is none of these is an error that names it.
"""

import hashlib
import json
import re
import sys
from importlib.metadata import version
from pathlib import Path

from odmdm_connector import plugins

NAME = "connector.json"
#: Each system's file, in the manifest's order: the system, its architecture and the file's extension
FILES = (
    ("windows", "x64", ".exe"),
    ("windows", "arm64", ".exe"),
    ("macos", "x64", ".zip"),
    ("macos", "arm64", ".zip"),
    ("linux", "x64", ".tar.gz"),
    ("linux", "arm64", ".tar.gz"),
    ("any", "any", ".pyz"),
)
#: What Deploy App takes as a file's name: a bare name in the manifest's folder
BARE = re.compile(r"[A-Za-z0-9._+-]{1,120}")


class ManifestError(Exception):
    """A folder that cannot be described as the contract says"""


def file_name(release: str, system: str, arch: str) -> str:
    """The name of the connector's file for that system and architecture"""
    extension = next(extension for named, machine, extension in FILES if (named, machine) == (system, arch))
    if system == "any":
        return f"odmdm-connector-{release}{extension}"
    return f"odmdm-connector-{release}-{system}-{arch}{extension}"


def built_in() -> list[dict[str, str]]:
    """The MDMs built into the connector, name and title, as the package registers them"""
    return [
        {"name": plugin.name, "title": plugins.load(plugin.name).title}
        for plugin in plugins.installed()
        if plugin.built_in
    ]


def manifest(folder: Path, release: str) -> dict[str, object]:
    """The manifest for the files in the folder, connector.json aside"""
    known = {file_name(release, system, arch): (system, arch) for system, arch, _ in FILES}
    found = {path.name: path for path in folder.iterdir() if path.name != NAME}
    for name, path in sorted(found.items()):
        if name not in known or not BARE.fullmatch(name) or not path.is_file():
            raise ManifestError(f"{path} is not one of the connector's files of version {release}")
    if not found:
        raise ManifestError(f"{folder} holds none of the connector's files")
    files = [
        {
            "os": system,
            "arch": arch,
            "file": name,
            "size": found[name].stat().st_size,
            "sha256": hashlib.sha256(found[name].read_bytes()).hexdigest(),
        }
        for name, (system, arch) in known.items()
        if name in found
    ]
    return {"version": release, "mdms": built_in(), "files": files}


def main(argv: list[str]) -> int:
    """Write the manifest into the folder named, or say why not"""
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        print("Give the folder of the connector's files: manifest.py DIR", file=sys.stderr)
        return 2
    folder = Path(argv[0])
    try:
        written = manifest(folder, version("odmdm-connector"))
    except ManifestError as exc:
        print(exc, file=sys.stderr)
        return 1
    (folder / NAME).write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
