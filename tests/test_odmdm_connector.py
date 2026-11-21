"""Package level tests"""

from odmdm_connector import __version__


def test_version() -> None:
    """Make sure version matches expected"""
    assert __version__ == "1.0.0+261121"
