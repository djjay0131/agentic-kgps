"""Wave 4: routing with a provenance gate, B0/B1/B2 harness, PROV-O export."""

import json

import pytest
from kg_contracts.derivation import Derivation, DerivationInput
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion

from kgps import ProvenanceService, StaticAssertionCatalog
from kgps.export import chain_to_prov, graph_to_prov
from kgps.grounding import build_provenance_graph
from kgps.harness import (
    Baseline,
    EvalCase,
    ExtractiveGenerator,
    HiddenEvidence,
    Perturbation,
    run_harness,
)
from kgps.routing import (
    AssertionDocuments,
    DenseRetriever,
    GraphRetriever,
    HashingEmbedder,
    ProvenanceRouter,
    RetrievalMode,
    SparseRetriever,
    fuse,
    route,
)

SUP = EvidenceRelationship.SUPPORTS
IDS = {name: new_identity_id("g") for name in ("review", "effect", "policy", "rumour")}


def superseded(a, by):
    from datetime import UTC, datetime

    from kg_contracts.assertions import CurationStatus

    return a.model_copy(update={
        "assertion_id": "as_old", "superseded_by": by,
        "status": CurationStatus.SUPERSEDED, "superseded_at": datetime(2026, 10, 8, tzinfo=UTC),
    })


def mk(**kw):
    return make_assertion(**kw).model_copy(update={"source_candidate_ids": ("cand_x",)})


@pytest.fixture
def world(registry, chunk_evidence):
    evs, refs = chunk_evidence
    review = mk(subject_identity=IDS["review"], predicate="reduces",
                object_value="post-release defects",
                evidence_refs=(EvidenceRef(evidence_id=evs[0].evidence_id, relationship=SUP),))
    fifteen = mk(subject_identity=IDS["review"], predicate="reduces_defects_by",
                 object_value="15%", evidence_refs=(refs[1],))
    teams = mk(subject_identity=IDS["effect"], predicate="holds_for",
               object_value="teams larger than five developers", evidence_refs=(refs[2],))
    derived = mk(subject_identity=IDS["policy"], predicate="recommends",
                 object_value="code review for large teams",
                 evidence_refs=(refs[2],)).model_copy(update={"derivation": Derivation(
                     method="synthesis", implementation_version="1", deterministic=True,
                     inputs=(DerivationInput(kind="assertion", ref=teams.assertion_id),))})
    ungrounded = mk(subject_identity=IDS["rumour"], predicate="claims",
                    object_value="code review doubles defects in 40 projects")
    catalog = StaticAssertionCatalog((review, fifteen, teams, derived, ungrounded))
    svc = ProvenanceService(catalog, registry)
    return svc, catalog, review, fifteen, teams, derived, ungrounded


# -- routing --------------------------------------------------------------------------


def test_router_rules():
    assert route("why does code review help?").modes == (RetrievalMode.GRAPH, RetrievalMode.DENSE)
    assert route('what fell by "15%"?').modes[0] is RetrievalMode.SPARSE
    assert route("tell me about reviews").modes == (RetrievalMode.DENSE,)
    assert "semantic backstop" in route("x").reason


def test_sparse_dense_graph_retrievers(world):
    svc, catalog, review, fifteen, teams, derived, _ = world
    docs = AssertionDocuments(catalog, svc)
    assert "In our study of 40 projects" in docs.texts[fifteen.assertion_id]
    sparse = SparseRetriever(docs)
    assert sparse.retrieve("defects fell by 15%", 3)[0].assertion_id == fifteen.assertion_id
    dense = DenseRetriever(docs)
    assert dense.retrieve("teams larger than five developers", 2)[0].assertion_id in {
        teams.assertion_id, derived.assertion_id}
    e = HashingEmbedder(64)
    assert e("same text") == e("same text") and len(e("x")) == 64
    graph = GraphRetriever(docs, sparse)
    items = graph.retrieve("five developers effect held", 5)
    ids = {i.assertion_id for i in items}
    assert {teams.assertion_id, derived.assertion_id} <= ids
    # shared subject identity is a neighbour edge too
    via = {i.assertion_id: i.via for i in graph.retrieve("15% defects fell", 5)}
    assert via.get(review.assertion_id) == fifteen.assertion_id


