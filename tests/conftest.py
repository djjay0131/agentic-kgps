"""Shared fixtures: real KGIS evidence registry + kg_contracts reference graph."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from kg_contracts.evidence import Provenance, present_evidence
from kg_contracts.testing.memory import MemoryGraphStore
from kgis.evidence.store import SqliteEvidenceRegistry
from kgis.extraction.documents import Document, ParagraphChunker
from kgis.extraction.provenance import build_chunk_evidence, chunk_evidence_ref

NOW = datetime(2026, 10, 7, tzinfo=UTC)
PROV = Provenance(source="test", actor="tester")

PAPER = (
    "Automated code review reduces post-release defects.\n\n"
    "In our study of 40 projects, defects fell by 15% after adoption.\n\n"
    "The effect held only for teams larger than five developers."
)

# Stand-in for kgis ExtractorConfig: build_chunk_evidence reads only these fields.
EXTRACTOR = SimpleNamespace(
    extractor_id="claim-extractor",
    model_id="vt-arc/llama",
    model_version="2026-09",
    prompt_version="p3",
)


@pytest.fixture
def registry() -> SqliteEvidenceRegistry:
    return SqliteEvidenceRegistry(":memory:")


@pytest.fixture
def chunks():
    doc = Document(doc_id="doi:10.1145/3793657.3793888", text=PAPER, source_type="paper")
    return ParagraphChunker().chunk(doc)


@pytest.fixture
def chunk_evidence(registry, chunks):
    """KGIS-produced chunk evidence (real builder), stored in the real registry."""
    evs = [build_chunk_evidence(c, EXTRACTOR, observed_at=NOW) for c in chunks]
    registry.put_many(evs)
    refs = [chunk_evidence_ref(c, EXTRACTOR) for c in chunks]
    return evs, refs


@pytest.fixture
def structured_evidence(registry):
    """Structured-sync style evidence: hash only, key-field fragment, no span."""
    ev = present_evidence(
        evidence_id="ev_row1",
        source_type="table",
        source_locator="roster@snapshot=abc#player_id=7",
        observed_at=NOW,
        payload_hash="deadbeef",
        provenance=PROV,
    )
    registry.put(ev)
    return ev


@pytest.fixture
def graph() -> MemoryGraphStore:
    return MemoryGraphStore()
