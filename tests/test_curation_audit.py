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


def test_real_kgcs_record_projects_and_round_trips_through_a_readonly_store(tmp_path):
    kgcs_audit = pytest.importorskip("kgcs.observability.semantic_audit")
    kgcs_sqlite = pytest.importorskip("kgcs.persistence.sqlite")
    from kgps.config import AUDIT_DB_ENV, ReadOnlyAudit

    rec = kgcs_audit.AssertionSemanticAuditRecord.model_validate_json(FIXTURE.read_text())
    db = tmp_path / "audit.db"
    sink = kgcs_sqlite.SqliteSemanticAuditSink(sqlite3.connect(db))
    sink.record(rec)

    ro = ReadOnlyAudit(db)
    (got,) = ro.records_for_assertion(rec.assertion_ids[0])
    d = CurationDecision.from_record(got)
    assert d.audit_id == rec.audit_id and d.final_kind == "SUPERSESSION"
    assert d.decision_kind == "ASSERTION" and d.consulted_adviser
    with pytest.raises(Exception, match="readonly"):
        ro._sink().record(rec.model_copy(update={"audit_id": "au_other"}))
    assert AUDIT_DB_ENV == "KGPS_AUDIT_DB"


def test_audit_config_errors(tmp_path):
    from kgps.config import ConfigError, ReadOnlyAudit

    with pytest.raises(ConfigError, match="not found"):
        ReadOnlyAudit(tmp_path / "nope.db")
