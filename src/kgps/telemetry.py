"""OpenTelemetry spans for provenance queries (wave 2b; decision D-015).

``opentelemetry-api`` is an optional extra (``agentic-kgps[otel]``). When it
is not installed every helper here is a no-op, so the core service keeps a
single hard dependency (``agentic-kgis``). When it is installed but no SDK /
exporter is configured, the API's own no-op tracer applies: KGPS never
configures a tracer provider itself — that is the host application's job.

Span names are ``kgps.<operation>``. Attributes use the ``kgps.`` prefix and
carry identifiers and counts only, never evidence payloads or quotes (they may
hold source text the deployer has not cleared for telemetry backends).
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

try:  # pragma: no cover - exercised by whichever env runs the tests
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]

TRACER_NAME = "kgps"

AttrValue = str | bool | int | float

MAX_ATTR_LEN = 256
"""Caller-supplied strings (ids, trace ids) are truncated: an agent passing
text where an id belongs must not ship that text to a telemetry backend."""


def bounded(value: str) -> str:
    return value if len(value) <= MAX_ATTR_LEN else value[:MAX_ATTR_LEN] + "…"


def otel_available() -> bool:
    return _otel_trace is not None


def _result_attributes(result: object) -> dict[str, AttrValue]:
    """Counts and verdicts from a KGPS result, by duck typing (no payloads)."""
    attrs: dict[str, AttrValue] = {}
    grounded = getattr(result, "grounded", None)
    if isinstance(grounded, bool):
        attrs["kgps.grounded"] = grounded
    chain = getattr(result, "chain", result)
    gaps = getattr(chain, "gaps", None)
    if isinstance(gaps, tuple):
        attrs["kgps.gap_count"] = len(gaps)
        blocking = sorted({g.kind.value for g in gaps if getattr(g, "blocking", False)})
        if blocking:
            attrs["kgps.blocking_gaps"] = ",".join(blocking)
    links = getattr(chain, "links", None)
    if isinstance(links, tuple):
        attrs["kgps.link_count"] = len(links)
    revalidate = getattr(result, "needs_revalidation", None)
    if isinstance(revalidate, tuple):
        attrs["kgps.impacted_count"] = len(revalidate)
    if isinstance(result, tuple):
        attrs["kgps.result_count"] = len(result)
    return attrs


@contextmanager
def span(operation: str, attributes: Mapping[str, AttrValue] | None = None) -> Iterator[Any]:
    """A ``kgps.<operation>`` span, or a no-op when OpenTelemetry is absent."""
    if _otel_trace is None:
        yield None
        return
    tracer = _otel_trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(f"kgps.{operation}") as current:
        for key, value in (attributes or {}).items():
            current.set_attribute(key, bounded(value) if isinstance(value, str) else value)
        yield current


def traced(operation: str, subject_attr: str = "kgps.subject_id") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a query method: span named ``kgps.<operation>``.

    The first positional argument after ``self`` (an assertion or evidence id)
    is recorded under ``subject_attr``; result counts are added on return.
    Exceptions are recorded on the span and re-raised unchanged.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if _otel_trace is None:
                return fn(*args, **kwargs)
            attrs: dict[str, AttrValue] = {}
            subject = args[1] if len(args) > 1 else next(iter(kwargs.values()), None)
            if isinstance(subject, str):
                attrs[subject_attr] = subject
            with span(operation, attrs) as current:
                result = fn(*args, **kwargs)
                for key, value in _result_attributes(result).items():
                    current.set_attribute(key, value)
                return result

        return wrapper

    return decorate
