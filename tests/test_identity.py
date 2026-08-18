import pytest

from custom_components.enocean_ptm216b.identity import (
    canonicalize_address,
    device_handle,
    device_identifier,
)

SECRET = b"\x01" * 32


def test_device_identifier_is_stable_for_equivalent_address_formats():
    colon = device_identifier(SECRET, "AA:BB:CC:DD:EE:FF")
    compact = device_identifier(SECRET, "aabbccddeeff")

    assert colon == compact
    assert len(colon) == 64
    assert device_handle(colon) == f"test-{colon[:16]}"


def test_device_identifier_changes_when_secret_or_address_changes():
    first = device_identifier(SECRET, "AA:BB:CC:DD:EE:FF")

    assert first != device_identifier(b"\x02" * 32, "AA:BB:CC:DD:EE:FF")
    assert first != device_identifier(SECRET, "AA:BB:CC:DD:EE:00")


@pytest.mark.parametrize(
    "address", ["", "not-an-address", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:GG"]
)
def test_canonicalize_address_rejects_invalid_addresses(address):
    with pytest.raises(ValueError):
        canonicalize_address(address)
