"""Checking a firm's logo before it is kept.

A logo is the one file in the product that comes from a person and is then handed to a
layout engine and to every client who receives a report. So it is checked once, on the way
in, and refused unless it is plainly an ordinary picture:

* a PNG or a JPEG, decided by reading the file and not by trusting its name or the type the
  browser claimed. An SVG is refused on purpose: it is a program as much as a picture;
* small enough to belong on a cover page;
* readable from start to finish.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

MAX_LOGO_BYTES = 512 * 1024
MAX_LOGO_PIXELS = 4000
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}


def check_logo(data: bytes) -> str:
    """Return the logo's media type, or raise ValueError with a message a person can act on."""
    if not data:
        raise ValueError("the logo file is empty")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(
            f"the logo is {len(data) // 1024} KB; the limit is {MAX_LOGO_BYTES // 1024} KB. "
            f"Save a smaller copy."
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValueError("the logo could not be read as a picture; use a PNG or a JPEG") from None
    if kind not in _MIME:
        raise ValueError(f"the logo is a {kind} file; use a PNG or a JPEG")
    if width > MAX_LOGO_PIXELS or height > MAX_LOGO_PIXELS:
        raise ValueError(
            f"the logo is {width} x {height} pixels; the limit is {MAX_LOGO_PIXELS} each way"
        )
    return _MIME[kind]
