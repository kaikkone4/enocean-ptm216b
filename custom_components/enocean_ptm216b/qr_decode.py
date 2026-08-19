"""Optional, best-effort QR/label photo decoding for the Add-device wizard.

``zxing-cpp`` is deliberately NOT listed in ``manifest.json``'s
``requirements`` array, even though it is the library this module uses when
present. Two verified facts drove that decision, documented here rather than
only in the PR body so a future reader of this module sees the reasoning
next to the code it shaped:

- ``zxing-cpp`` has never published a musllinux wheel (checked across every
  version on PyPI) -- only manylinux/macOS/Windows. Home Assistant OS is
  musl-based (Alpine), so a hard requirement would fail to install there.
- Home Assistant's own ``async_process_deps_reqs`` installs every listed
  requirement for a domain, for every config entry of that domain, BEFORE
  ``async_setup``/``async_setup_entry`` ever runs. A single failed
  requirement blocks the whole integration -- every already-commissioned
  switch, not just this optional photo-upload convenience.

So this module imports ``zxingcpp`` (the actual importable module name --
NOT ``zxing_cpp``, which is only the PyPI *distribution* name) inside a
plain ``try/except ImportError`` and treats "not installed" as just another
"nothing decoded" outcome: :func:`is_qr_decode_available` and
:func:`decode_qr_image` never raise for that reason. Advanced users on a
glibc Home Assistant install (Container/Supervised on Debian, dev
environments) can manually ``pip install zxing-cpp`` into the Home
Assistant Python environment to enable photo-QR decode; see README.md's
commissioning section.

Never logs, persists, or echoes back an image byte or a decoded string --
only the caller's already-parsed (address, key) result ever leaves this
boundary, via :mod:`commissioning_input`.
"""

from __future__ import annotations

import io

try:
    import zxingcpp
except ImportError:
    zxingcpp = None


def is_qr_decode_available() -> bool:
    """Return whether photo-QR decoding is usable in this environment."""
    return zxingcpp is not None


def decode_qr_image(image_bytes: bytes) -> str | None:
    """Decode one QR code's text from raw image bytes, or ``None``.

    Returns ``None`` -- never raises -- when the decode library is
    unavailable, the bytes are not a readable image, or no QR code is
    found. Must be called from an executor thread (image decoding is
    blocking); callers own that hop, e.g. via
    ``hass.async_add_executor_job``.
    """
    if zxingcpp is None:
        return None
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        for barcode in zxingcpp.read_barcodes(image, formats=zxingcpp.QRCode):
            if barcode.valid and barcode.text:
                return barcode.text
    except Exception:  # noqa: BLE001 - any decode failure means "not found"
        return None
    return None
