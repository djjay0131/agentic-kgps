from kg_contracts.assertions import CurationStatus
from kg_contracts.evidence import (
    AbsenceReason,
    EvidenceRef,
    EvidenceRelationship,
    absent_evidence,
)
from kg_contracts.testing.factories import make_assertion

from conftest import NOW, PAPER, PROV
from kgps import GapKind, ProvenanceService, StaticAssertionCatalog


def mk(**kw):
    """An assertion with a source candidate, so NO_SOURCE_CANDIDATE stays out of the way."""
    return make_assertion(**kw).model_copy(update={"source_candidate_ids": ("cand_x",)})


def svc(registry, *assertions):
    return ProvenanceService(StaticAssertionCatalog(assertions), registry)


def kinds(chain):
    return {g.kind for g in chain.gaps}


def test_kgis_extraction_evidence_resolves_to_exact_source_span(registry, chunk_evidence):
    evs, refs = chunk_evidence
    a = mk(predicate="reduces_defects_by", object_value="15%", evidence_refs=(refs[1],))
    chain = svc(registry, a).evidence_chain(a.assertion_id)

    assert chain.grounded
    (link,) = chain.links
    assert link.evidence == evs[1]
    assert PAPER[link.span.start : link.span.end] == evs[1].content
    # KGIS emits DERIVED_FROM only: grounded, but nothing has verified support.
    assert kinds(chain) == {GapKind.NO_SUPPORTS_RELATIONSHIP}


def test_supports_relationship_clears_the_verification_gap(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[1].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    a = mk(evidence_refs=(ref,))
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert chain.grounded and chain.gaps == ()


def test_no_refs_is_blocking(registry):
    a = mk()
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert not chain.grounded
    assert kinds(chain) == {GapKind.NO_EVIDENCE_REFS}


def test_dangling_ref_is_blocking(registry):
    a = make_assertion(
        evidence_refs=(EvidenceRef(evidence_id="ev_gone", relationship=EvidenceRelationship.SUPPORTS),)
    )
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert not chain.grounded
    assert {GapKind.DANGLING_EVIDENCE_REF, GapKind.NO_PRESENT_EVIDENCE} <= kinds(chain)


def test_absent_only_is_blocking_and_names_the_reason(registry):
    ev = absent_evidence(
        evidence_id="ev_abs", source_type="api", source_locator="k", observed_at=NOW,
        reason=AbsenceReason.SOURCE_UNAVAILABLE, provenance=PROV,
    )
    registry.put(ev)
    a = make_assertion(
        evidence_refs=(EvidenceRef(evidence_id="ev_abs", relationship=EvidenceRelationship.SUPPORTS),)
    )
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert not chain.grounded
    gap = next(g for g in chain.gaps if g.kind is GapKind.EVIDENCE_ABSENT)
    assert "SOURCE_UNAVAILABLE" in gap.detail


def test_contradicting_evidence_does_not_ground(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[2].evidence_id, relationship=EvidenceRelationship.CONTRADICTS)
    a = make_assertion(evidence_refs=(ref,))
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert not chain.grounded
    assert {GapKind.CONTRADICTING_EVIDENCE, GapKind.NO_PRESENT_EVIDENCE} <= kinds(chain)


def test_structured_row_evidence_is_grounded_but_unanchored(registry, structured_evidence):
    ref = EvidenceRef(evidence_id="ev_row1", relationship=EvidenceRelationship.DERIVED_FROM)
    a = make_assertion(evidence_refs=(ref,))
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert chain.grounded
    assert {GapKind.NO_SPAN, GapKind.HASH_ONLY_EVIDENCE} <= kinds(chain)


def test_superseded_assertion_is_explained_with_a_status_gap(registry, chunk_evidence):
    _, refs = chunk_evidence
    a = make_assertion(status=CurationStatus.SUPERSEDED, superseded_at=NOW, evidence_refs=(refs[0],))
    chain = svc(registry, a).evidence_chain(a.assertion_id)
    assert chain.grounded
    assert GapKind.NOT_ACTIVE in kinds(chain)


def test_unknown_assertion(registry):
    chain = svc(registry).evidence_chain("as_missing")
    assert chain.assertion is None and not chain.grounded
    assert kinds(chain) == {GapKind.UNKNOWN_ASSERTION}


def test_explain_summarises_span_actor_and_model(registry, chunk_evidence):
    _, refs = chunk_evidence
    a = make_assertion(predicate="reduces_defects_by", object_value="15%", evidence_refs=(refs[1],))
    text = svc(registry, a).explain(a.assertion_id).summary
    assert "reduces_defects_by '15%'" in text
    assert "doi:10.1145/3793657.3793888 chars" in text
    assert "actor claim-extractor, model vt-arc/llama" in text
    assert text.endswith("Grounded.")
