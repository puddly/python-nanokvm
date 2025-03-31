import pytest

from nanokvm.utils import obfuscate_password


@pytest.mark.parametrize(
    "password", ["something_simple", "Something !@# complex äöü'\""]
)
def test_obfuscate_password(password: str) -> None:
    assert obfuscate_password(password)
