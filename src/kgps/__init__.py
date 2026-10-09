"""KGPS — Knowledge Graph Provenance Service (PA-AKG read side).

KGIS ingests and records evidence; KGCS curates and records its decisions;
KGPS reads both and answers "why is this here, what does it rest on, and what
breaks if that source changes" — for canonical assertions and for the
generated output that cites them. Read-only by construction (ADR-0001).
"""

from kgps.api import ProvenanceAPI
from kgps.grounding import (
    AgentRole,
    AnswerScore,
    Citation,
    CitedSentence,
    EnvelopeKind,
    GroundedAnswer,
    ProvenanceEnvelope,
    ProvenanceGraph,
    build_provenance_graph,
    score_answer,
)
from kgps.models import (
    CurationDecision,
    EvidenceChain,
    EvidenceLink,
    Explanation,
    GapKind,
    ImpactReport,
    LineageNode,
    ProvenanceGap,
    SourceSpan,
)
from kgps.ports import (
    AssertionCatalog,
    AssertionLookup,
    CandidateRefLookup,
    CurationAuditLookup,
    EvidenceLookup,
    EvidenceSubjectLookup,
    GraphAssertionIndex,
    ReaderAssertionCatalog,
    StaticAssertionCatalog,
    catalog_for,
)
from kgps.service import ProvenanceService
from kgps.spans import parse_span, span_for

__version__ = "0.3.0"

__all__ = [
    "AgentRole",
    "AnswerScore",
    "AssertionCatalog",
    "AssertionLookup",
    "CandidateRefLookup",
    "EvidenceSubjectLookup",
    "ReaderAssertionCatalog",
    "catalog_for",
    "span_for",
    "Citation",
    "CurationAuditLookup",
    "CurationDecision",
    "CitedSentence",
    "EnvelopeKind",
    "EvidenceChain",
    "EvidenceLink",
    "EvidenceLookup",
    "Explanation",
    "GapKind",
    "GraphAssertionIndex",
    "GroundedAnswer",
    "ImpactReport",
    "LineageNode",
    "ProvenanceEnvelope",
    "ProvenanceGap",
    "ProvenanceGraph",
    "ProvenanceAPI",
    "ProvenanceService",
    "SourceSpan",
    "StaticAssertionCatalog",
    "build_provenance_graph",
    "parse_span",
    "score_answer",
]
