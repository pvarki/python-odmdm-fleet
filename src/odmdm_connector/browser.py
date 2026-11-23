"""Opening the page in the operator's default browser: the one place the connector starts a process

The address holds the page's key, and on Linux and macOS a program's arguments can be read by other
users of the computer. So the address is never handed to the system's opener: a small file in the
connector's own directory, which only the user can read, leads on to it, and the opener is given
that file's path alone. The file holds the key while the connector runs and is removed when it stops.
"""

import html
import logging
import os
import sys
import webbrowser
from pathlib import Path

LOGGER = logging.getLogger(__name__)

#: The file in the connector's directory that leads on to the page's address
NAME = "open-page.html"


def can_open() -> bool:
    """Whether there is a desktop to open a browser on; without one, webbrowser starts a text browser in the terminal"""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _page(address: str) -> str:
    escaped = html.escape(address, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f'<meta http-equiv="refresh" content="0; url={escaped}">\n'
        "<title>odmdm-connector</title></head>\n"
        f'<body><p><a href="{escaped}">Open the connector\'s page</a></p></body></html>\n'
    )


def _write(path: Path, address: str) -> None:
    """The file anew, readable only by the user: one left from an earlier start is removed first"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as written:
        written.write(_page(address))


def open_page(address: str, directory: Path) -> bool:
    """Ask the default browser to open the address, through a file in the directory; whether it was asked

    Raises nothing that writing the file or starting a browser raises: the connector starts whether
    or not a browser opens.
    """
    if not can_open():
        return False
    path = directory / NAME
    try:
        _write(path, address)
        if webbrowser.open(path.as_uri()):
            return True
        LOGGER.warning("No browser could be asked to open the page")
    except (OSError, ValueError, webbrowser.Error) as exc:
        LOGGER.warning("The browser could not be asked to open the page: %s", type(exc).__name__)
    remove(directory)
    return False


def remove(directory: Path) -> None:
    """Remove the file, if it is there; never raises"""
    try:
        (directory / NAME).unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("The file %s could not be removed: %s", directory / NAME, type(exc).__name__)
