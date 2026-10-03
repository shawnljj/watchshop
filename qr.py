#!/usr/bin/env python3
"""QR rendering for job tags.

minqr is the encoder used by default: it is in this repo, has no dependency to
install on the shop's machine, and is verified against an independent reader by
test_qr.py. segno is used only if it happens to be present and minqr is put
aside -- see qr.svg() for the switch. Both produce a valid QR for the same text;
neither is a byte-exact copy of the other.
"""
import io

try:
    import minqr
except ImportError:                                   # pragma: no cover
    minqr = None

try:
    import segno
except ImportError:
    segno = None


def _segno_svg(text, scale, border):
    """segno's SVG writer needs a TEXT stream, not BytesIO."""
    qr = segno.make(text, error="m", micro=False)
    buf = io.StringIO()
    qr.save(buf, kind="svg", scale=scale, border=border,
            dark="#0b0b0c", light="#ffffff", xmldecl=False,
            svgns=True, nl=False)
    return buf.getvalue()


def svg(text: str, scale: int = 4, border: int = 2) -> str:
    """An SVG string encoding `text`."""
    if minqr is not None:
        return minqr.svg(text, scale=scale, border=border)
    if segno is not None:
        return _segno_svg(text, scale, border)
    raise RuntimeError("no QR encoder available")


def png_bytes(text: str, scale: int = 6, border: int = 2) -> bytes:
    if minqr is not None:
        return minqr.png_bytes(text, scale=scale, border=border)
    if segno is not None:
        buf = io.BytesIO()
        segno.make(text, error="m", micro=False).save(buf, kind="png",
                                                      scale=scale, border=border)
        return buf.getvalue()
    raise RuntimeError("no QR encoder available")


def data_uri(text: str, scale: int = 6) -> str:
    """An inline PNG data URI, for a receipt page with no second request."""
    import base64
    return "data:image/png;base64," + base64.b64encode(
        png_bytes(text, scale=scale, border=2)).decode()
