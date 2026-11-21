"""The connector starts no process, and the test run makes sure of it"""

import os
import subprocess
import webbrowser
from collections.abc import Callable

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
