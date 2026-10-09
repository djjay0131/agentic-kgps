"""Wave 2a: kg_contracts 2.2/2.3 features (typed spans, candidate join, successors)."""

from datetime import UTC, datetime

from kg_contracts.assertions import CurationStatus
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship, Provenance, present_evidence
from kg_contracts.testing.factories import make_assertion, make_entity

from conftest import NOW, PAPER
from kgps import (
    GapKind,
    ProvenanceService,
    ReaderAssertionCatalog,
    StaticAssertionCatalog,
    catalog_for,
    span_for,
)

LATER = datetime(2026, 10, 8, tzinfo=UTC)


def kinds(chain):
    return {g.kind for g in chain.gaps}


def with_candidates(a, *cids):
    return a.model_copy(update={"source_candidate_ids": tuple(cids)})


def test_typed_span_is_preferred_and_carries_the_quote(chunk_evidence):
    evs, _ = chunk_evidence
    span = span_for(evs[1])
    assert span is not None and span.typed
    assert PAPER[span.start : span.end] == evs[1].content


def test_legacy_locator_only_evidence_is_flagged_untyped(registry):
    ev = present_evidence(
        evidence_id="ev_legacy", source_type="paper", source_locator="doc#chunk:0@chars:0-10",
        observed_at=NOW, content="0123456789", provenance=Provenance(source="t", actor="t"),
    )
    registry.put(ev)
    a = with_candidates(make_assertion(evidence_refs=(
        EvidenceRef(evidence_id="ev_legacy", relationship=EvidenceRelationship.SUPPORTS),)), "c1")
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert chain.grounded
    assert GapKind.UNTYPED_SPAN in kinds(chain)
    assert chain.links[0].span.start == 0 and not chain.links[0].span.typed


def test_evidence_is_recovered_through_source_candidates(registry, chunk_evidence):
    _, refs = chunk_evidence
    registry.add_refs("cand_1", [refs[1]])
    a = with_candidates(make_assertion(), "cand_1")  # cites nothing itself
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert chain.grounded
    assert chain.links[0].via_candidate == "cand_1"
    assert GapKind.EVIDENCE_VIA_CANDIDATE in kinds(chain)
    assert any(n.kind == "candidate" and n.via == "source_candidate" for n in chain.lineage)


def test_candidate_refs_do_not_duplicate_assertion_refs(registry, chunk_evidence):
    _, refs = chunk_evidence
    registry.add_refs("cand_1", [refs[1], refs[2]])
    a = with_candidates(make_assertion(evidence_refs=(refs[1],)), "cand_1")
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert [link.via_candidate for link in chain.links] == [None, "cand_1"]
    assert GapKind.EVIDENCE_VIA_CANDIDATE not in kinds(chain)


def test_missing_candidate_link_is_a_non_blocking_gap(registry, chunk_evidence):
    _, refs = chunk_evidence
    a = make_assertion(evidence_refs=(refs[0],))
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert chain.grounded and GapKind.NO_SOURCE_CANDIDATE in kinds(chain)


def test_impacted_by_follows_candidates_via_reverse_index(registry, chunk_evidence):
    evs, refs = chunk_evidence
    registry.add_refs("cand_1", [refs[0]])
    a = with_candidates(make_assertion(), "cand_1")
    b = make_assertion(evidence_refs=(refs[0],))
    report = ProvenanceService(StaticAssertionCatalog([a, b]), registry).impacted_by(
        evs[0].evidence_id
    )
    assert report.direct == (b.assertion_id,)
    assert report.via_candidates == (a.assertion_id,)
    assert report.citing_candidates == ("cand_1",)
    assert set(report.needs_revalidation) == {a.assertion_id, b.assertion_id}


def test_successor_chain_and_explain(registry, chunk_evidence):
    _, refs = chunk_evidence
    new = with_candidates(make_assertion(evidence_refs=(refs[2],)), "c2")
    mid = with_candidates(make_assertion(evidence_refs=(refs[1],)), "c1")
    mid = mid.model_copy(update={"status": CurationStatus.SUPERSEDED, "superseded_at": LATER,
                                 "superseded_by": new.assertion_id})
    old = with_candidates(make_assertion(evidence_refs=(refs[0],)), "c0")
    old = old.model_copy(update={"status": CurationStatus.SUPERSEDED, "superseded_at": LATER,
                                 "superseded_by": mid.assertion_id})
    svc = ProvenanceService(StaticAssertionCatalog([old, mid, new]), registry)
    chain = svc.evidence_chain(old.assertion_id)
    assert chain.successors == (mid.assertion_id, new.assertion_id)
    assert chain.current_assertion_id == new.assertion_id
    text = svc.explain(old.assertion_id).summary
    assert f"current: {new.assertion_id}" in text and "From candidate(s): c0" in text
    assert "model vt-arc/llama@2026-09" in text  # Provenance.model_version (ADR-0023)


