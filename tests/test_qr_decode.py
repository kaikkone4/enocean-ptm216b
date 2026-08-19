"""Tests for qr_decode.py: optional, best-effort QR/label photo decoding.

The real ``zxing-cpp`` library is never required for these tests: the
unavailable-decoder path is mocked directly, and the one "bonus" real-decode
test is skipped automatically when ``zxingcpp``/``qrcode`` are not
importable (see the module docstring in qr_decode.py for why zxing-cpp is
never a hard requirement of this integration).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.enocean_ptm216b import qr_decode


def test_is_qr_decode_available_reflects_import_success():
    with patch.object(qr_decode, "zxingcpp", None):
        assert qr_decode.is_qr_decode_available() is False

    with patch.object(qr_decode, "zxingcpp", object()):
        assert qr_decode.is_qr_decode_available() is True


def test_decode_qr_image_returns_none_when_library_unavailable():
    with patch.object(qr_decode, "zxingcpp", None):
        assert qr_decode.decode_qr_image(b"not a real image") is None


def test_decode_qr_image_returns_none_on_unreadable_bytes_when_available():
    """Even if the library imports fine, garbage bytes must fail closed to
    None -- never raise -- exactly like every other decode failure mode.
    """
    fake_zxingcpp = type("FakeZxing", (), {"QRCode": object()})()
    with patch.object(qr_decode, "zxingcpp", fake_zxingcpp):
        assert qr_decode.decode_qr_image(b"not a real image") is None


def test_decode_qr_image_returns_text_from_a_valid_barcode_result():
    class _Barcode:
        valid = True
        text = "30SAABBCCDDEEFF+Z000102030405060708090A0B0C0D0E0F"

    class _FakeZxing:
        QRCode = object()

        @staticmethod
        def read_barcodes(image, formats=None):
            return [_Barcode()]

    from PIL import Image

    image = Image.new("L", (10, 10))
    import io

    buf = io.BytesIO()
    image.save(buf, format="PNG")

    with patch.object(qr_decode, "zxingcpp", _FakeZxing()):
        result = qr_decode.decode_qr_image(buf.getvalue())

    assert result == _Barcode.text


def test_decode_qr_image_skips_invalid_barcodes():
    class _InvalidBarcode:
        valid = False
        text = "should never be returned"

    class _FakeZxing:
        QRCode = object()

        @staticmethod
        def read_barcodes(image, formats=None):
            return [_InvalidBarcode()]

    from PIL import Image

    image = Image.new("L", (10, 10))
    import io

    buf = io.BytesIO()
    image.save(buf, format="PNG")

    with patch.object(qr_decode, "zxingcpp", _FakeZxing()):
        result = qr_decode.decode_qr_image(buf.getvalue())

    assert result is None


# ---------------------------------------------------------------------------
# Bonus: real zxing-cpp decode, only if this environment happens to have it.
# ---------------------------------------------------------------------------

zxingcpp = pytest.importorskip("zxingcpp", reason="zxing-cpp not installed (optional)")
qrcode = pytest.importorskip("qrcode", reason="qrcode not installed (dev-only)")


def test_real_decode_of_a_generated_synthetic_qr_image():
    import io

    payload = "30SAABBCCDDEEFF+Z000102030405060708090A0B0C0D0E0F"
    image = qrcode.make(payload)
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    if not qr_decode.is_qr_decode_available():
        pytest.skip("qr_decode module did not detect zxingcpp")

    result = qr_decode.decode_qr_image(buf.getvalue())

    assert result == payload
