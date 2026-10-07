"""Typed provenance for generated output (PA-AKG §II; design spec §5).

The PA-AKG proposal's net-new claim is that per-claim provenance is a *typed,
propagated, verifiable artifact* in every message between agents, not a
post-hoc trace. This module is the contract for that:

* ``Citation`` / ``CitedSentence`` / ``GroundedAnswer`` — what a generating
  agent must emit: each output sentence (by character span) cites canonical
  assertions, optionally narrowed to specific evidence ids.
* ``ProvenanceEnvelope`` — the message wrapper between agent roles
  (supervisor → retriever → curator → validator → generator), so each hop
  carries the evidence chain rather than free text.
* ``build_provenance_graph`` — resolves an answer into the full graph
  output span → assertion → evidence span, using ``ProvenanceService``.
* ``score_answer`` — the first provenance metrics of the evaluation harness
  (proposal §Evaluation): citation coverage and chain completeness. Faithfulness
  (does the cited evidence *entail* the sentence?) needs a verifier and is
  wave 3 (design spec §6).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kgps.models import EvidenceChain
from kgps.service import ProvenanceService


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str
    # Narrow to particular evidence of that assertion; empty = all of it.
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CitedSentence(BaseModel):
    """One unit of generated output and the assertions it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def _check_span(self) -> "CitedSentence":
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    text: str
    sentences: tuple[CitedSentence, ...]
    produced_by: str
    model: str | None = None
    trace_id: str

    @model_validator(mode="after")
    def _check_sentences(self) -> "GroundedAnswer":
        for s in self.sentences:
            if self.text[s.start : s.end] != s.text:
                raise ValueError(f"sentence span {s.start}-{s.end} does not match answer text")
        return self


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    RETRIEVER = "retriever"
    CURATOR = "curator"
    VALIDATOR = "validator"
    GENERATOR = "generator"


class EnvelopeKind(StrEnum):
    RETRIEVAL_RESULT = "retrieval_result"  # candidate assertions + evidence ids
    CURATION_RESULT = "curation_result"  # accepted/rejected assertions with audit ids
    VALIDATION_VERDICT = "validation_verdict"  # accept/reject with cited justification
    ANSWER = "answer"  # a GroundedAnswer


class ProvenanceEnvelope(BaseModel):
    """A message between agents that cannot be sent without its evidence.

    ``citations`` must be non-empty unless ``abstained`` is set: an agent that
    has no grounding says so explicitly instead of passing ungrounded text on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sender: AgentRole
    recipient: AgentRole
    kind: EnvelopeKind
    trace_id: str
    parent_trace_id: str | None = None
    citations: tuple[Citation, ...] = ()
    abstained: bool = False
    rationale: str | None = None
    answer: GroundedAnswer | None = None

    @model_validator(mode="after")
    def _check_grounding(self) -> "ProvenanceEnvelope":
        if not self.citations and not self.abstained and self.answer is None:
            raise ValueError("an envelope must carry citations, an answer, or abstain")
        if self.kind is EnvelopeKind.ANSWER and self.answer is None:
            raise ValueError("ANSWER envelopes carry a GroundedAnswer")
        return self


class EdgeKind(StrEnum):
    CITES = "CITES"  # output span -> assertion
    BACKED_BY = "BACKED_BY"  # assertion -> evidence (relationship in `label`)
    DERIVED_FROM = "DERIVED_FROM"  # assertion -> upstream lineage node


class ProvenanceEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EdgeKind
    source: str
    target: str
    label: str | None = None


class ProvenanceGraph(BaseModel):
    """output span → assertion → evidence (span), for one answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    edges: tuple[ProvenanceEdge, ...]
    chains: dict[str, EvidenceChain]


def sentence_node(index: int, s: CitedSentence) -> str:
    return f"out:{index}@chars:{s.start}-{s.end}"


def build_provenance_graph(answer: GroundedAnswer, svc: ProvenanceService) -> ProvenanceGraph:
    edges: list[ProvenanceEdge] = []
    chains: dict[str, EvidenceChain] = {}
    for i, sentence in enumerate(answer.sentences):
        node = sentence_node(i, sentence)
        for c in sentence.citations:
            edges.append(ProvenanceEdge(kind=EdgeKind.CITES, source=node, target=c.assertion_id))
            if c.assertion_id in chains:
                continue
            chain = svc.evidence_chain(c.assertion_id)
            chains[c.assertion_id] = chain
            for link in chain.links:
                target = link.evidence_id
                if link.span is not None:
                    target += f"#chars:{link.span.start}-{link.span.end}"
                edges.append(
                    ProvenanceEdge(
                        kind=EdgeKind.BACKED_BY,
                        source=c.assertion_id,
                        target=target,
                        label=link.relationship.value,
                    )
                )
            for n in chain.lineage:
                if n.parent_ref is not None:
                    edges.append(
                        ProvenanceEdge(
                            kind=EdgeKind.DERIVED_FROM,
                            source=n.parent_ref,
                            target=n.ref,
                            label=n.kind,
                        )
                    )
    return ProvenanceGraph(trace_id=answer.trace_id, edges=tuple(edges), chains=chains)


class AnswerScore(BaseModel):
    """Provenance metrics for one answer (evaluation harness, wave 1 subset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentences: int
    cited_sentences: int
    grounded_sentences: int
    citations: int
    grounded_citations: int
    span_anchored_citations: int

    @property
    def citation_coverage(self) -> float:
        """Share of sentences that cite anything at all."""
        return self.cited_sentences / self.sentences if self.sentences else 0.0

    @property
    def chain_completeness(self) -> float:
        """Share of sentences whose every citation resolves to a grounded chain."""
        return self.grounded_sentences / self.sentences if self.sentences else 0.0

    @property
    def span_anchoring(self) -> float:
        """Share of grounded citations whose evidence carries a character span."""
        return self.span_anchored_citations / self.grounded_citations if self.grounded_citations else 0.0


def score_answer(answer: GroundedAnswer, svc: ProvenanceService) -> AnswerScore:
    graph = build_provenance_graph(answer, svc)
    cited = grounded_sentences = citations = grounded_citations = anchored = 0
    for s in answer.sentences:
        if s.citations:
            cited += 1
        all_ok = bool(s.citations)
        for c in s.citations:
            citations += 1
            chain = graph.chains[c.assertion_id]
            links = [
                link
                for link in chain.links
                if not c.evidence_ids or link.evidence_id in c.evidence_ids
            ]
            ok = chain.grounded and (
                not c.evidence_ids or any(link.evidence is not None for link in links)
            )
            if ok:
                grounded_citations += 1
                if any(link.span is not None for link in links):
                    anchored += 1
            else:
                all_ok = False
        if all_ok:
            grounded_sentences += 1
    return AnswerScore(
        sentences=len(answer.sentences),
        cited_sentences=cited,
        grounded_sentences=grounded_sentences,
        citations=citations,
        grounded_citations=grounded_citations,
        span_anchored_citations=anchored,
    )