def test_unresolved_successor_is_a_gap(registry):
    ghost = make_assertion()
    a = make_assertion().model_copy(update={"status": CurationStatus.SUPERSEDED,
                                            "superseded_at": LATER,
                                            "superseded_by": ghost.assertion_id})
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert GapKind.UNRESOLVED_SUCCESSOR in kinds(chain)


def test_redacted_evidence_is_reported(chunk_evidence):
    evs, refs = chunk_evidence

    class Redacting:
        def get(self, eid):
            return next((e for e in evs if e.evidence_id == eid), None)

        def redaction(self, eid):
            return ("2026-10-09T00:00:00Z", "erased") if eid == evs[0].evidence_id else (None, None)

    a = with_candidates(make_assertion(evidence_refs=(refs[0],)), "c")
    chain = ProvenanceService(StaticAssertionCatalog([a]), Redacting()).evidence_chain(
        a.assertion_id
    )
    assert GapKind.REDACTED_EVIDENCE in kinds(chain)


def test_reader_catalog_uses_native_get_assertion(graph, registry, chunk_evidence):
    _, refs = chunk_evidence
    e = make_entity()
    graph.put_entity(e)
    a = make_assertion(subject_identity=e.identity_id, evidence_refs=(refs[0],))
    graph.put_assertion(a)
    graph.mark_superseded(a.assertion_id, LATER)
    catalog = catalog_for(graph)
    assert isinstance(catalog, ReaderAssertionCatalog)
    assert catalog.get_assertion(a.assertion_id).status is CurationStatus.SUPERSEDED
    assert [x.assertion_id for x in catalog.iter_assertions()] == [a.assertion_id]


# --- review round (PR #2) -----------------------------------------------------

from kg_contracts.evidence import AbsenceReason  # noqa: E402
from kg_contracts.stores import UnsupportedCapabilityError  # noqa: E402

from kgps import GraphAssertionIndex  # noqa: E402


class _NoLookupReader:
    """Wraps a memory store but hides get_assertion support."""

    def __init__(self, inner, raise_on_get=False):
        self._inner = inner
        self._raise = raise_on_get

    def capabilities(self):
        return self._inner.capabilities().model_copy(update={"supports_assertion_lookup": False})

    def find_entities(self, *a, **k):
        return self._inner.find_entities(*a, **k)

    def assertions_for(self, *a, **k):
        return self._inner.assertions_for(*a, **k)

    def get_assertion(self, *a, **k):
        if self._raise:
            raise UnsupportedCapabilityError("no lookup")
        return None  # an inherited Protocol stub would do this silently


def _graph_with_one(graph, refs):
    e = make_entity()
    graph.put_entity(e)
    a = make_assertion(subject_identity=e.identity_id, evidence_refs=(refs[0],))
    graph.put_assertion(a)
    return a


def test_catalog_for_falls_back_when_lookup_not_advertised(graph, chunk_evidence):
    _, refs = chunk_evidence
    a = _graph_with_one(graph, refs)
    catalog = catalog_for(_NoLookupReader(graph))
    assert isinstance(catalog, GraphAssertionIndex)
    assert catalog.get_assertion(a.assertion_id) is not None


def test_reader_catalog_falls_back_on_unsupported_capability(graph, chunk_evidence):
    _, refs = chunk_evidence
    a = _graph_with_one(graph, refs)
    catalog = ReaderAssertionCatalog(_NoLookupReader(graph, raise_on_get=True))
    assert catalog.get_assertion(a.assertion_id).assertion_id == a.assertion_id