def test_fuse_is_reciprocal_rank_and_deterministic(world):
    svc, catalog, *_ = world
    docs = AssertionDocuments(catalog, svc)
    a = SparseRetriever(docs).retrieve("defects", 5)
    b = DenseRetriever(docs).retrieve("defects", 5)
    assert fuse([a, b], 3) == fuse([a, b], 3)
    assert all(i.mode is RetrievalMode.FUSED for i in fuse([a, b], 3))


def test_provenance_router_drops_ungrounded_and_follows_supersession(world, registry):
    svc, catalog, review, fifteen, teams, derived, ungrounded = world
    docs = AssertionDocuments(catalog, svc)
    sparse = SparseRetriever(docs)
    router = ProvenanceRouter(svc, [sparse, DenseRetriever(docs), GraphRetriever(docs, sparse)])
    r = router.retrieve("code review doubles defects in 40 projects", 5)
    assert ungrounded.assertion_id in r.dropped_ungrounded
    assert all(i.grounded for i in r.items)
    assert ungrounded.assertion_id not in {i.assertion_id for i in r.items}
    open_router = ProvenanceRouter(svc, [sparse], require_grounded=False)
    assert ungrounded.assertion_id in {
        i.assertion_id for i in open_router.retrieve('"doubles" 40', 5).items}

    old = superseded(fifteen, fifteen.assertion_id)
    cat2 = StaticAssertionCatalog((old, fifteen))
    svc2 = ProvenanceService(cat2, registry)
    docs2 = AssertionDocuments(cat2, svc2)
    r2 = ProvenanceRouter(svc2, [SparseRetriever(docs2)]).retrieve('"15%" defects', 5)
    assert r2.replaced_superseded.get("as_old") == fifteen.assertion_id
    assert [i.assertion_id for i in r2.items] == [fifteen.assertion_id]


# -- harness -------------------------------------------------------------------------------


def test_extractive_generator_spans_are_valid():
    ans = ExtractiveGenerator()("defects?", [("a1", "Intro. Defects fell 15%."), ("a2", "")], "t")
    assert ans.text == "Defects fell 15%." and ans.sentences[0].citations[0].assertion_id == "a1"


def test_hidden_evidence_delegates_everything_else(registry, chunk_evidence):
    evs, _ = chunk_evidence
    h = HiddenEvidence(registry, {evs[0].evidence_id})
    assert h.get(evs[0].evidence_id) is None and h.get(evs[1].evidence_id) == evs[1]
    assert h.refs_for("nobody") == []


def test_harness_b2_never_cites_ungrounded_and_recovers_under_perturbation(world, registry):
    svc, catalog, review, fifteen, teams, derived, ungrounded = world
    cases = [
        EvalCase(case_id="c1", question="By how much did defects fall in the 40 projects?",
                 gold_assertion_ids=(fifteen.assertion_id,)),
        EvalCase(case_id="c2", question="Does code review double defects in 40 projects?",
                 gold_assertion_ids=(fifteen.assertion_id,)),
        EvalCase(case_id="c3", question="For which teams did the effect hold?",
                 gold_assertion_ids=(teams.assertion_id,)),
    ]
    report = run_harness(cases, catalog, registry, k=3)
    assert len(report.results) == len(cases) * 3 * 2
    b0 = report.summary(Baseline.B0)
    b2 = report.summary(Baseline.B2)
    b2p = report.summary(Baseline.B2, Perturbation.HIDE_GOLD_EVIDENCE)
    b0p = report.summary(Baseline.B0, Perturbation.HIDE_GOLD_EVIDENCE)
    # The provenance gate: B2 never cites an ungrounded record, with or without perturbation.
    assert b2.ungrounded_citation_rate == 0.0 and b2p.ungrounded_citation_rate == 0.0
    assert b2.chain_completeness == 1.0
    # When the gold evidence disappears, B0 keeps citing what is now ungrounded; B2 does not.
    assert b0p.ungrounded_citation_rate > 0.0
    assert b2.faithfulness >= b0.faithfulness
    table = report.table()
    assert table.count("\n") == 1 + 6 + 2 and "| B2 | hide_gold_evidence |" in table
    # Recall measures retrieval before the gate, so it is comparable across baselines.
    assert b2.recall_at_k == b0.recall_at_k == 1.0
    assert 0.0 <= b2p.faithful_answer_rate <= b2p.faithfulness
    json.dumps(report.model_dump(mode="json"))


