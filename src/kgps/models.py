"""Read-side provenance records (PA-AKG, design spec §4).

These are *views* over records that KGIS and KGCS already persist: no type
here is ever written back to the canonical graph (ADR-0001). Every view is
frozen and JSON-serialisable, so it can travel unchanged in an HTTP response,
an MCP tool result, or a typed message between agents (``grounding.py``).

Gaps are data, not exceptions (ADR-0003). A chain that cannot be fully
reconstructed is still returned, with each defect named as a
``ProvenanceGap`` and a ``blocking`` flag saying whether the defect means the
assertion is *not grounded* (as opposed to *grounded, but weakly anchored*).
"""

from enum import StrEnum

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import Evidence, EvidenceAvailability, EvidenceRelationship
from pydantic import BaseModel, ConfigDict, Field


class SourceSpan(BaseModel):
    """A character span inside a source document.

    Preferred source is the typed ``Evidence.span`` (kg_contracts >= 2.2.0,
    agentic-kgis #56); ``typed=False`` marks a span recovered by parsing the
    legacy ``<locator>#chunk:<i>@chars:<start>-<end>`` fragment instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    locator: str
    chunk_index: int | None = None
    start: int
    end: int
    quote: str | None = None
    typed: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start


class GapKind(StrEnum):
    """Why a provenance chain is incomplete."""

    # Blocking: the assertion is not grounded by present evidence.
    NO_EVIDENCE_REFS = "NO_EVIDENCE_REFS"
    DANGLING_EVIDENCE_REF = "DANGLING_EVIDENCE_REF"
    NO_PRESENT_EVIDENCE = "NO_PRESENT_EVIDENCE"
    UNKNOWN_ASSERTION = "UNKNOWN_ASSERTION"
    # Non-blocking: grounded, but the anchor or the lineage is weaker than it should be.
    EVIDENCE_ABSENT = "EVIDENCE_ABSENT"
    EVIDENCE_ERROR = "EVIDENCE_ERROR"
    NO_SUPPORTS_RELATIONSHIP = "NO_SUPPORTS_RELATIONSHIP"
    CONTRADICTING_EVIDENCE = "CONTRADICTING_EVIDENCE"
    NO_SPAN = "NO_SPAN"
    UNTYPED_SPAN = "UNTYPED_SPAN"
    HASH_ONLY_EVIDENCE = "HASH_ONLY_EVIDENCE"
    REDACTED_EVIDENCE = "REDACTED_EVIDENCE"
    NOT_ACTIVE = "NOT_ACTIVE"
    NO_SOURCE_CANDIDATE = "NO_SOURCE_CANDIDATE"
    EVIDENCE_VIA_CANDIDATE = "EVIDENCE_VIA_CANDIDATE"
    UNRESOLVED_LINEAGE_INPUT = "UNRESOLVED_LINEAGE_INPUT"
    UNRESOLVED_SUCCESSOR = "UNRESOLVED_SUCCESSOR"
    LINEAGE_CYCLE = "LINEAGE_CYCLE"
    LINEAGE_DEPTH_LIMIT = "LINEAGE_DEPTH_LIMIT"


BLOCKING_GAPS: frozenset[GapKind] = frozenset(
    {
        GapKind.NO_EVIDENCE_REFS,
        GapKind.DANGLING_EVIDENCE_REF,
        GapKind.NO_PRESENT_EVIDENCE,
        GapKind.UNKNOWN_ASSERTION,
    }
)


class ProvenanceGap(BaseModel):
    """One named defect in a provenance chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: GapKind
    subject_id: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_GAPS


class EvidenceLink(BaseModel):
    """One assertion → evidence edge, with the evidence resolved if it exists.

    ``via_candidate`` is set when the edge was not cited on the assertion
    itself but recovered through one of its ``source_candidate_ids`` and the
    evidence registry's candidate-keyed refs (ADR-0028 join).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    relationship: EvidenceRelationship
    evidence: Evidence | None
    span: SourceSpan | None = None
    via_candidate: str | None = None

    @property
    def resolved(self) -> bool:
        return self.evidence is not None

    @property
    def model_version(self) -> str | None:
        if self.evidence is None:
            return None
        return getattr(self.evidence.provenance, "model_version", None)


class LineageNode(BaseModel):
    """A node reached by walking ``Derivation.inputs`` (and candidate links).

    ``kind`` mirrors ``DerivationInput.kind`` (``assertion``, ``evidence``,
    ``artifact``, ``candidate``); the root assertion itself has kind
    ``assertion`` and depth 0. ``parent_ref`` is the node that consumed this
    one, so the list of nodes encodes the lineage DAG as parent edges.
    ``via`` says which edge produced the node: ``derivation`` or
    ``source_candidate`` (ADR-0028).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    ref: str
    depth: int = Field(ge=0)
    parent_ref: str | None = None
    method: str | None = None
    implementation_version: str | None = None
    resolved: bool = True
    via: str = "derivation"


class EvidenceChain(BaseModel):
    """Everything KGPS can reconstruct about why one assertion is in the graph.

    ``assertion`` is ``None`` only when the id is unknown to the reader, in
    which case ``gaps`` carries a single ``UNKNOWN_ASSERTION``. ``successors``
    is the forward ``superseded_by`` chain (oldest first), so a superseded
    assertion can be explained and its current replacement found.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str
    assertion: Assertion | None
    links: tuple[EvidenceLink, ...] = ()
    lineage: tuple[LineageNode, ...] = ()
    successors: tuple[str, ...] = ()
    gaps: tuple[ProvenanceGap, ...] = ()

    @property
    def grounded(self) -> bool:
        """True when no blocking gap exists: at least one PRESENT evidence resolves."""
        return not any(gap.blocking for gap in self.gaps)

    @property
    def present_evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            link.evidence
            for link in self.links
            if link.evidence is not None
            and link.evidence.availability is EvidenceAvailability.PRESENT
            and link.relationship
            in (EvidenceRelationship.SUPPORTS, EvidenceRelationship.DERIVED_FROM)
        )

    @property
    def source_candidate_ids(self) -> tuple[str, ...]:
        if self.assertion is None:
            return ()
        return tuple(getattr(self.assertion, "source_candidate_ids", ()))

    @property
    def current_assertion_id(self) -> str:
        """The latest record in the supersession chain (itself if not superseded)."""
        return self.successors[-1] if self.successors else self.assertion_id


class ImpactReport(BaseModel):
    """Assertions whose grounding depends on one piece of evidence (reverse lineage).

    ``direct`` cite the evidence through ``evidence_refs`` or a derivation
    input; ``via_candidates`` reach it because one of their
    ``source_candidate_ids`` cites it in the evidence registry (ADR-0028);
    ``transitive`` reach it only through ``Derivation.inputs`` of other
    assertions. Together they are the re-validation set when that evidence is
    retracted, revised or found wrong (design spec §4.4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    direct: tuple[str, ...] = ()
    via_candidates: tuple[str, ...] = ()
    transitive: tuple[str, ...] = ()
    citing_candidates: tuple[str, ...] = ()

    @property
    def needs_revalidation(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for a in (*self.direct, *self.via_candidates, *self.transitive):
            seen.setdefault(a, None)
        return tuple(seen)


class Explanation(BaseModel):
    """A human/agent-readable answer to "why is this assertion here?"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str
    summary: str
    grounded: bool
    chain: EvidenceChain
