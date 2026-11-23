"""Package level tests"""

from odmdm_connector import __version__


def test_version() -> None:
    """Make sure version matches expected"""
    assert __version__ == "1.6.0+261123"
