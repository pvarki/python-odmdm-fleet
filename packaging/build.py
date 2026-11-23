"""Build the connector as one self-contained file for this computer's system, and pack it

    uv run --group dist python packaging/build.py [OUT]

PyInstaller, a build tool only, puts Python, the connector and keyring into one executable,
build/pyinstaller/odmdm-connector (.exe on Windows). It is packed into OUT, default
dist/mdm-connector, under the name connector.json gives it: the .exe as it is, otherwise one
executable named odmdm-connector in a .zip (macOS) or .tar.gz (Linux), with its executable bit,
which a browser's download would lose. README.rst and CI both build with this file.
"""

import platform
import shutil
import sys
import tarfile
import zipfile
from importlib.metadata import version
from pathlib import Path

import PyInstaller.__main__
from manifest import file_name

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "pyinstaller"
#: --copy-metadata: the entry points, through which the connector finds Fleet.
#: --collect-submodules: the modules only an entry point names, Fleet's among them.
ARGUMENTS = (
    "--onefile",
    "--name",
    "odmdm-connector",
    "--collect-data",
    "odmdm_connector",
    "--copy-metadata",
    "odmdm-connector",
    "--collect-submodules",
    "odmdm_connector",
    "--noconfirm",
    "--clean",
    "--distpath",
    str(BUILD),
    "--workpath",
    str(BUILD / "work"),
    "--specpath",
    str(BUILD),
    str(ROOT / "packaging" / "launcher.py"),
)
SYSTEMS = {"win32": "windows", "darwin": "macos", "linux": "linux"}
ARCHITECTURES = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def pack(executable: Path, out: Path, release: str, system: str, arch: str) -> Path:
    """The executable in OUT under its name in connector.json, packed for its system"""
    out.mkdir(parents=True, exist_ok=True)
    packed = out / file_name(release, system, arch)
    if system == "windows":
        shutil.copyfile(executable, packed)
    elif system == "macos":
        member = zipfile.ZipInfo.from_file(executable, "odmdm-connector")
        member.create_system = 3  # Unix, so that unzipping keeps the mode
        member.external_attr = 0o100755 << 16
        with zipfile.ZipFile(packed, "w") as archive:
            archive.writestr(member, executable.read_bytes())
    else:
        with tarfile.open(packed, "w:gz") as archive:
            archive.add(executable, "odmdm-connector", filter=_owned_by_nobody)
    return packed


def _owned_by_nobody(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mode = 0o755
    return member


def main(argv: list[str]) -> int:
    """Build and pack for this computer, and say where the file is"""
    out = Path(argv[0]) if argv else ROOT / "dist" / "mdm-connector"
    system = SYSTEMS.get(sys.platform, "")
    arch = ARCHITECTURES.get(platform.machine().lower(), "")
    if not system or not arch:
        print(f"No file is made for {sys.platform} on {platform.machine()}", file=sys.stderr)
        return 1
    PyInstaller.__main__.run(list(ARGUMENTS))
    executable = BUILD / ("odmdm-connector.exe" if system == "windows" else "odmdm-connector")
    print(pack(executable, out, version("odmdm-connector"), system, arch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
