"""What KGPS reads, stated as narrow protocols (ADR-0002, amended by ADR-0004).

KGPS is read-only over every store it touches (ADR-0001).

* ``EvidenceLookup`` — ``get(evidence_id)``; agentic-kgis
  ``SqliteEvidenceRegistry`` satisfies it.
* ``CandidateRefLookup`` / ``EvidenceSubjectLookup`` — the registry's
  candidate-keyed refs (``refs_for``) and its reverse index (``subjects_for``,
  agentic-kgis #59). Optional: when the evidence store provides them, KGPS
  follows ``Assertion.source_candidate_ids`` (ADR-0028) into the registry.
* ``AssertionLookup`` — one assertion by id, any status. Since kg_contracts
  2.2.0 ``GraphReader.get_assertion`` provides this natively;
  ``ReaderAssertionCatalog`` uses it and only falls back to a scan for
  iteration.
* ``AssertionCatalog`` — lookup plus iteration, needed for reverse lineage
  through ``Derivation.inputs`` (no reverse derivation index exists upstream).
"""

from collections.abc import Iterable, Iterator
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import Evidence, EvidenceRef
from kg_contracts.stores import GraphReader, GraphReadOptions


@runtime_checkable
class EvidenceLookup(Protocol):
    def get(self, evidence_id: str) -> Evidence | None: ...


@runtime_checkable
class CandidateRefLookup(Protocol):
    def refs_for(self, subject_id: str) -> list[EvidenceRef]: ...


@runtime_checkable
class EvidenceSubjectLookup(Protocol):
    def subjects_for(self, evidence_id: str) -> list[str]: ...


@runtime_checkable
class AssertionLookup(Protocol):
    def get_assertion(self, assertion_id: str) -> Assertion | None: ...


@runtime_checkable
class AssertionCatalog(AssertionLookup, Protocol):
    def iter_assertions(self) -> Iterator[Assertion]: ...


HISTORY = GraphReadOptions(include_superseded=True, include_revoked=True)


def _scan(reader: GraphReader, options: GraphReadOptions) -> dict[str, Assertion]:
    by_id: dict[str, Assertion] = {}
    for entity in reader.find_entities(options=options):
        for assertion in reader.assertions_for(entity.identity_id, options):
            by_id[assertion.assertion_id] = assertion
    return by_id


class GraphAssertionIndex:
    """An ``AssertionCatalog`` built by scanning a ``GraphReader`` (snapshot).

    Kept for readers without ``get_assertion`` and for tests. Provenance must
    explain superseded and revoked records too, so the scan reads with both
    history switches on. Call ``refresh()`` after the graph moves epochs.
    """

    def __init__(self, reader: GraphReader, options: GraphReadOptions = HISTORY) -> None:
        self._reader = reader
        self._options = options
        self._by_id: dict[str, Assertion] = {}
        self.refresh()

    def refresh(self) -> None:
        self._by_id = _scan(self._reader, self._options)

    def get_assertion(self, assertion_id: str) -> Assertion | None:
        return self._by_id.get(assertion_id)

    def iter_assertions(self) -> Iterator[Assertion]:
        return iter(tuple(self._by_id.values()))


class ReaderAssertionCatalog:
    """An ``AssertionCatalog`` that asks the reader directly (ADR-0004).

    ``get_assertion`` delegates to ``GraphReader.get_assertion`` with history
    switches on — live, no snapshot, indexed when the adapter advertises
    ``supports_assertion_lookup``. ``iter_assertions`` still scans, because
    reverse derivation lineage has no upstream index; it is only used by
    ``ProvenanceService.impacted_by``.
    """

    def __init__(self, reader: GraphReader, options: GraphReadOptions = HISTORY) -> None:
        if not hasattr(reader, "get_assertion"):
            raise TypeError(
                "reader lacks get_assertion (kg_contracts < 2.2.0); use GraphAssertionIndex"
            )
        self._reader = reader
        self._options = options

    def get_assertion(self, assertion_id: str) -> Assertion | None:
        return self._reader.get_assertion(assertion_id, self._options)

    def iter_assertions(self) -> Iterator[Assertion]:
        return iter(tuple(_scan(self._reader, self._options).values()))


def catalog_for(reader: GraphReader) -> AssertionCatalog:
    """The best catalog for ``reader``: direct lookup when available, else a scan."""
    if hasattr(reader, "get_assertion"):
        return ReaderAssertionCatalog(reader)
    return GraphAssertionIndex(reader)


class StaticAssertionCatalog:
    """An ``AssertionCatalog`` over an explicit collection (tests, exports, fixtures)."""

    def __init__(self, assertions: Iterable[Assertion]) -> None:
        self._by_id = {a.assertion_id: a for a in assertions}

    def get_assertion(self, assertion_id: str) -> Assertion | None:
        return self._by_id.get(assertion_id)

    def iter_assertions(self) -> Iterator[Assertion]:
        return iter(tuple(self._by_id.values()))
