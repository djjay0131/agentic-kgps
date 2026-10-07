"""Parse the character spans KGIS encodes inside evidence locators.

KGIS extraction writes ``source_locator = f"{locator}#{fragment}"`` with
``fragment = f"chunk:{i}@chars:{start}-{end}"`` (agentic-kgis
``kgis/extraction/documents.py``). Structured sync writes key-field fragments
that carry no span, and those parse to ``None``. This is the one place KGPS
depends on that string format; it goes away when KGIS adds a typed span
(design spec §7, upstream U1).
"""

import re

from kgps.models import SourceSpan

_FRAGMENT = re.compile(r"^(?:chunk:(?P<chunk>\d+)@)?chars:(?P<start>\d+)-(?P<end>\d+)$")


def parse_span(source_locator: str) -> SourceSpan | None:
    """Return the span in ``source_locator``, or ``None`` if it carries none."""
    locator, sep, fragment = source_locator.rpartition("#")
    if not sep or not locator:
        return None
    match = _FRAGMENT.match(fragment)
    if match is None:
        return None
    start, end = int(match["start"]), int(match["end"])
    if end < start:
        return None
    chunk = match["chunk"]
    return SourceSpan(
        locator=locator,
        chunk_index=int(chunk) if chunk is not None else None,
        start=start,
        end=end,
    )
