"""Character spans for evidence.

Since kg_contracts 2.2.0 (agentic-kgis #56) evidence may carry a typed
``Evidence.span`` (``TextSpan(start, end, quote)``); that is always preferred.
Older evidence only encodes the span inside ``source_locator`` as
``<locator>#chunk:<i>@chars:<start>-<end>``; ``parse_span`` recovers it so
pre-2.2 rows remain explainable (decision D-006). Structured-sync fragments
carry no span and yield ``None``.
"""

import re

from kg_contracts.evidence import Evidence

from kgps.models import SourceSpan

_FRAGMENT = re.compile(r"^(?:chunk:(?P<chunk>\d+)@)?chars:(?P<start>\d+)-(?P<end>\d+)$")


def parse_span(source_locator: str) -> SourceSpan | None:
    """Return the span encoded in ``source_locator``, or ``None`` if it carries none."""
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


def span_for(evidence: Evidence) -> SourceSpan | None:
    """The best available span for ``evidence``: typed first, then the locator."""
    parsed = parse_span(evidence.source_locator)
    typed = getattr(evidence, "span", None)
    if typed is not None:
        locator = parsed.locator if parsed is not None else evidence.source_locator.split("#")[0]
        return SourceSpan(
            locator=locator,
            chunk_index=parsed.chunk_index if parsed is not None else None,
            start=typed.start,
            end=typed.end,
            quote=typed.quote,
            typed=True,
        )
    return parsed
