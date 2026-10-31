"""Package level tests"""

from odmdm_fleet import __version__


def test_version() -> None:
    """Make sure version matches expected"""
    assert __version__ == "0.1.0+261031"
