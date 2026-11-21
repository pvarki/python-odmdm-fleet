====================
odmdm-connector-demo
====================

A pretend MDM for odmdm-connector, and the starting point for a plugin. Its three phones live in
memory, and it lacks one capability on purpose: it takes no challenge by API, so ``challenge``
says where to paste one instead.

Copy this folder, rename the package ``odmdm-connector-<name>`` and the entry point, and replace
the list with calls to your MDM's API. ``tests/test_demo_contract.py`` runs the connector's
contract checks; keep it passing.

Try it next to the connector::

    uv build --out-dir dist
    uv build --out-dir dist examples/odmdm-connector-demo
    uv tool install dist/odmdm_connector-*.whl --with dist/odmdm_connector_demo-*.whl
    odmdm-connector mdms
    odmdm-connector --mdm demo phones
