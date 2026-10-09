"""KGPS — Knowledge Graph Provenance Service (PA-AKG read side).

KGIS ingests and records evidence; KGCS curates and records its decisions;
KGPS reads both and answers "why is this here, what does it rest on, and what
breaks if that source changes" — for canonical assertions and for the
generated output that cites them. Read-only by construction (ADR-0001).
"""

from kgps.api import ProvenanceAPI
from kgps.correct import CorrectionAction, CorrectionResult, correct_answer
from kgps.export import chain_to_prov, graph_to_prov
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
from kgps.harness import Baseline, EvalCase, HarnessReport, Perturbation, run_harness
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
from kgps.routing import (
    DenseRetriever,
    GraphRetriever,
    ProvenanceRouter,
    RetrievalMode,
    Retriever,
    SparseRetriever,
    route,
)
from kgps.service import ProvenanceService
from kgps.spans import parse_span, span_for
from kgps.verify import (
    AnswerVerification,
    Judgement,
    LexicalVerifier,
    LLMJudgeVerifier,
    SupportsProposal,
    Verdict,
    VerificationScore,
    Verifier,
    propose_supports,
    verify_answer,
)

__version__ = "0.5.0"

__all__ = [
    "Baseline",
    "DenseRetriever",
    "EvalCase",
    "GraphRetriever",
    "HarnessReport",
    "Perturbation",
    "ProvenanceRouter",
    "RetrievalMode",
    "Retriever",
    "SparseRetriever",
    "chain_to_prov",
    "graph_to_prov",
    "route",
    "run_harness",
    "AnswerVerification",
    "CorrectionAction",
    "CorrectionResult",
    "Judgement",
    "LLMJudgeVerifier",
    "LexicalVerifier",
    "SupportsProposal",
    "Verdict",
    "VerificationScore",
    "Verifier",
    "correct_answer",
    "propose_supports",
    "verify_answer",
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
