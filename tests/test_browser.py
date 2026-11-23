"""Opening the page in the default browser, with the opener replaced so that no test starts anything"""

import html
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from odmdm_connector import browser

KEY = "k3y-of_the.page"
ADDRESS = f"http://127.0.0.1:8737/#key={KEY}&x=<\"'>"


class Opener:
    """The opener: what it was handed, and its answer or what it raises"""

    def __init__(self, answer: bool = True, raises: Exception | None = None) -> None:
        self.answer, self.raises = answer, raises
        self.handed: list[str] = []

    def __call__(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
        self.handed.append(url)
        if self.raises is not None:
            raise self.raises
        return self.answer


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Linux computer with a desktop"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def _opener(monkeypatch: pytest.MonkeyPatch) -> Opener:
    made = Opener()
    monkeypatch.setattr("webbrowser.open", made)
    return made


def test_the_opener_gets_a_file_that_leads_on_to_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None
) -> None:
    opener = _opener(monkeypatch)

    assert browser.open_page(ADDRESS, tmp_path / "config")

    [handed] = opener.handed
    assert KEY not in handed, "only the file's path reaches another program's arguments"
    assert urlparse(handed).scheme == "file"
    path = Path(url2pathname(urlparse(handed).path))
    assert path == tmp_path / "config" / browser.NAME
    text = path.read_text(encoding="utf-8")
    assert html.escape(ADDRESS, quote=True) in text
    assert ADDRESS not in text, "escaped for HTML"
    assert 'http-equiv="refresh"' in text
    assert "<a href=" in text


@pytest.mark.skipif(os.name != "posix", reason="a system without such modes")
def test_the_file_is_readable_by_the_user_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None) -> None:
    _opener(monkeypatch)

    browser.open_page(ADDRESS, tmp_path)

    assert stat.S_IMODE((tmp_path / browser.NAME).stat().st_mode) == 0o600


def test_a_file_left_from_an_earlier_start_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None
) -> None:
    left = tmp_path / browser.NAME
    left.write_text("the earlier key", encoding="utf-8")
    if os.name == "posix":
        left.chmod(0o644)
    _opener(monkeypatch)

    assert browser.open_page(ADDRESS, tmp_path)

    assert "the earlier key" not in left.read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(left.stat().st_mode) == 0o600


@pytest.mark.parametrize("display", ["DISPLAY", "WAYLAND_DISPLAY"])
def test_either_display_is_a_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None, display: str
) -> None:
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setenv(display, "wayland-0")
    opener = _opener(monkeypatch)

    assert browser.open_page(ADDRESS, tmp_path)
    assert len(opener.handed) == 1


def test_without_a_desktop_nothing_is_written_or_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None
) -> None:
    monkeypatch.delenv("DISPLAY")
    opener = _opener(monkeypatch)

    assert not browser.open_page(ADDRESS, tmp_path)
    assert opener.handed == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macos_always_have_a_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    opener = _opener(monkeypatch)

    assert browser.open_page(ADDRESS, tmp_path)
    assert len(opener.handed) == 1


@pytest.mark.parametrize(
    "opener",
    [Opener(answer=False), Opener(raises=OSError(2, "No such file")), Opener(raises=ValueError("embedded null byte"))],
    ids=["says no", "cannot start", "refuses the path"],
)
def test_when_no_browser_is_asked_the_file_is_gone_and_the_answer_is_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None, opener: Opener
) -> None:
    monkeypatch.setattr("webbrowser.open", opener)

    assert not browser.open_page(ADDRESS, tmp_path)
    assert len(opener.handed) == 1
    assert not (tmp_path / browser.NAME).exists()


def test_a_file_that_cannot_be_written_is_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None) -> None:
    blocked = tmp_path / "a file, not a directory"
    blocked.write_text("", encoding="utf-8")
    opener = _opener(monkeypatch)

    assert not browser.open_page(ADDRESS, blocked)
    assert opener.handed == []


def test_remove_takes_the_file_away_and_is_quiet_when_it_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: None
) -> None:
    _opener(monkeypatch)
    browser.open_page(ADDRESS, tmp_path)

    browser.remove(tmp_path)
    browser.remove(tmp_path)

    assert not (tmp_path / browser.NAME).exists()


def test_remove_says_in_the_log_when_it_cannot(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / browser.NAME).mkdir()

    browser.remove(tmp_path)

    assert f"The file {tmp_path / browser.NAME} could not be removed" in caplog.text
