"""HTML now, PDF when the deployment target can carry a browser.

docs/18 specifies headless Chromium via Playwright, because it renders
exactly what the browser preview shows and nothing else does. That is
still the target. But Playwright needs a ~400MB browser and a set of
system libraries, and whether the deployment target carries them is an
open decision — see docs/30 §Decisions.

Rather than block every document on that, rendering splits in two:

* **HTML is always produced**, stored on the row, and hashed. The web
  preview, the parity guarantee and the determinism test all work now.
* **PDF is produced by whichever backend is configured**, and by none if
  none is available. A document with no PDF is still issued, numbered
  and immutable — it simply has not been printed yet, and can be
  back-filled by re-running the renderer over the stored context.

`DOCUMENT_PDF_BACKEND` selects: "playwright", or "none".
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.template.loader import render_to_string

from documents.tokens import print_palette

log = logging.getLogger(__name__)


def render_html(*, template: str, context: dict) -> str:
    """The document, as HTML. One template renders preview and print.

    The palette is injected rather than imported by the template so a
    template cannot reach for a colour that is not in the print set.
    """
    return render_to_string(template, {**context, "palette": print_palette()})


def content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


class PdfUnavailable(RuntimeError):
    """No PDF backend is configured or installed."""


def render_pdf(html: str) -> bytes | None:
    """Bytes, or None when no backend is available.

    None rather than an exception: an unprintable document is a
    deployment gap, not a reason to refuse to issue the record.
    """
    backend = getattr(settings, "DOCUMENT_PDF_BACKEND", "none")
    if backend == "none":
        return None
    if backend == "playwright":
        try:
            return _playwright_pdf(html)
        except Exception:  # pragma: no cover - depends on the host
            log.exception("PDF rendering failed; the document was issued without one")
            return None
    raise PdfUnavailable(f"Unknown PDF backend: {backend}")


def _playwright_pdf(html: str) -> bytes:  # pragma: no cover - needs a browser
    """A4 with the margins docs/18 specifies.

    `print_background` is on so the totals tint survives; everything else
    on the page is hairlines and type, which print regardless.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "16mm",
                    "bottom": "20mm",
                    "left": "18mm",
                    "right": "18mm",
                },
            )
        finally:
            browser.close()