# -- PROV-O export ---------------------------------------------------------------------------


def test_chain_export_is_valid_prov_jsonld(world):
    rdflib = pytest.importorskip("rdflib")
    svc, _, review, fifteen, teams, derived, _ = world
    doc = chain_to_prov(svc.evidence_chain(derived.assertion_id))
    text = json.dumps(doc)
    assert "Automated code review" not in text and "teams larger" not in text.replace(
        "teams larger than five developers", "")  # object value only; no evidence text
    g = rdflib.Graph().parse(data=text, format="json-ld")
    PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
    a = rdflib.URIRef(f"urn:kgps:assertion:{derived.assertion_id}")
    t = rdflib.URIRef(f"urn:kgps:assertion:{teams.assertion_id}")
    assert (a, rdflib.RDF.type, PROV.Entity) in g
    assert (a, PROV.wasDerivedFrom, t) in g  # derivation lineage
    derived_from = set(g.objects(a, PROV.wasDerivedFrom))
    assert any("urn:kgps:evidence:" in str(o) for o in derived_from)
    agents = {str(o) for o in g.subjects(rdflib.RDF.type, PROV.SoftwareAgent)}
    assert any("vt-arc" in x and "2026-09" in x for x in agents)


def test_answer_export_links_output_spans_and_supersession(world, registry):
    rdflib = pytest.importorskip("rdflib")
    from kgps.grounding import Citation, CitedSentence, GroundedAnswer

    svc, _, review, fifteen, *_ = world
    text = "Defects fell 15%."
    ans = GroundedAnswer(question="q", text=text, produced_by="navigator", trace_id="tr_x",
                         sentences=(CitedSentence(text=text, start=0, end=len(text), citations=(
                             Citation(assertion_id=fifteen.assertion_id),)),))
    graph = build_provenance_graph(ans, svc)
    doc = graph_to_prov(graph, produced_by="navigator",
                        sentences={"out:0@chars:0-17": text}, include_quotes=True)
    g = rdflib.Graph().parse(data=json.dumps(doc), format="json-ld")
    PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
    out = rdflib.URIRef("urn:kgps:output:" + "tr_x%2Fout%3A0%40chars%3A0-17")
    assert (out, PROV.wasDerivedFrom, rdflib.URIRef(f"urn:kgps:assertion:{fifteen.assertion_id}")) in g
    assert (out, PROV.wasGeneratedBy, rdflib.URIRef("urn:kgps:answer:tr_x")) in g
    assert (out, PROV.value, rdflib.Literal(text)) in g

    old = superseded(fifteen, fifteen.assertion_id)
    svc2 = ProvenanceService(StaticAssertionCatalog((old, fifteen)), registry)
    g2 = rdflib.Graph().parse(data=json.dumps(chain_to_prov(svc2.evidence_chain("as_old"))),
                              format="json-ld")
    assert (rdflib.URIRef(f"urn:kgps:assertion:{fifteen.assertion_id}"), PROV.wasRevisionOf,
            rdflib.URIRef("urn:kgps:assertion:as_old")) in g2


def test_prov_on_api_and_http(world, tmp_path, chunk_evidence):
    from kgps.api import ProvenanceAPI

    svc, _, _, fifteen, *_ = world
    body = ProvenanceAPI(svc).prov(fifteen.assertion_id)
    assert "@graph" in body and "@context" in body
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kgis.evidence.store import SqliteEvidenceRegistry

    from kgps.config import ReadOnlyRegistry
    from kgps.http import create_router

    evs, _ = chunk_evidence
    db = tmp_path / "ev.db"
    SqliteEvidenceRegistry(str(db)).put_many(evs)
    app = FastAPI()
    app.include_router(create_router(ProvenanceService(
        StaticAssertionCatalog((fifteen,)), ReadOnlyRegistry(db))))
    c = TestClient(app)
    r = c.get(f"/assertions/{fifteen.assertion_id}/prov")
    assert r.status_code == 200 and any(
        n.get("kgps:grounded") is True for n in r.json()["@graph"])
    text = "Defects fell 15%."
    ans = {"question": "q", "text": text, "produced_by": "nav", "trace_id": "t",
           "sentences": [{"text": text, "start": 0, "end": len(text),
                          "citations": [{"assertion_id": fifteen.assertion_id}]}]}
    r = c.post("/answers/prov", json=ans)
    assert r.status_code == 200 and "Defects fell" not in r.text


