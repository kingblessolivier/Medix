"""Pagination.

Numbered pages for browsable resources; cursor for append-only tables
where OFFSET is expensive and rows insert constantly.

See docs/27-layout.md.
"""

from rest_framework.pagination import CursorPagination as DrfCursorPagination
from rest_framework.pagination import PageNumberPagination as DrfPageNumberPagination


class PageNumberPagination(DrfPageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class LedgerCursorPagination(DrfCursorPagination):
    """For stock movements, sales and audit events."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
    ordering = "-occurred_at"
