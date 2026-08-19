"""Tests for qr_decode.py: optional, best-effort QR/label photo decoding.

Neither real backend (``zxing-cpp`` nor ``pyrxing``) is required for these
tests: every backend and the lazy installer are mocked directly. The two
"bonus" real-decode tests are skipped automatically when the corresponding
backend/``qrcode`` are not importable (see the module docstring in
qr_decode.py for why neither backend is ever a hard requirement of this
integration). tests/conftest.py's autouse ``_reset_qr_decoder_install_state``
fixture resets ``qr_decode``'s process-lifetime "already attempted" memory
and stubs the installer to fail fast before every test in the whole suite,
so nothing here (or elsewhere) ever triggers a real network pip install.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.enocean_ptm216b import qr_decode
from custom_components.enocean_ptm216b.const import DOMAIN

# ---------------------------------------------------------------------------
# is_qr_decode_available: reflects either backend's import state
# ---------------------------------------------------------------------------


def test_is_qr_decode_available_reflects_zxingcpp_import_success():
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
    ):
        assert qr_decode.is_qr_decode_available() is False

    with (
        patch.object(qr_decode, "zxingcpp", object()),
        patch.object(qr_decode, "pyrxing", None),
    ):
        assert qr_decode.is_qr_decode_available() is True


def test_is_qr_decode_available_reflects_pyrxing_import_success():
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
    ):
        assert qr_decode.is_qr_decode_available() is False

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", object()),
    ):
        assert qr_decode.is_qr_decode_available() is True


def test_is_qr_decode_available_true_when_either_backend_present():
    with (
        patch.object(qr_decode, "zxingcpp", object()),
        patch.object(qr_decode, "pyrxing", object()),
    ):
        assert qr_decode.is_qr_decode_available() is True


# ---------------------------------------------------------------------------
# decode_qr_image (bytes): zxingcpp branch (existing behaviour, unchanged)
# ---------------------------------------------------------------------------


def test_decode_qr_image_returns_none_when_no_backend_available():
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
    ):
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


def test_decode_qr_image_uses_pyrxing_when_zxingcpp_unavailable():
    class _Result:
        text = "30SAABBCCDDEEFF+Z000102030405060708090A0B0C0D0E0F"
        format = "QRCode"

    class _FakePyrxing:
        @staticmethod
        def read_barcodes(image, formats=None):
            assert formats == ["QRCode"]
            return [_Result()]

    from PIL import Image

    image = Image.new("L", (10, 10))
    import io

    buf = io.BytesIO()
    image.save(buf, format="PNG")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", _FakePyrxing()),
    ):
        result = qr_decode.decode_qr_image(buf.getvalue())

    assert result == _Result.text


# ---------------------------------------------------------------------------
# decode_qr_image_at_path: the preferred, no-Pillow-needed pyrxing entry point
# ---------------------------------------------------------------------------


def test_decode_qr_image_at_path_with_mocked_pyrxing(tmp_path):
    class _Result:
        text = "30SAABBCCDDEEFF+Z000102030405060708090A0B0C0D0E0F"
        format = "QRCode"

    class _FakePyrxing:
        @staticmethod
        def read_barcodes(path, formats=None):
            assert formats == ["QRCode"]
            assert isinstance(path, str)
            return [_Result()]

    fake_path = tmp_path / "label.png"
    fake_path.write_bytes(b"pyrxing is mocked -- real image bytes not needed")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", _FakePyrxing()),
    ):
        result = qr_decode.decode_qr_image_at_path(str(fake_path))

    assert result == _Result.text


def test_decode_qr_image_at_path_returns_none_when_pyrxing_finds_nothing(tmp_path):
    class _FakePyrxing:
        @staticmethod
        def read_barcodes(path, formats=None):
            return []

    fake_path = tmp_path / "label.png"
    fake_path.write_bytes(b"no barcode in here")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", _FakePyrxing()),
    ):
        assert qr_decode.decode_qr_image_at_path(str(fake_path)) is None


def test_decode_qr_image_at_path_fails_closed_when_pyrxing_raises(tmp_path):
    class _FakePyrxing:
        @staticmethod
        def read_barcodes(path, formats=None):
            raise RuntimeError("unreadable image")

    fake_path = tmp_path / "label.png"
    fake_path.write_bytes(b"garbage")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", _FakePyrxing()),
    ):
        assert qr_decode.decode_qr_image_at_path(str(fake_path)) is None


def test_decode_qr_image_at_path_returns_none_when_no_backend_available(tmp_path):
    fake_path = tmp_path / "label.png"
    fake_path.write_bytes(b"irrelevant")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
    ):
        assert qr_decode.decode_qr_image_at_path(str(fake_path)) is None


def test_decode_qr_image_at_path_falls_back_to_zxingcpp_when_pyrxing_absent(tmp_path):
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
    fake_path = tmp_path / "label.png"
    image.save(fake_path, format="PNG")

    with (
        patch.object(qr_decode, "zxingcpp", _FakeZxing()),
        patch.object(qr_decode, "pyrxing", None),
    ):
        result = qr_decode.decode_qr_image_at_path(str(fake_path))

    assert result == _Barcode.text


# ---------------------------------------------------------------------------
# async_ensure_qr_decoder: lazy, at-most-once-per-run install
# ---------------------------------------------------------------------------


async def test_ensure_returns_true_immediately_when_a_backend_already_imports(hass):
    installer = AsyncMock()
    with (
        patch.object(qr_decode, "zxingcpp", object()),
        patch.object(qr_decode, "async_process_requirements", installer),
    ):
        result = await qr_decode.async_ensure_qr_decoder(hass)

    assert result is True
    installer.assert_not_called()


async def test_ensure_calls_the_installer_with_pyrxing_pinned_and_expected_name(hass):
    installer = AsyncMock(
        side_effect=qr_decode.RequirementsNotFound("x", ["pyrxing==0.6.1"])
    )
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(qr_decode, "async_process_requirements", installer),
    ):
        await qr_decode.async_ensure_qr_decoder(hass)

    installer.assert_called_once()
    called_hass, name, requirements = installer.call_args.args
    assert called_hass is hass
    assert name == f"{DOMAIN}.qr_decode"
    assert requirements == ["pyrxing==0.6.1"]


async def test_ensure_success_path_reimports_and_returns_true(hass):
    fake_module = type(sys)("pyrxing")
    fake_module.read_barcodes = lambda *a, **kw: []

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(
            qr_decode, "async_process_requirements", AsyncMock(return_value=None)
        ),
        patch.dict(sys.modules, {"pyrxing": fake_module}),
    ):
        result = await qr_decode.async_ensure_qr_decoder(hass)

        assert result is True
        assert qr_decode.pyrxing is fake_module


async def test_ensure_failure_returns_false_and_does_not_retry_on_second_call(hass):
    installer = AsyncMock(
        side_effect=qr_decode.RequirementsNotFound("x", ["pyrxing==0.6.1"])
    )
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(qr_decode, "async_process_requirements", installer),
    ):
        first = await qr_decode.async_ensure_qr_decoder(hass)
        second = await qr_decode.async_ensure_qr_decoder(hass)

    assert first is False
    assert second is False
    installer.assert_called_once()


async def test_ensure_returns_false_on_any_unexpected_installer_error(hass):
    """Never raises: an installer error other than RequirementsNotFound must
    still fail closed, exactly like every other failure mode here.
    """
    installer = AsyncMock(side_effect=RuntimeError("pip exploded"))
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(qr_decode, "async_process_requirements", installer),
    ):
        result = await qr_decode.async_ensure_qr_decoder(hass)

    assert result is False


async def test_ensure_returns_false_when_install_succeeds_but_import_still_fails(hass):
    """A "successful" install that still fails to import must fail closed
    too, and must not raise.
    """
    # sys.modules["pyrxing"] = None is the standard way to force the next
    # `import pyrxing` to raise ImportError regardless of whether pyrxing is
    # actually installed on disk in this environment (see CPython import
    # system docs: a None value in sys.modules means "this name is known to
    # not exist").
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(
            qr_decode, "async_process_requirements", AsyncMock(return_value=None)
        ),
        patch.dict(sys.modules, {"pyrxing": None}),
    ):
        result = await qr_decode.async_ensure_qr_decoder(hass)

    assert result is False


# ---------------------------------------------------------------------------
# Privacy: decode/install never log anything sensitive
# ---------------------------------------------------------------------------


async def test_decode_and_ensure_never_log_decoded_text(hass, caplog):
    caplog.set_level("DEBUG", logger="custom_components.enocean_ptm216b")
    secret_text = "30SAABBCCDDEEFF+Zsuper-secret-marker-0102030405060708"

    class _Result:
        text = secret_text
        format = "QRCode"

    class _FakePyrxing:
        @staticmethod
        def read_barcodes(path, formats=None):
            return [_Result()]

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", _FakePyrxing()),
    ):
        result = qr_decode.decode_qr_image_at_path("/fake/path.png")
    assert result == secret_text

    installer = AsyncMock(
        side_effect=qr_decode.RequirementsNotFound("x", ["pyrxing==0.6.1"])
    )
    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", None),
        patch.object(qr_decode, "async_process_requirements", installer),
    ):
        await qr_decode.async_ensure_qr_decoder(hass)

    our_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.enocean_ptm216b")
    )
    assert secret_text not in our_text


# ---------------------------------------------------------------------------
# Bonus: real backend decode, only if this environment happens to have it.
#
# Each bonus test does its own function-scoped pytest.importorskip, rather
# than a module-level one, so a missing zxing-cpp (the common case -- it's
# never in requirements-test.txt, see qr_decode.py's module docstring)
# cannot abort collection of the pyrxing bonus test below it, and vice
# versa: a module-level importorskip's Skipped exception aborts exec of the
# rest of the module, not just the one test.
# ---------------------------------------------------------------------------

_QR_PAYLOAD = "30SAABBCCDDEEFF+Z000102030405060708090A0B0C0D0E0F"


def test_real_decode_of_a_generated_synthetic_qr_image_via_zxingcpp():
    pytest.importorskip("zxingcpp", reason="zxing-cpp not installed (optional)")
    qrcode = pytest.importorskip("qrcode", reason="qrcode not installed (dev-only)")
    import io

    image = qrcode.make(_QR_PAYLOAD)
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    if not qr_decode.is_qr_decode_available():
        pytest.skip("qr_decode module did not detect zxingcpp")

    result = qr_decode.decode_qr_image(buf.getvalue())

    assert result == _QR_PAYLOAD


def test_real_decode_of_a_generated_synthetic_qr_image_via_pyrxing(tmp_path):
    real_pyrxing = pytest.importorskip(
        "pyrxing", reason="pyrxing not installed (optional)"
    )
    qrcode = pytest.importorskip("qrcode", reason="qrcode not installed (dev-only)")

    image = qrcode.make(_QR_PAYLOAD)
    fake_path = tmp_path / "label.png"
    image.save(fake_path, format="PNG")

    with (
        patch.object(qr_decode, "zxingcpp", None),
        patch.object(qr_decode, "pyrxing", real_pyrxing),
    ):
        result = qr_decode.decode_qr_image_at_path(str(fake_path))

    assert result == _QR_PAYLOAD
