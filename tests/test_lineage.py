from kg_contracts.derivation import Derivation, DerivationInput
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.testing.factories import make_assertion, make_entity

from kgps import GapKind, GraphAssertionIndex, ProvenanceService, StaticAssertionCatalog


def derived(*inputs, method="sum"):
    return Derivation(
        method=method,
        deterministic=True,
        inputs=tuple(DerivationInput(kind=k, ref=r) for k, r in inputs),
        implementation_version="1.0",
    )


def build(chunk_evidence):
    evs, refs = chunk_evidence
    base1 = make_assertion(predicate="wall_len", object_value=3, evidence_refs=(refs[0],))
    base2 = make_assertion(predicate="wall_len", object_value=4, evidence_refs=(refs[1],))
    total = make_assertion(
        predicate="total_len",
        object_value=7,
        derivation=derived(("assertion", base1.assertion_id), ("assertion", base2.assertion_id)),
    )
    studs = make_assertion(
        predicate="studs",
        object_value=12,
        derivation=derived(("assertion", total.assertion_id), ("evidence", evs[2].evidence_id),
                           method="stud_calc"),
    )
    return evs, (base1, base2, total, studs)


def test_lineage_walks_derivations_to_evidence(registry, chunk_evidence):
    evs, (b1, b2, total, studs) = build(chunk_evidence)
    svc = ProvenanceService(StaticAssertionCatalog((b1, b2, total, studs)), registry)
    nodes = svc.lineage(studs.assertion_id)
    by_ref = {n.ref: n for n in nodes}
    assert by_ref[studs.assertion_id].depth == 0
    assert by_ref[studs.assertion_id].method == "stud_calc"
    assert by_ref[total.assertion_id].parent_ref == studs.assertion_id
    assert by_ref[b1.assertion_id].depth == 2
    assert by_ref[evs[2].evidence_id].kind == "evidence"
    assert all(n.resolved for n in nodes)


def test_derived_without_direct_evidence_is_flagged_but_lineage_is_returned(
    registry, chunk_evidence
):
    _, (b1, b2, total, studs) = build(chunk_evidence)
    svc = ProvenanceService(StaticAssertionCatalog((b1, b2, total, studs)), registry)
    chain = svc.evidence_chain(total.assertion_id)
    assert GapKind.NO_EVIDENCE_REFS in {g.kind for g in chain.gaps}
    assert "derived; see lineage" in chain.gaps[0].detail
    assert len(chain.lineage) == 3


def test_unresolved_input_and_cycle_are_gaps(registry):
    a = make_assertion(predicate="a", object_value=1)
    b = make_assertion(
        predicate="b", object_value=2,
        derivation=derived(("assertion", "as_nope"), ("evidence", "ev_nope")),
    )
    # a <-> c cycle
    c_id = "as_c"
    a = a.model_copy(update={"derivation": derived(("assertion", c_id))})
    c = make_assertion(predicate="c", object_value=3, derivation=derived(("assertion", a.assertion_id)))
    c = c.model_copy(update={"assertion_id": c_id})
    svc = ProvenanceService(StaticAssertionCatalog((a, b, c)), registry)

    kinds_b = [g.kind for g in svc.evidence_chain(b.assertion_id).gaps]
    assert kinds_b.count(GapKind.UNRESOLVED_LINEAGE_INPUT) == 2
    assert GapKind.LINEAGE_CYCLE in {g.kind for g in svc.evidence_chain(a.assertion_id).gaps}


def test_depth_limit(registry):
    chain = [make_assertion(predicate="p0", object_value=0)]
    for i in range(1, 6):
        chain.append(
            make_assertion(predicate=f"p{i}", object_value=i,
                           derivation=derived(("assertion", chain[-1].assertion_id)))
        )
    svc = ProvenanceService(StaticAssertionCatalog(chain), registry, max_depth=2)
    gaps = svc.evidence_chain(chain[-1].assertion_id).gaps
    assert GapKind.LINEAGE_DEPTH_LIMIT in {g.kind for g in gaps}


def test_impacted_by_returns_direct_and_transitive(registry, chunk_evidence):
    evs, (b1, b2, total, studs) = build(chunk_evidence)
    svc = ProvenanceService(StaticAssertionCatalog((b1, b2, total, studs)), registry)

    r0 = svc.impacted_by(evs[0].evidence_id)
    assert r0.direct == (b1.assertion_id,)
    assert r0.transitive == tuple(sorted((total.assertion_id, studs.assertion_id)))

    r2 = svc.impacted_by(evs[2].evidence_id)  # consumed only through a Derivation input
    assert r2.direct == (studs.assertion_id,) and r2.transitive == ()
    assert svc.impacted_by("ev_unused").needs_revalidation == ()


def test_graph_assertion_index_reads_history_from_a_graph_reader(graph, chunk_evidence):
    _, refs = chunk_evidence
    e = make_entity()
    graph.put_entity(e)
    a = make_assertion(subject_identity=e.identity_id, evidence_refs=(refs[0],))
    graph.put_assertion(a)
    from datetime import UTC, datetime

    graph.mark_superseded(a.assertion_id, datetime(2026, 10, 8, tzinfo=UTC))
    index = GraphAssertionIndex(graph)
    found = index.get_assertion(a.assertion_id)
    assert found is not None and found.status.value == "SUPERSEDED"
    assert [x.assertion_id for x in index.iter_assertions()] == [a.assertion_id]


def test_supports_ref_counts_like_any_other_for_impact(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[0].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    a = make_assertion(evidence_refs=(ref,))
    svc = ProvenanceService(StaticAssertionCatalog((a,)), registry)
    assert svc.impacted_by(evs[0].evidence_id).direct == (a.assertion_id,)
