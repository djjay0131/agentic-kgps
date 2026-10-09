"""KGCS curation-audit join in explain (decision D-019)."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.testing.factories import make_assertion

from kgps import GapKind, ProvenanceService, StaticAssertionCatalog
from kgps.api import ProvenanceAPI
from kgps.models import CurationDecision

FIXTURE = Path(__file__).parent / "fixtures" / "kgcs_assertion_audit.json"


def record(assertion_id, **kw):
    """Duck-typed stand-in for kgcs AssertionSemanticAuditRecord."""
    base = dict(
        audit_id="au_1",
        decision_kind=SimpleNamespace(value="ASSERTION"),
        trace_id="tr_c",
        plan_id="pl_1",
        final=SimpleNamespace(kind="SUPERSESSION", rationale="newer evidence"),
        review=SimpleNamespace(action="APPROVE", status="APPLIED"),
        assessments=("one",),
        recorded_at=datetime(2026, 10, 1, tzinfo=UTC),
        assertion_ids=(assertion_id,),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class Audit:
    def __init__(self, by_id):
        self.by_id = by_id

    def records_for_assertion(self, assertion_id):
        return self.by_id.get(assertion_id, [])


@pytest.fixture
def grounded(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[1].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    return make_assertion(evidence_refs=(ref,)).model_copy(
        update={"source_candidate_ids": ("cand_x",)}
    )


def test_explain_reports_the_curation_decision(registry, grounded):
    aid = grounded.assertion_id
    svc = ProvenanceService(
        StaticAssertionCatalog((grounded,)), registry, audit=Audit({aid: [record(aid)]})
    )
    exp = svc.explain(aid)
    (d,) = exp.chain.decisions
    assert (d.audit_id, d.decision_kind, d.final_kind) == ("au_1", "ASSERTION", "SUPERSESSION")
    assert d.consulted_adviser and d.review_action == "APPROVE"
    assert "Curated: ASSERTION decision SUPERSESSION at 2026-10-01" in exp.summary
    assert exp.grounded and exp.chain.gaps == ()
    body = ProvenanceAPI(svc).explain(aid)
    assert body["chain"]["decisions"][0]["audit_id"] == "au_1"
    json.dumps(body)


def test_missing_audit_is_a_non_blocking_gap(registry, grounded):
    svc = ProvenanceService(StaticAssertionCatalog((grounded,)), registry, audit=Audit({}))
    chain = svc.evidence_chain(grounded.assertion_id)
    assert chain.grounded
    assert [g.kind for g in chain.gaps] == [GapKind.NO_CURATION_AUDIT]


def test_without_an_audit_port_nothing_changes(registry, grounded):
    svc = ProvenanceService(StaticAssertionCatalog((grounded,)), registry)
    chain = svc.evidence_chain(grounded.assertion_id)
    assert chain.decisions == () and chain.gaps == ()


def test_audit_failures_are_store_errors(registry, grounded):
    class Broken:
        def records_for_assertion(self, _):
            raise sqlite3.OperationalError("locked")

    aid = grounded.assertion_id
    svc = ProvenanceService(StaticAssertionCatalog((grounded,)), registry, audit=Broken())
    kinds = [g.kind for g in svc.evidence_chain(aid).gaps]
    assert kinds == [GapKind.STORE_ERROR]  # not also NO_CURATION_AUDIT

    bad = Audit({aid: [SimpleNamespace(audit_id="au_x", recorded_at="not a date")]})
    svc = ProvenanceService(StaticAssertionCatalog((grounded,)), registry, audit=bad)
    kinds = [g.kind for g in svc.evidence_chain(aid).gaps]
    assert kinds == [GapKind.STORE_ERROR]


def _hand_built_store(db, records):
    """The two KGCS tables KGPS reads, built without kgcs (mirrors its DDL)."""
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE semantic_audit_records (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "audit_id TEXT NOT NULL UNIQUE, decision_kind TEXT NOT NULL, trace_id TEXT NOT NULL, "
        "plan_id TEXT, recorded_at TEXT, record_json TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE semantic_audit_assertions (audit_id TEXT NOT NULL, assertion_id TEXT NOT NULL)"
    )
    for rec in records:
        conn.execute(
            "INSERT INTO semantic_audit_records "
            "(audit_id, decision_kind, trace_id, record_json) VALUES (?, ?, ?, ?)",
            (rec["audit_id"], rec["decision_kind"], rec["trace_id"], json.dumps(rec)),
        )
        for aid in rec["assertion_ids"]:
            conn.execute("INSERT INTO semantic_audit_assertions VALUES (?, ?)",
                         (rec["audit_id"], aid))
    conn.commit()
    conn.close()


def test_readonly_audit_reads_real_record_json_without_kgcs(tmp_path, registry, chunk_evidence):
    from kg_contracts.evidence import EvidenceRef as Ref

    from kgps.config import ASSERTIONS_ENV, AUDIT_DB_ENV, EVIDENCE_DB_ENV, service_from_env

    rec = json.loads(FIXTURE.read_text())
    old_id, new_id = rec["assertion_ids"]
    db = tmp_path / "audit.db"
    _hand_built_store(db, [rec])

    evs, _ = chunk_evidence
    from kgis.evidence.store import SqliteEvidenceRegistry

    evdb = tmp_path / "ev.db"
    SqliteEvidenceRegistry(str(evdb)).put_many(evs)
    ref = Ref(evidence_id=evs[0].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    old = make_assertion(evidence_refs=(ref,)).model_copy(
        update={"assertion_id": old_id, "source_candidate_ids": ("c",)})
    new = make_assertion(evidence_refs=(ref,)).model_copy(
        update={"assertion_id": new_id, "source_candidate_ids": ("c",)})
    jl = tmp_path / "a.jsonl"
    jl.write_text(old.model_dump_json() + "\n" + new.model_dump_json() + "\n")

    svc = service_from_env(
        {EVIDENCE_DB_ENV: str(evdb), ASSERTIONS_ENV: str(jl), AUDIT_DB_ENV: str(db)}
    )
    old_exp, new_exp = svc.explain(old_id), svc.explain(new_id)
    (d_old,), (d_new,) = old_exp.chain.decisions, new_exp.chain.decisions
    assert (d_old.decision_kind, d_old.final_kind) == ("ASSERTION", "SUPERSESSION")
    assert d_old.role == "prior" and d_new.role == "resulting"
    assert d_old.recorded_at is not None and d_old.consulted_adviser
    assert "this assertion: prior" in old_exp.summary
    assert "this assertion: resulting" in new_exp.summary
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        svc._audit._conn().execute("DELETE FROM semantic_audit_records")
    svc._audit.close()


def test_er_records_and_abstaining_advisers_project_honestly():
    er = {
        "audit_id": "au_er", "decision_kind": "ER", "trace_id": "t",
        "final": {"action": "MERGE", "rationale": "same paper"},
        "assessments": [{"abstained": True}], "review": {"action": "APPROVE"},
    }
    d = CurationDecision.from_record(er, "as_1")
    assert (d.decision_kind, d.final_kind, d.role) == ("ER", "MERGE", "affected")
    assert d.consulted_adviser is False  # every adviser abstained
    assert d.review_status is None


def test_unknown_assertion_gets_no_audit_gap(registry):
    svc = ProvenanceService(StaticAssertionCatalog(()), registry, audit=Audit({}))
    assert [g.kind for g in svc.evidence_chain("nope").gaps] == [GapKind.UNKNOWN_ASSERTION]


def test_mixed_good_and_bad_records(registry, grounded):
    aid = grounded.assertion_id
    bad = SimpleNamespace(audit_id="au_x", recorded_at="not a date")
    svc = ProvenanceService(
        StaticAssertionCatalog((grounded,)), registry,
        audit=Audit({aid: [record(aid), bad]}),
    )
    chain = svc.evidence_chain(aid)
    assert [d.audit_id for d in chain.decisions] == ["au_1"]
    assert [g.kind for g in chain.gaps] == [GapKind.STORE_ERROR]


def test_real_kgcs_writer_store_is_readable(tmp_path):
    kgcs_audit = pytest.importorskip("kgcs.observability.semantic_audit")
    kgcs_sqlite = pytest.importorskip("kgcs.persistence.sqlite")
    from kgps.config import ReadOnlyAudit

    rec = kgcs_audit.AssertionSemanticAuditRecord.model_validate_json(FIXTURE.read_text())
    db = tmp_path / "audit.db"
    kgcs_sqlite.SqliteSemanticAuditSink(sqlite3.connect(db)).record(rec)
    ro = ReadOnlyAudit(db)
    (got,) = ro.records_for_assertion(rec.assertion_ids[0])
    d = CurationDecision.from_record(got, rec.assertion_ids[0])
    assert d.audit_id == rec.audit_id and d.final_kind == "SUPERSESSION" and d.role == "prior"
    # Same projection from the typed object KGCS itself returns.
    typed = kgcs_sqlite.SqliteSemanticAuditSink(sqlite3.connect(db)).records_for_assertion(
        rec.assertion_ids[0])[0]
    assert CurationDecision.from_record(typed, rec.assertion_ids[0]) == d
    ro.close()


def test_audit_config_errors(tmp_path):
    from kgps.config import ConfigError, ReadOnlyAudit

    with pytest.raises(ConfigError, match="not found"):
        ReadOnlyAudit(tmp_path / "nope.db")
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("create table x(a)").connection.commit()
    with pytest.raises(ConfigError, match="missing semantic_audit_assertions"):
        ReadOnlyAudit(other)
