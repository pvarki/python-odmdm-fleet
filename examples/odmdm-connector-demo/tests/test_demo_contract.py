"""The demo keeps the contract every MDM plugin keeps"""

import pytest
from odmdm_connector_demo import DemoMdm

from odmdm_connector.mdm import Mdm
from odmdm_connector.testing import contract


def _demo(refuse_after: int | None = None) -> Mdm:
    return DemoMdm({}, refuse_after=refuse_after)


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_the_demo_keeps_the_contract(check: contract.Check) -> None:
    check(_demo)
