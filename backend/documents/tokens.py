"""Print colours, read from the application's design tokens.

docs/18 requires a single print stylesheet built from the same tokens as
the application, with **no document-local colour values**. The honest way
to hold that is to read the token file rather than to copy the hex codes
here and promise to keep them in step.

So this parses the `:root` block of `frontend/src/design/tokens.css` —
the light theme, because paper has no dark mode — and exposes the subset
a document needs. A token renamed on the frontend breaks the document
tests, which is the intended failure.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

#: What a document is allowed to use, mapped to its token name. Documents
#: are near-monochrome: text, muted text, hairlines, and the brand once,
#: on the wordmark. Nothing here is a fill.
PRINT_TOKENS = {
    "text": "--text",
    "muted": "--text-2",
    "faint": "--text-3",
    "hairline": "--border",
    "hairline_soft": "--border-hair",
    "brand": "--brand",
    "surface": "--surface",
    "tint": "--brand-weak",
}

_DECLARATION = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", re.MULTILINE)


class TokensMissing(RuntimeError):
    pass


def _tokens_path() -> Path:
    configured = getattr(settings, "DESIGN_TOKENS_PATH", None)
    if configured:
        return Path(configured)
    return Path(settings.ROOT_DIR) / "frontend" / "src" / "design" / "tokens.css"


@lru_cache(maxsize=1)
def print_palette() -> dict[str, str]:
    """The document palette, resolved from the light theme.

    Only the first `:root { ... }` block is read. The dark-theme
    overrides live in later blocks and would otherwise win, which would
    put a dark-mode background on a printed page.
    """
    path = _tokens_path()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TokensMissing(f"Design tokens not readable at {path}") from exc

    start = source.index(":root")
    end = source.index("}", start)
    declarations = dict(_DECLARATION.findall(source[start:end]))

    palette = {}
    for name, token in PRINT_TOKENS.items():
        try:
            palette[name] = declarations[token].strip()
        except KeyError as exc:
            raise TokensMissing(
                f"{token} is gone from tokens.css; documents depend on it."
            ) from exc
    return palette
