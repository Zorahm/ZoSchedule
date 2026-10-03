"""@font-face CSS with the fonts inlined as data URIs.

The page is rendered from a string (origin `about:blank`), where `file://` fonts
are blocked, so the files travel inside the CSS. Variable woff2 subsets copied
from the design file: Onest, JetBrains Mono, Unbounded.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

_RANGES = {
    "cyrillic": "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116",
    "latin": (
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, "
        "U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, "
        "U+FEFF, U+FFFD"
    ),
}

# family -> weight range of the variable font
_FAMILIES = {
    "Onest": "100 900",
    "JetBrains Mono": "100 800",
    "Unbounded": "200 900",
}


def _face(family: str, weights: str, subset: str) -> str:
    file = _FONTS_DIR / f"{family.replace(' ', '')}-{subset}.woff2"
    encoded = base64.b64encode(file.read_bytes()).decode("ascii")
    return (
        f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weights};"
        f"src:url(data:font/woff2;base64,{encoded}) format('woff2');"
        f"unicode-range:{_RANGES[subset]}}}"
    )


@lru_cache(maxsize=1)
def font_css() -> str:
    return "".join(
        _face(family, weights, subset)
        for family, weights in _FAMILIES.items()
        for subset in _RANGES
    )
