"""Wave 2b: JSON API, MCP tools, HTTP router, env config, telemetry."""

import asyncio
import json

import pytest
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.testing.factories import make_assertion

from kgps import ProvenanceService, StaticAssertionCatalog
from kgps.api import ProvenanceAPI
from kgps.config import (
    ASSERTIONS_ENV,
    EVIDENCE_DB_ENV,
    FACTORY_ENV,
    ConfigError,
    service_from_env,
)


@pytest.fixture
def registry(tmp_path):
    """Servers call the service from worker threads (FastAPI threadpool, MCP),
    so the registry connection must allow cross-thread use, as config.py does."""
    import sqlite3

    from kgis.evidence.store import SqliteEvidenceRegistry

    db = tmp_path / "evidence.db"
    SqliteEvidenceRegistry(str(db))  # KGIS creates the schema
    return SqliteEvidenceRegistry(sqlite3.connect(db, check_same_thread=False))


def mk(**kw):
    return make_assertion(**kw).model_copy(update={"source_candidate_ids": ("cand_x",)})


@pytest.fixture
def world(registry, chunk_evidence):
    evs, refs = chunk_evidence
    sup = EvidenceRef(evidence_id=evs[1].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    claim = mk(predicate="reduces_defects_by", object_value="15%", evidence_refs=(sup,))
    bare = mk(predicate="made_up", object_value="x")
    svc = ProvenanceService(StaticAssertionCatalog((claim, bare)), registry)
    return svc, claim, bare, evs


def answer_for(claim):
    text = "Defects fell 15%."
    return {
        "question": "q",
        "text": text,
        "sentences": [
            {"text": text, "start": 0, "end": len(text),
             "citations": [{"assertion_id": claim.assertion_id}]}
        ],
        "produced_by": "navigator",
        "trace_id": "tr_9",
    }


# -- ProvenanceAPI -------------------------------------------------------------


def test_api_payloads_are_json_and_carry_computed_fields(world):
    svc, claim, bare, evs = world
    api = ProvenanceAPI(svc)

    exp = api.explain(claim.assertion_id)
    json.dumps(exp)  # fully serialisable
    assert exp["grounded"] is True
    chain = exp["chain"]
    assert chain["present_evidence_ids"] == [evs[1].evidence_id]
    assert chain["links"][0]["resolved"] is True
    assert chain["links"][0]["model_version"] == "2026-09"
    assert chain["current_assertion_id"] == claim.assertion_id

    nope = api.explain(bare.assertion_id)
    assert nope["grounded"] is False
    assert any(g["kind"] == "NO_EVIDENCE_REFS" and g["blocking"] for g in nope["chain"]["gaps"])


def test_api_unknown_assertion_is_data_not_error(world):
    api = ProvenanceAPI(world[0])
    body = api.explain("does/not/exist")
    assert body["grounded"] is False
    assert [g["kind"] for g in body["chain"]["gaps"]] == ["UNKNOWN_ASSERTION"]
    assert api.successors("does/not/exist")["successors"] == []
    assert api.lineage("does/not/exist")["lineage"] == []


def test_api_impact_and_scores(world):
    svc, claim, _, evs = world
    api = ProvenanceAPI(svc)
    impact = api.impacted_by(evs[1].evidence_id)
    assert impact["needs_revalidation"] == [claim.assertion_id]

    score = api.score_answer(answer_for(claim))
    assert score["chain_completeness"] == 1.0 and score["trace_id"] == "tr_9"
    graph = api.provenance_graph(answer_for(claim))
    assert {e["kind"] for e in graph["edges"]} >= {"CITES", "BACKED_BY"}
    json.dumps(graph)


def test_api_rejects_malformed_answers(world):
    api = ProvenanceAPI(world[0])
    with pytest.raises(ValueError, match="invalid GroundedAnswer"):
        api.score_answer({"question": "q"})


def test_store_failure_in_lineage_and_successors_is_a_gap_not_an_exception(registry):
    class Broken:
        def get_assertion(self, _):
            raise RuntimeError("db down")

        def iter_assertions(self):
            raise RuntimeError("db down")

    svc = ProvenanceService(Broken(), registry)
    assert svc.lineage("a") == () and svc.successors("a") == ()
    body = ProvenanceAPI(svc).lineage("a")
    assert "STORE_ERROR" in {g["kind"] for g in body["gaps"]}


# -- MCP -----------------------------------------------------------------------


def test_mcp_server_registers_all_tools_and_answers(world):
    pytest.importorskip("mcp")
    from kgps.mcp_server import TOOL_NAMES, build_server

    svc, claim, *_ = world
    server = build_server(svc)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == set(TOOL_NAMES)
    assert all(t.description for t in tools)

    result = asyncio.run(server.call_tool("kg_explain", {"assertion_id": claim.assertion_id}))
    content = result.content if hasattr(result, "content") else result
    if isinstance(content, tuple):  # mcp<2 returns (content, structured)
        content = content[0]
    payload = json.loads(content[0].text)
    assert payload["grounded"] is True

    scored = asyncio.run(server.call_tool("kg_score_answer", {"answer": answer_for(claim)}))
    content = scored.content if hasattr(scored, "content") else scored
    if isinstance(content, tuple):
        content = content[0]
    assert json.loads(content[0].text)["citation_coverage"] == 1.0


# -- HTTP ----------------------------------------------------------------------


@pytest.fixture
def client(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from kgps.http import create_app

    return TestClient(create_app(world[0]))


def test_http_routes(client, world):
    _, claim, _, evs = world
    aid = claim.assertion_id
    assert client.get("/health").json()["service"] == "kgps"
    assert client.get(f"/assertions/{aid}/explain").json()["grounded"] is True
    assert client.get(f"/assertions/{aid}/chain", params={"with_lineage": False}).json()[
        "lineage"
    ] == []
    assert client.get(f"/assertions/{aid}/lineage").status_code == 200
    assert client.get(f"/assertions/{aid}/successors").json()["current_assertion_id"] == aid
    assert client.get(f"/evidence/{evs[1].evidence_id}/impact").json()["direct"] == [aid]
    r = client.post("/answers/score", json=answer_for(claim))
    assert r.status_code == 200 and r.json()["grounded_citations"] == 1
    assert client.post("/answers/provenance", json=answer_for(claim)).status_code == 200


def test_http_ids_with_slashes_and_bad_bodies(client):
    r = client.get("/assertions/doi:10.1145/3793657/explain")
    assert r.status_code == 200
    assert r.json()["assertion_id"] == "doi:10.1145/3793657"
    assert client.post("/answers/score", json={"question": "q"}).status_code == 422


# -- config --------------------------------------------------------------------


def test_service_from_files_is_read_only(tmp_path, chunk_evidence):
    from kgis.evidence.store import SqliteEvidenceRegistry

    evs, refs = chunk_evidence
    db = tmp_path / "exported.db"
    SqliteEvidenceRegistry(str(db)).put_many(evs)
    a = mk(evidence_refs=(refs[0],))
    jl = tmp_path / "assertions.jsonl"
    jl.write_text(a.model_dump_json() + "\n\n", encoding="utf-8")

    svc = service_from_env({EVIDENCE_DB_ENV: str(db), ASSERTIONS_ENV: str(jl)})
    assert svc.evidence_chain(a.assertion_id).grounded
    with pytest.raises(Exception, match="readonly"):
        svc._evidence.put(evs[0])  # ADR-0001: KGPS cannot write the registry


def test_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        service_from_env({})
    with pytest.raises(ConfigError, match="module:callable"):
        service_from_env({FACTORY_ENV: "nocolon"})
    with pytest.raises(ConfigError, match="not ProvenanceService"):
        service_from_env({FACTORY_ENV: "builtins:dict"})
    bad = tmp_path / "a.jsonl"
    bad.write_text("{}\n", encoding="utf-8")
    db = tmp_path / "missing.db"
    with pytest.raises(ConfigError, match="not found"):
        service_from_env({EVIDENCE_DB_ENV: str(db), ASSERTIONS_ENV: str(bad)})


def factory_for_test():
    return ProvenanceService(StaticAssertionCatalog(()), _NullEvidence())


class _NullEvidence:
    def get(self, _):
        return None


def test_config_factory(monkeypatch):
    svc = service_from_env({FACTORY_ENV: "test_w2b_surfaces:factory_for_test"})
    assert isinstance(svc, ProvenanceService)


# -- telemetry -----------------------------------------------------------------


def test_spans_are_emitted_without_payloads(world):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("kgps")
    monkey = trace.get_tracer
    trace.get_tracer = lambda *_a, **_k: tracer  # avoid the global set-once provider
    try:
        svc, claim, bare, _ = world
        svc.explain(claim.assertion_id)
        svc.evidence_chain(bare.assertion_id)
        ProvenanceAPI(svc).score_answer(answer_for(claim))
    finally:
        trace.get_tracer = monkey

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert {"kgps.explain", "kgps.evidence_chain", "kgps.score_answer"} <= set(spans)
    ex = spans["kgps.explain"].attributes
    assert ex["kgps.assertion_id"] == claim.assertion_id and ex["kgps.grounded"] is True
    bare_span = [
        s for s in exporter.get_finished_spans()
        if s.attributes.get("kgps.assertion_id") == bare.assertion_id
    ][0]
    assert bare_span.attributes["kgps.blocking_gaps"] == "NO_EVIDENCE_REFS"
    for s in exporter.get_finished_spans():  # identifiers and counts only
        for value in s.attributes.values():
            assert "defects fell" not in str(value).lower()
