"""Wave 3: verifier, drop-the-evidence control, metrics, proposals, correction loop."""

import json

import pytest
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship, TextSpan, present_evidence
from kg_contracts.testing.factories import make_assertion

from conftest import NOW, PROV
from kgps import ProvenanceService, StaticAssertionCatalog
from kgps.correct import CorrectionAction, correct_answer
from kgps.grounding import Citation, CitedSentence, GroundedAnswer
from kgps.verify import (
    LexicalVerifier,
    LLMJudgeVerifier,
    ProposalKind,
    Verdict,
    evidence_text,
    propose_supports,
    verify_answer,
)

LEX = LexicalVerifier()


def mk(**kw):
    return make_assertion(**kw).model_copy(update={"source_candidate_ids": ("cand_x",)})


def ans(*parts, trace="tr_v"):
    """parts: (text, [assertion ids])"""
    text = " ".join(p[0] for p in parts)
    sentences, cursor = [], 0
    for t, cites in parts:
        sentences.append(CitedSentence(
            text=t, start=cursor, end=cursor + len(t),
            citations=tuple(Citation(assertion_id=a) for a in cites)))
        cursor += len(t) + 1
    return GroundedAnswer(question="q", text=text, sentences=tuple(sentences),
                          produced_by="gen", trace_id=trace)


@pytest.fixture
def kb(registry, chunk_evidence):
    evs, refs = chunk_evidence  # refs are DERIVED_FROM (KGIS extraction)
    review = mk(predicate="reduces_defects", object_value="post-release", evidence_refs=(refs[0],))
    fifteen = mk(predicate="reduces_defects_by", object_value="15%", evidence_refs=(refs[1],))
    teams = mk(predicate="holds_for", object_value="teams>5", evidence_refs=(refs[2],))
    bare = mk(predicate="made_up", object_value="x")
    svc = ProvenanceService(StaticAssertionCatalog((review, fifteen, teams, bare)), registry)
    return svc, review, fifteen, teams, bare, evs


# -- lexical baseline --------------------------------------------------------------


@pytest.mark.parametrize("claim,verdict", [
    ("Defects fell by 15% in a study of 40 projects.", Verdict.ENTAILED),
    ("Defects fell by 25% in a study of 40 projects.", Verdict.CONTRADICTED),
    ("Defects did not fall in the study of 40 projects after adoption.", Verdict.CONTRADICTED),
    ("Pair programming improves morale.", Verdict.NOT_ENTAILED),
])
def test_lexical_verifier(claim, verdict):
    ev = "In our study of 40 projects, defects fell by 15% after adoption."
    assert LEX.check(claim, ev).verdict is verdict


def test_lexical_verifier_edge_cases():
    assert LEX.check("x", "").verdict is Verdict.NOT_ENTAILED
    assert LEX.check("the of and", "evidence").verdict is Verdict.UNVERIFIABLE
    with pytest.raises(ValueError):
        LexicalVerifier(threshold=0)


def test_evidence_text_uses_content_then_quote_never_hash_only():
    quote_only = present_evidence(
        evidence_id="ev_q", source_type="paper", source_locator="doc#chars:0-5",
        observed_at=NOW, payload_hash="h", provenance=PROV,
        span=TextSpan(start=0, end=5, quote="Hello"),
    )
    hash_only = present_evidence(
        evidence_id="ev_h", source_type="table", source_locator="t#k=1",
        observed_at=NOW, payload_hash="h", provenance=PROV,
    )
    assert evidence_text(quote_only) == "Hello"
    assert evidence_text(hash_only) is None
    assert evidence_text(None) is None


# -- LLM judge -----------------------------------------------------------------------


def test_llm_judge_parses_and_fails_closed():
    seen = {}

    def complete(prompt):
        seen["prompt"] = prompt
        return 'Sure. {"verdict": "entailed", "score": 0.9, "rationale": "matches"}'

    j = LLMJudgeVerifier(complete, model_id="m1").check("claim", "evidence")
    assert j.verdict is Verdict.ENTAILED and j.score == 0.9
    assert "claim" in seen["prompt"] and "evidence" in seen["prompt"]

    for raw in ["no json here", '{"verdict": "MAYBE"}', '{"verdict": "UNVERIFIABLE"}', "[1,2]"]:
        assert LLMJudgeVerifier(lambda _p, r=raw: r, model_id="m").check("c", "e").verdict is (
            Verdict.UNVERIFIABLE
        )

    def boom(_):
        raise TimeoutError

    assert LLMJudgeVerifier(boom, model_id="m").check("c", "e").verdict is Verdict.UNVERIFIABLE
    weird = LLMJudgeVerifier(lambda _p: '{"verdict":"CONTRADICTED","score":"high"}', model_id="m")
    assert weird.check("c", "e").score == 0.0


