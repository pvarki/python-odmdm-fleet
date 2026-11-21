"""Guards for the whole test run"""

import os
import subprocess
import sys
import webbrowser
from collections.abc import Iterator
from typing import NoReturn

import pytest

#: Every way the standard library starts a process, as module and function names. subprocess,
#: os.popen and webbrowser end in Popen._execute_child; multiprocessing in fork_exec or CreateProcess.
STARTERS: tuple[tuple[object, tuple[str, ...]], ...] = (
    (subprocess.Popen, ("_execute_child",)),
    (sys.modules.get("_posixsubprocess"), ("fork_exec",)),
    (sys.modules.get("_winapi"), ("CreateProcess",)),
    (webbrowser, ("open", "open_new", "open_new_tab")),
    (
        os,
        (
            "system",
            "popen",
            "fork",
            "forkpty",
            "posix_spawn",
            "posix_spawnp",
            "startfile",
            *(f"exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
            *(f"spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
        ),
    ),
)


def _refuse(*args: object, **kwargs: object) -> NoReturn:
    raise RuntimeError("A test started a process, and the connector never starts one")


@pytest.fixture(autouse=True, scope="session")
def no_process_starts() -> Iterator[None]:
    """A process started anywhere, by the connector or by a library, fails the test that did it"""
    with pytest.MonkeyPatch.context() as patch:
        for module, names in STARTERS:
            for name in names:
                if module is not None and hasattr(module, name):
                    patch.setattr(module, name, _refuse)
        yield