def test_successor_cycle_and_depth_limit_are_gaps(registry):
    a, b = make_assertion(), make_assertion()
    sup = {"status": CurationStatus.SUPERSEDED, "superseded_at": LATER}
    a = a.model_copy(update={**sup, "superseded_by": b.assertion_id})
    b = b.model_copy(update={**sup, "superseded_by": a.assertion_id})
    chain = ProvenanceService(StaticAssertionCatalog([a, b]), registry).evidence_chain(
        a.assertion_id
    )
    assert GapKind.LINEAGE_CYCLE in kinds(chain) and chain.successors == (b.assertion_id,)

    chainlist = [make_assertion() for _ in range(5)]
    for i in range(4):
        chainlist[i] = chainlist[i].model_copy(
            update={**sup, "superseded_by": chainlist[i + 1].assertion_id}
        )
    svc = ProvenanceService(StaticAssertionCatalog(chainlist), registry, max_depth=2)
    assert GapKind.LINEAGE_DEPTH_LIMIT in kinds(svc.evidence_chain(chainlist[0].assertion_id))


def test_typed_span_without_fragment_keeps_full_locator():
    from kg_contracts.evidence import TextSpan

    ev = present_evidence(
        evidence_id="ev_url", source_type="web", source_locator="https://x.org/a#section",
        observed_at=NOW, content="abc", provenance=Provenance(source="t", actor="t"),
        span=TextSpan(start=3, end=6, quote="abc"),
    )
    span = span_for(ev)
    assert span.typed and span.locator == "https://x.org/a#section" and span.quote == "abc"


def test_assertion_in_direct_is_not_repeated_in_via_candidates(registry, chunk_evidence):
    evs, refs = chunk_evidence
    registry.add_refs("cand_1", [refs[0]])
    a = with_candidates(make_assertion(evidence_refs=(refs[0],)), "cand_1")
    report = ProvenanceService(StaticAssertionCatalog([a]), registry).impacted_by(
        evs[0].evidence_id
    )
    assert report.direct == (a.assertion_id,) and report.via_candidates == ()


def test_contextualizes_only_is_not_grounded(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[0].evidence_id,
                      relationship=EvidenceRelationship.CONTEXTUALIZES)
    a = with_candidates(make_assertion(evidence_refs=(ref,)), "c")
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert not chain.grounded and GapKind.NO_PRESENT_EVIDENCE in kinds(chain)


def test_dangling_candidate_ref_does_not_unground_supported_assertion(registry, chunk_evidence):
    _, refs = chunk_evidence
    registry.add_refs("cand_1", [EvidenceRef(evidence_id="ev_gone",
                                             relationship=EvidenceRelationship.SUPPORTS)])
    a = with_candidates(make_assertion(evidence_refs=(refs[1],)), "cand_1")
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert chain.grounded
    gap = next(g for g in chain.gaps if g.kind is GapKind.DANGLING_EVIDENCE_REF)
    assert "via candidate cand_1" in gap.detail


def test_store_errors_become_gaps_not_exceptions(chunk_evidence):
    _, refs = chunk_evidence

    class Broken:
        def get(self, eid):
            raise RuntimeError("database is locked")

        def refs_for(self, sid):
            raise RuntimeError("database is locked")

        def subjects_for(self, eid):
            raise RuntimeError("database is locked")

    a = with_candidates(make_assertion(evidence_refs=(refs[0],)), "c")
    svc = ProvenanceService(StaticAssertionCatalog([a]), Broken())
    chain = svc.evidence_chain(a.assertion_id)
    assert GapKind.STORE_ERROR in kinds(chain) and not chain.grounded
    report = svc.impacted_by("ev_x")
    assert report.errors and report.errors[0].kind is GapKind.STORE_ERROR


def test_absent_evidence_still_reported(registry):
    from kg_contracts.evidence import absent_evidence

    registry.put(absent_evidence(evidence_id="ev_abs2", source_type="api", source_locator="k",
                                 observed_at=NOW, reason=AbsenceReason.SOURCE_OMITTED,
                                 provenance=Provenance(source="t", actor="t")))
    a = with_candidates(make_assertion(evidence_refs=(
        EvidenceRef(evidence_id="ev_abs2", relationship=EvidenceRelationship.SUPPORTS),)), "c")
    chain = ProvenanceService(StaticAssertionCatalog([a]), registry).evidence_chain(a.assertion_id)
    assert {GapKind.EVIDENCE_ABSENT, GapKind.NO_PRESENT_EVIDENCE} <= kinds(chain)