# -- answer verification + metrics ------------------------------------------------------


def test_verify_answer_scores_faithfulness_precision_minimality(kb):
    svc, review, fifteen, teams, bare, _ = kb
    a = ans(
        ("Defects fell by 15% across 40 projects after adoption.", [fifteen.assertion_id]),
        ("The effect held only for teams larger than five developers.",
         [teams.assertion_id, review.assertion_id]),
        ("Code review doubles developer salaries.", [review.assertion_id]),
        ("Uncited remark.", []),
    )
    v = verify_answer(a, svc, LEX)
    s = v.score
    assert [x.supported for x in v.sentences] == [True, True, False, False]
    assert s.cited_sentences == 3 and s.supported_sentences == 2
    assert s.faithfulness == pytest.approx(2 / 3)
    assert s.citations == 4 and s.supporting_citations == 2
    assert s.citation_precision == 0.5
    assert s.minimality == pytest.approx((1.0 + 0.5) / 2)  # 2nd sentence cites one extra
    assert v.unsupported == (2, 3)
    json.dumps(v.model_dump(mode="json"))


def test_ungrounded_citations_are_unverifiable(kb):
    svc, *_rest = kb
    bare = _rest[3]
    v = verify_answer(ans(("Anything at all here.", [bare.assertion_id])), svc, LEX)
    (check,) = v.sentences[0].checks
    assert check.judgement.verdict is Verdict.UNVERIFIABLE and not check.supports


def test_drop_the_evidence_control_discounts_prior_knowledge(kb):
    svc, _, fifteen, *_ = kb

    class Oracle:
        """Says ENTAILED to everything — including empty evidence."""

        name, version = "oracle", "1"

        def check(self, claim, evidence_text):
            from kgps.verify import Judgement

            return Judgement(verdict=Verdict.ENTAILED, score=1.0)

    a = ans(("Defects fell by 15% across 40 projects.", [fifteen.assertion_id]))
    v = verify_answer(a, svc, Oracle())
    (check,) = v.sentences[0].checks
    assert check.leaky and not check.supports
    assert v.score.leaky_checks == 1 and v.score.faithfulness == 0.0
    # With the control off the same oracle "verifies" it: the control is what catches it.
    assert verify_answer(a, svc, Oracle(), control=False).score.faithfulness == 1.0


# -- proposals ---------------------------------------------------------------------------


def test_propose_supports_upgrades_and_contradictions(kb):
    svc, review, fifteen, teams, bare, evs = kb

    def render(a):
        return {
            fifteen.assertion_id: "defects fell by 15% in the study of 40 projects",
            teams.assertion_id: "the effect held for teams larger than nine developers 9",
            review.assertion_id: "automated code review reduces post-release defects",
        }.get(a.assertion_id, "nothing")

    props = propose_supports(
        svc, LEX, [review.assertion_id, fifteen.assertion_id, teams.assertion_id,
                   bare.assertion_id, "unknown"],
        render=render, trace_id="tr_p",
    )
    by = {p.assertion_id: p for p in props}
    assert by[fifteen.assertion_id].kind is ProposalKind.SUPPORTS_UPGRADE
    assert by[fifteen.assertion_id].proposed_relationship is EvidenceRelationship.SUPPORTS
    assert by[review.assertion_id].kind is ProposalKind.SUPPORTS_UPGRADE
    assert by[teams.assertion_id].kind is ProposalKind.CONTRADICTION
    assert bare.assertion_id not in by

    kw = by[fifteen.assertion_id].trigger_kwargs()
    assert kw["kind"] == "NEW_EVIDENCE" and kw["evidence_ids"] == (evs[1].evidence_id,)
    assert kw["assertion_ids"] == (fifteen.assertion_id,) and kw["trace_id"] == "tr_p"
    assert by[teams.assertion_id].trigger_kwargs()["kind"] == "CONTRADICTION_DETECTED"


