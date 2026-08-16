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

import atexit
import hashlib
import logging
import queue
import threading

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


#: A4 with the margins docs/18 specifies. `print_background` is on so the
#: totals tint survives; the rest of the page is hairlines and type,
#: which print regardless.
PAGE = {
    "format": "A4",
    "print_background": True,
    "margin": {"top": "16mm", "bottom": "20mm", "left": "18mm", "right": "18mm"},
}


class _BrowserThread:
    """One long-lived Chromium, driven from a thread of its own.

    Two problems solved together.

    **Launch cost.** Starting Chromium takes seconds, and a depot
    dispatching fifty orders would pay it fifty times. The browser is
    started once and kept.

    **The event loop.** Playwright's sync API runs an asyncio loop in
    whichever thread starts it, and Django refuses ORM access from a
    thread with a running loop — `SynchronousOnlyOperation`. Keeping the
    browser alive in a request thread therefore breaks every query that
    follows it. Confining it to a worker thread keeps the loop out of the
    caller's way entirely, and the queue hand-off is what makes that
    safe.
    """

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._serve, name="medix-pdf", daemon=True
        )
        self._thread.start()

    def _serve(self) -> None:  # pragma: no cover - needs a browser
        from playwright.sync_api import sync_playwright

        with sync_playwright() as driver:
            browser = driver.chromium.launch()
            try:
                while True:
                    job = self._jobs.get()
                    if job is None:
                        return
                    html, reply = job
                    try:
                        page = browser.new_page()
                        try:
                            page.set_content(html, wait_until="load")
                            reply.put(("ok", page.pdf(**PAGE)))
                        finally:
                            page.close()
                    except Exception as exc:
                        reply.put(("error", exc))
            finally:
                browser.close()

    def render(self, html: str, *, timeout: int = 60) -> bytes:
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((html, reply))
        status, payload = reply.get(timeout=timeout)
        if status == "error":
            raise payload
        return payload

    def stop(self) -> None:  # pragma: no cover - process teardown
        self._jobs.put(None)


_renderer: _BrowserThread | None = None
_renderer_lock = threading.Lock()


def _playwright_pdf(html: str) -> bytes:  # pragma: no cover - needs a browser
    global _renderer
    with _renderer_lock:
        if _renderer is None:
            _renderer = _BrowserThread()
            atexit.register(_renderer.stop)
    return _renderer.render(html)
