"""What KGPS reads, stated as narrow protocols (ADR-0002).

KGPS is read-only over every store it touches (ADR-0001). It needs three
lookups that ``kg_contracts`` does not yet expose as a single port:

* ``EvidenceLookup`` — satisfied as-is by agentic-kgis ``SqliteEvidenceRegistry``.
* ``AssertionLookup`` — fetch one canonical assertion *by id*, including
  superseded and revoked records. ``GraphReader`` only offers
  ``assertions_for(identity_id)``, so ``GraphAssertionIndex`` adapts any
  ``GraphReader`` by scanning it. A store-native implementation (a Neo4j
  index on ``assertion_id``) should replace the scan in production; proposing
  ``get_assertion`` upstream is design spec §7, upstream U4.
* ``AssertionCatalog`` — iterate all assertions, needed for reverse lineage
  (``impacted_by``). Same adapter, same caveat.
"""

from collections.abc import Iterable, Iterator
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import Evidence
from kg_contracts.stores import GraphReader, GraphReadOptions


@runtime_checkable
class EvidenceLookup(Protocol):
    def get(self, evidence_id: str) -> Evidence | None: ...


@runtime_checkable
class AssertionLookup(Protocol):
    def get_assertion(self, assertion_id: str) -> Assertion | None: ...


@runtime_checkable
class AssertionCatalog(AssertionLookup, Protocol):
    def iter_assertions(self) -> Iterator[Assertion]: ...


HISTORY = GraphReadOptions(include_superseded=True, include_revoked=True)


class GraphAssertionIndex:
    """An ``AssertionCatalog`` built by scanning a ``GraphReader``.

    Provenance must explain superseded and revoked records too, so the scan
    reads with both history switches on. The index is a snapshot taken at
    construction; call ``refresh()`` after the graph moves to a new epoch.
    """

    def __init__(self, reader: GraphReader, options: GraphReadOptions = HISTORY) -> None:
        self._reader = reader
        self._options = options
        self._by_id: dict[str, Assertion] = {}
        self.refresh()

    def refresh(self) -> None:
        by_id: dict[str, Assertion] = {}
        for entity in self._reader.find_entities(options=self._options):
            for assertion in self._reader.assertions_for(entity.identity_id, self._options):
                by_id[assertion.assertion_id] = assertion
        self._by_id = by_id

    def get_assertion(self, assertion_id: str) -> Assertion | None:
        return self._by_id.get(assertion_id)

    def iter_assertions(self) -> Iterator[Assertion]:
        return iter(tuple(self._by_id.values()))


class StaticAssertionCatalog:
    """An ``AssertionCatalog`` over an explicit collection (tests, exports, fixtures)."""

    def __init__(self, assertions: Iterable[Assertion]) -> None:
        self._by_id = {a.assertion_id: a for a in assertions}

    def get_assertion(self, assertion_id: str) -> Assertion | None:
        return self._by_id.get(assertion_id)

    def iter_assertions(self) -> Iterator[Assertion]:
        return iter(tuple(self._by_id.values()))