def test_proposals_skip_links_already_supports(registry, chunk_evidence):
    evs, _ = chunk_evidence
    ref = EvidenceRef(evidence_id=evs[1].evidence_id, relationship=EvidenceRelationship.SUPPORTS)
    a = mk(evidence_refs=(ref,))
    svc = ProvenanceService(StaticAssertionCatalog((a,)), registry)
    assert propose_supports(svc, LEX, [a.assertion_id],
                            render=lambda _a: "defects fell by 15% in 40 projects") == ()


def test_proposal_trigger_kwargs_are_accepted_by_kgcs(kb):
    triggers = pytest.importorskip("kgcs.recuration.triggers")
    svc, _, fifteen, *_ = kb
    (p,) = propose_supports(svc, LEX, [fifteen.assertion_id],
                            render=lambda _a: "defects fell by 15% in the study of 40 projects")
    kw = p.trigger_kwargs()
    trig = triggers.CurationTrigger.of(**{**kw, "kind": triggers.TriggerKind(kw["kind"])})
    assert trig.evidence_ids == (p.evidence_id,)


# -- correction loop ------------------------------------------------------------------------


def test_correction_recites_regenerates_and_abstains(kb):
    svc, review, fifteen, teams, bare, _ = kb
    a = ans(
        ("Defects fell by 15% across 40 projects after adoption.", [review.assertion_id]),
        ("The effect held for teams larger than eleven developers.", [teams.assertion_id]),
        ("Code review doubles developer salaries.", [bare.assertion_id]),
    )

    def retrieve(text):
        return [review.assertion_id, fifteen.assertion_id] if "15%" in text else []

    def regenerate(text, evidence):
        return "The effect held only for teams larger than five developers." if "teams" in text else None

    r = correct_answer(a, svc, LEX, retrieve=retrieve, regenerate=regenerate)
    actions = [c.action for c in r.corrections]
    assert actions == [CorrectionAction.RECITE, CorrectionAction.REGENERATE, CorrectionAction.ABSTAIN]
    assert r.corrections[0].new_citations == (fifteen.assertion_id,)
    assert r.answer is not None and len(r.answer.sentences) == 2
    assert r.answer.text == (
        "Defects fell by 15% across 40 projects after adoption. "
        "The effect held only for teams larger than five developers."
    )
    assert r.final is not None and r.final.score.faithfulness == 1.0
    assert r.initial.score.faithfulness == 0.0
    assert not r.abstained and r.changed


def test_correction_abstains_entirely_when_nothing_survives(kb):
    svc, *_rest = kb
    bare = _rest[3]
    r = correct_answer(ans(("Made up claim entirely.", [bare.assertion_id])), svc, LEX)
    assert r.abstained and r.final is None


def test_correction_is_a_noop_on_a_faithful_answer(kb):
    svc, _, fifteen, *_ = kb
    a = ans(("Defects fell by 15% across 40 projects after adoption.", [fifteen.assertion_id]))
    r = correct_answer(a, svc, LEX)
    assert r.answer == a and not r.changed and r.rounds == 0


def test_correction_survives_failing_callbacks(kb):
    svc, review, *_ = kb

    def boom(*_a):
        raise RuntimeError("down")

    r = correct_answer(ans(("Code review doubles salaries.", [review.assertion_id])), svc, LEX,
                       retrieve=boom, regenerate=boom)
    assert r.abstained and "retriever failed" in r.corrections[0].reason
    with pytest.raises(ValueError):
        correct_answer(ans(("x y z.", [])), svc, LEX, max_rounds=0)


def test_verify_is_exposed_on_api_mcp_and_http(kb):
    from kgps.api import ProvenanceAPI

    svc, _, fifteen, *_ = kb
    a = ans(("Defects fell by 15% across 40 projects after adoption.", [fifteen.assertion_id]))
    body = ProvenanceAPI(svc).verify_answer(a.model_dump(mode="json"))
    assert body["score"]["faithfulness"] == 1.0 and body["unsupported"] == []
    assert body["sentences"][0]["checks"][0]["supports"] is True
    json.dumps(body)

    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kgps.http import create_router

    app = FastAPI()
    app.include_router(create_router(ProvenanceAPI(svc)))
    # The default in-memory registry is single-threaded; call through the API object instead
    # of the threadpool when it matters. Here only validation is exercised over HTTP.
    assert TestClient(app).post("/answers/verify", json={"question": "q"}).status_code == 422

    pytest.importorskip("mcp")
    from kgps.mcp_server import TOOL_NAMES

    assert "kg_verify_answer" in TOOL_NAMES
