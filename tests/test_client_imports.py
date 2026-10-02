"""Compatibility tests for imports from the client module."""

from typing import Any

from nanokvm.models.pro import EdidValue


def test_wildcard_import_preserves_edid_type_alias() -> None:
    """Legacy wildcard imports expose the original EDID type alias."""
    namespace: dict[str, Any] = {}
    exec("from nanokvm.client import *", namespace)

    assert namespace["EdidValue"] is EdidValue
