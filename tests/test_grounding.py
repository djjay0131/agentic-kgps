import pytest
from kg_contracts.testing.factories import make_assertion
from pydantic import ValidationError

from kgps import (
    AgentRole,
    Citation,
    CitedSentence,
    EnvelopeKind,
    GroundedAnswer,
    ProvenanceEnvelope,
    ProvenanceService,
    StaticAssertionCatalog,
    build_provenance_graph,
    score_answer,
)


def answer(text, sentences):
    return GroundedAnswer(
        question="Does automated code review reduce defects?",
        text=text,
        sentences=tuple(sentences),
        produced_by="navigator",
        trace_id="tr_1",
    )


def sentence(text, full, *citations):
    start = full.index(text)
    return CitedSentence(text=text, start=start, end=start + len(text), citations=citations)


@pytest.fixture
def setup(registry, chunk_evidence, structured_evidence):
    _, refs = chunk_evidence
    from kg_contracts.evidence import EvidenceRef, EvidenceRelationship

    claim = make_assertion(predicate="reduces_defects_by", object_value="15%",
                           evidence_refs=(refs[1],))
    scope = make_assertion(predicate="holds_for", object_value="teams>5",
                           evidence_refs=(refs[2],))
    row = make_assertion(predicate="team_size", object_value=7,
                         evidence_refs=(EvidenceRef(evidence_id="ev_row1",
                                                    relationship=EvidenceRelationship.SUPPORTS),))
    ungrounded = make_assertion(predicate="made_up", object_value="x")
    svc = ProvenanceService(StaticAssertionCatalog((claim, scope, row, ungrounded)), registry)
    return svc, claim, scope, row, ungrounded


def test_provenance_graph_links_output_span_to_assertion_to_evidence_span(setup):
    svc, claim, scope, *_ = setup
    full = "Defects fell 15%. Only for larger teams."
    ans = answer(full, [
        sentence("Defects fell 15%.", full, Citation(assertion_id=claim.assertion_id)),
        sentence("Only for larger teams.", full, Citation(assertion_id=scope.assertion_id)),
    ])
    g = build_provenance_graph(ans, svc)
    cites = [(e.source, e.target) for e in g.edges if e.kind == "CITES"]
    assert cites[0] == ("out:0@chars:0-17", claim.assertion_id)
    backed = [e for e in g.edges if e.kind == "BACKED_BY" and e.source == claim.assertion_id]
    assert backed[0].target.endswith("#chars:53-117") and backed[0].label == "DERIVED_FROM"


def test_score_answer(setup):
    svc, claim, scope, row, ungrounded = setup
    full = "A. B. C. D."
    ans = answer(full, [
        sentence("A.", full, Citation(assertion_id=claim.assertion_id)),
        sentence("B.", full, Citation(assertion_id=row.assertion_id)),
        sentence("C.", full, Citation(assertion_id=ungrounded.assertion_id)),
        sentence("D.", full),
    ])
    s = score_answer(ans, svc)
    assert (s.sentences, s.cited_sentences, s.grounded_sentences) == (4, 3, 2)
    assert s.citation_coverage == 0.75
    assert s.chain_completeness == 0.5
    assert s.span_anchoring == 0.5  # claim is span-anchored, the table row is not


def test_answer_sentence_spans_must_match_text():
    with pytest.raises(ValidationError):
        answer("Hello world.", [CitedSentence(text="Bye", start=0, end=3)])


def test_envelope_refuses_ungrounded_messages():
    with pytest.raises(ValidationError):
        ProvenanceEnvelope(sender=AgentRole.RETRIEVER, recipient=AgentRole.VALIDATOR,
                           kind=EnvelopeKind.RETRIEVAL_RESULT, trace_id="t")
    ok = ProvenanceEnvelope(sender=AgentRole.RETRIEVER, recipient=AgentRole.VALIDATOR,
                            kind=EnvelopeKind.RETRIEVAL_RESULT, trace_id="t", abstained=True,
                            rationale="no candidate assertions above threshold")
    assert ok.abstained
    roundtrip = ProvenanceEnvelope.model_validate_json(
        ProvenanceEnvelope(sender=AgentRole.SUPERVISOR, recipient=AgentRole.GENERATOR,
                           kind=EnvelopeKind.CURATION_RESULT, trace_id="t",
                           citations=(Citation(assertion_id="as_1", confidence=0.9),)
                           ).model_dump_json()
    )
    assert roundtrip.citations[0].confidence == 0.9