# -- review regressions (PR #6) ---------------------------------------------------------


def test_hidden_evidence_keeps_the_candidate_path(registry, chunk_evidence):
    """isinstance Protocol checks ignore __getattr__: forwarding silently disabled
    candidate-joined evidence for every assertion under perturbation."""
    from kgps.ports import CandidateRefLookup, EvidenceSubjectLookup

    evs, _ = chunk_evidence
    h = HiddenEvidence(registry, {evs[0].evidence_id})
    assert isinstance(h, CandidateRefLookup) and isinstance(h, EvidenceSubjectLookup)
    assert h.subjects_for(evs[0].evidence_id) == []


def test_gate_drops_revoked_records(world, registry):
    from kg_contracts.assertions import CurationStatus

    svc, catalog, review, fifteen, *_ = world
    revoked = fifteen.model_copy(update={"assertion_id": "as_revoked",
                                         "status": CurationStatus.REVOKED})
    cat = StaticAssertionCatalog((revoked, review))
    svc2 = ProvenanceService(cat, registry)
    docs = AssertionDocuments(cat, svc2)
    r = ProvenanceRouter(svc2, [SparseRetriever(docs)]).retrieve('defects fell by "15%"', 5)
    assert "as_revoked" in r.dropped_inactive
    assert "as_revoked" not in {i.assertion_id for i in r.items}
    assert "as_revoked" in {i.assertion_id for i in r.candidates}


def test_router_and_index_never_raise(world):
    svc, catalog, *_ = world
    docs = AssertionDocuments(catalog, svc)

    class Broken:
        mode = RetrievalMode.SPARSE

        def retrieve(self, query, k):
            raise RuntimeError("index down")

    r = ProvenanceRouter(svc, [Broken(), DenseRetriever(docs)]).retrieve('"15%" why', 3)
    assert any("sparse: RuntimeError" in e for e in r.errors)
    assert any("graph: no retriever" in e for e in r.errors)
    assert r.decision.modes == (RetrievalMode.DENSE,) and r.items

    class BadCatalog:
        def get_assertion(self, _):
            return None

        def iter_assertions(self):
            raise OSError("db gone")

    bad = AssertionDocuments(BadCatalog(), svc)
    assert bad.texts == {} and bad.errors


def test_hub_identities_are_not_expanded(registry, chunk_evidence):
    evs, refs = chunk_evidence
    hub = new_identity_id("g")
    many = [mk(subject_identity=hub, predicate=f"p{i}", object_value=str(i),
               evidence_refs=(refs[0],)) for i in range(60)]
    cat = StaticAssertionCatalog(many)
    svc = ProvenanceService(cat, registry)
    docs = AssertionDocuments(cat, svc)
    g = GraphRetriever(docs, SparseRetriever(docs), max_identity_group=50)
    assert all(not n for n in g._neighbours.values())


def test_export_keeps_gap_details_and_text_out_by_default(registry):
    from kgps.export import chain_to_prov

    class Exploding:
        def get(self, _):
            raise RuntimeError("row=('In our study of 40 projects, defects fell by 15%',)")

    a = mk(evidence_refs=(EvidenceRef(evidence_id="ev_x", relationship=SUP),))
    svc = ProvenanceService(StaticAssertionCatalog((a,)), Exploding())
    text = json.dumps(chain_to_prov(svc.evidence_chain(a.assertion_id)))
    assert "defects fell" not in text and "STORE_ERROR @ ev_x" in text
    assert "defects fell" in json.dumps(
        chain_to_prov(svc.evidence_chain(a.assertion_id), include_quotes=True))
