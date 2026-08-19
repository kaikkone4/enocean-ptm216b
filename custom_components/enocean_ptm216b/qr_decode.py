"""Optional, best-effort QR/label photo decoding for the Add-device wizard.

History -- why ``zxing-cpp`` was never listed in ``manifest.json``'s
``requirements``, kept here because it still explains this module's shape:

- ``zxing-cpp`` has never published a musllinux wheel (checked across every
  version on PyPI) -- only manylinux/macOS/Windows. Home Assistant OS is
  musl-based (Alpine), so a hard requirement would fail to install there.
- Home Assistant's own ``async_process_deps_reqs`` installs every listed
  requirement for a domain, for every config entry of that domain, BEFORE
  ``async_setup``/``async_setup_entry`` ever runs. A single failed
  requirement blocks the whole integration -- every already-commissioned
  switch, not just this optional photo-upload convenience.

That meant nothing ever installed the optional decoder for anyone, and the
primary user (Home Assistant OS, musl) could never get it at all even by
hand -- there is no musl wheel to manually ``pip install`` either.

Phase 5C mechanism -- lazy runtime install of ``pyrxing`` instead:

``pyrxing`` (PyPI, Apache-2.0, https://github.com/tanagumo/pyrxing) is a
dependency-free zxing-cpp-based reader that, unlike ``zxing-cpp`` itself,
publishes musllinux wheels (aarch64/x86_64, musl 1.2+) alongside manylinux
(glibc 2.39+), macOS universal, and Windows wheels -- i.e. it covers Home
Assistant OS. It is still never added to ``manifest.json``'s
``requirements`` for the exact same reason ``zxing-cpp`` never was: a
platform this integration hasn't been checked against, or a Home Assistant
install with no outbound network access, must never fail integration setup
over an optional convenience feature. Instead, :func:`async_ensure_qr_decoder`
installs it lazily, at runtime, ONLY when the Add-device wizard's key-entry
step is about to render its form -- never from ``async_setup``/
``async_setup_entry`` -- via Home Assistant's own
``homeassistant.requirements.async_process_requirements``, the same
mechanism ``async_process_deps_reqs`` uses internally, but invoked directly
by this module so a failure here can never touch any other part of this
integration. That call raises ``RequirementsNotFound`` when no wheel is
installable (wrong platform, wrong Python, or no network); this module
catches that -- and, defensively, anything else the installer could raise --
and remembers the failure for the rest of this Home Assistant run, so a
user who reopens the wizard repeatedly does not pay a failed pip resolve on
every single form render. One attempt per Home Assistant start is the right
cadence; a fresh attempt only happens on the next restart.

Both backends are supported for as long as either is importable:
``zxingcpp`` (the actual importable module name for a manually
``pip install zxing-cpp``ed advanced/glibc setup -- NOT ``zxing_cpp``,
which is only the PyPI *distribution* name) and ``pyrxing`` (installed
lazily as above, or manually). :func:`is_qr_decode_available` reports
whether either currently imports, without installing anything -- it is the
fast, synchronous check every non-wizard caller should keep using.
:func:`async_ensure_qr_decoder` is the only thing that ever installs
anything, and only the wizard's key-entry step calls it.

Never logs, persists, or echoes back an image byte or a decoded string --
only the caller's already-parsed (address, key) result ever leaves this
boundary, via :mod:`commissioning_input`.
"""

from __future__ import annotations

import io
import logging

from homeassistant.core import HomeAssistant
from homeassistant.requirements import RequirementsNotFound, async_process_requirements

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

try:
    import zxingcpp
except ImportError:
    zxingcpp = None

try:
    import pyrxing
except ImportError:
    pyrxing = None

_PYRXING_REQUIREMENT = "pyrxing==0.6.1"
_PYRXING_QR_FORMATS = ["QRCode"]

# Process-lifetime memory of one failed lazy-install attempt (see this
# module's docstring for the "one attempt per Home Assistant start" cadence
# this exists to enforce). Only ever set True by async_ensure_qr_decoder;
# never reset except by a fresh process (i.e. a Home Assistant restart).
_install_attempted = False


def is_qr_decode_available() -> bool:
    """Return whether photo-QR decoding is usable right now.

    Never installs anything -- a plain import-state check, safe to call
    from anywhere, any number of times. See :func:`async_ensure_qr_decoder`
    for the one place that may attempt to make this True.
    """
    return zxingcpp is not None or pyrxing is not None


async def async_ensure_qr_decoder(hass: HomeAssistant) -> bool:
    """Best-effort, at-most-once-per-run lazy install of a QR decode backend.

    Returns True the moment either backend is importable -- immediately, no
    install attempted, if one already is. Otherwise makes exactly ONE
    attempt (per Home Assistant start; see the module docstring) to
    ``pip install pyrxing==0.6.1`` into Home Assistant's own Python
    environment via ``homeassistant.requirements.async_process_requirements``,
    then re-imports it on success.

    Must be called ONLY from the Add-device wizard's key-entry step, right
    before it builds its form schema -- never from ``async_setup``/
    ``async_setup_entry`` (see this module's docstring for why: a failed
    install must never be able to block anything but this one optional
    field). NEVER raises: every failure mode here -- no wheel for this
    platform/Python, no network, an unexpected installer error, or a
    "succeeded" install that still fails to import -- is treated identically
    and returns False.
    """
    global pyrxing, _install_attempted

    if is_qr_decode_available():
        return True

    if _install_attempted:
        return False
    _install_attempted = True

    try:
        await async_process_requirements(
            hass, f"{DOMAIN}.qr_decode", [_PYRXING_REQUIREMENT]
        )
    except RequirementsNotFound:
        _LOGGER.debug(
            "Optional photo-QR decoder (pyrxing) has no installable wheel "
            "for this platform/Python; photo upload stays unavailable for "
            "the rest of this Home Assistant run"
        )
        return False
    except Exception:  # noqa: BLE001 - a lazy install must never raise out
        _LOGGER.debug(
            "Optional photo-QR decoder (pyrxing) install failed unexpectedly",
            exc_info=True,
        )
        return False

    try:
        import pyrxing as _pyrxing
    except ImportError:
        _LOGGER.debug("pyrxing reported as installed but still failed to import")
        return False

    pyrxing = _pyrxing
    return True


def decode_qr_image_at_path(path: str) -> str | None:
    """Decode one QR code's text straight from a file path, or ``None``.

    Preferred entry point when a path is already on hand (the normal case:
    an uploaded file already lives at a temp path): ``pyrxing`` reads a
    path directly, with no Pillow dependency needed for this call at all.
    Falls back to the ``zxingcpp``+Pillow mechanism when only that backend
    is available. Returns ``None`` -- never raises -- when no backend is
    available, the file is not a readable image, or no QR code is found.
    Must be called from an executor thread (image decoding is blocking);
    callers own that hop, e.g. via ``hass.async_add_executor_job``.
    """
    if zxingcpp is not None:
        try:
            with open(path, "rb") as image_file:
                return decode_qr_image(image_file.read())
        except OSError:
            return None
    if pyrxing is not None:
        try:
            for barcode in pyrxing.read_barcodes(path, formats=_PYRXING_QR_FORMATS):
                if barcode.text:
                    return barcode.text
        except Exception:  # noqa: BLE001 - any decode failure means "not found"
            return None
        return None
    return None


def decode_qr_image(image_bytes: bytes) -> str | None:
    """Decode one QR code's text from raw image bytes, or ``None``.

    Kept for callers that only have bytes in hand (and by
    :func:`decode_qr_image_at_path`'s own ``zxingcpp`` fallback branch).
    Returns ``None`` -- never raises -- when no decode backend is
    available, the bytes are not a readable image, or no QR code is found.
    Must be called from an executor thread (image decoding is blocking);
    callers own that hop, e.g. via ``hass.async_add_executor_job``.
    """
    if zxingcpp is None and pyrxing is None:
        return None
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:  # noqa: BLE001 - any decode failure means "not found"
        return None

    if zxingcpp is not None:
        try:
            for barcode in zxingcpp.read_barcodes(image, formats=zxingcpp.QRCode):
                if barcode.valid and barcode.text:
                    return barcode.text
        except Exception:  # noqa: BLE001 - any decode failure means "not found"
            return None
        return None

    try:
        for barcode in pyrxing.read_barcodes(image, formats=_PYRXING_QR_FORMATS):
            if barcode.text:
                return barcode.text
    except Exception:  # noqa: BLE001 - any decode failure means "not found"
        return None
    return None
