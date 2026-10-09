"""B0 / B1 / B2 evaluation harness (wave 4; design spec §6; ADR-0008).

Three pipelines answer the same questions over the same graph and are scored
with the same computed metrics (no LLM-judged number without calibration —
design spec §6, honest-null stance):

* **B0** — vanilla RAG: dense retrieval, the generator cites whatever came back.
* **B1** — hybrid retrieval (sparse + dense, RRF), still no provenance gate.
* **B2** — PA-AKG: routed retrieval through ``ProvenanceRouter`` (grounded
  only, supersession followed), then ``verify_answer`` and ``correct_answer``.

Each case can be run under a **perturbation** (CRAG-style failure recovery):
``hide_gold_evidence`` hides the evidence of the gold assertions, so a good
system abstains or finds other support instead of citing an ungrounded
record.

The generator is a callable so a model can be plugged in;
``ExtractiveGenerator`` (deterministic: one sentence per retrieved item, the
best-matching evidence sentence, citing that item) makes the harness run in
CI and gives a reproducible floor.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum

from kg_contracts.evidence import Evidence, EvidenceRef, EvidenceRelationship
from pydantic import BaseModel, ConfigDict, Field

from kgps.correct import correct_answer
from kgps.grounding import Citation, CitedSentence, GroundedAnswer, score_answer
from kgps.models import GROUNDING_RELATIONSHIPS
from kgps.ports import AssertionCatalog, EvidenceLookup
from kgps.routing import (
    AssertionDocuments,
    DenseRetriever,
    GraphRetriever,
    ProvenanceRouter,
    RetrievedItem,
    SparseRetriever,
    fuse,
    tokenize,
)
from kgps.service import ProvenanceService
from kgps.verify import LexicalVerifier, Verifier, evidence_text, verify_answer


class Baseline(StrEnum):
    B0 = "B0"
    B1 = "B1"
    B2 = "B2"


class Perturbation(StrEnum):
    NONE = "none"
    HIDE_GOLD_EVIDENCE = "hide_gold_evidence"


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    question: str
    gold_assertion_ids: tuple[str, ...]


Generator = Callable[[str, Sequence[tuple[str, str]], str], GroundedAnswer]
"""(question, [(assertion_id, passage)], trace_id) -> GroundedAnswer."""

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


class ExtractiveGenerator:
    """One sentence per retrieved passage: its sentence closest to the question."""

    def __init__(self, max_sentences: int = 3) -> None:
        self.max_sentences = max_sentences

    def __call__(
        self, question: str, passages: Sequence[tuple[str, str]], trace_id: str
    ) -> GroundedAnswer:
        q = set(tokenize(question))
        chosen: list[tuple[str, str]] = []
        for aid, passage in passages:
            candidates = [s.strip() for s in _SENT.split(passage) if s.strip()]
            if not candidates:
                continue
            best = max(candidates, key=lambda s: (len(q & set(tokenize(s))), -len(s)))
            if best not in (c[1] for c in chosen):
                chosen.append((aid, best))
            if len(chosen) == self.max_sentences:
                break
        sentences: list[CitedSentence] = []
        texts: list[str] = []
        cursor = 0
        for aid, text in chosen:
            if texts:
                cursor += 1
            sentences.append(CitedSentence(
                text=text, start=cursor, end=cursor + len(text),
                citations=(Citation(assertion_id=aid),),
            ))
            texts.append(text)
            cursor += len(text)
        return GroundedAnswer(
            question=question, text=" ".join(texts), sentences=tuple(sentences),
            produced_by="extractive", trace_id=trace_id,
        )


class HiddenEvidence:
    """An ``EvidenceLookup`` that hides some evidence ids (perturbation).

    The optional registry methods are defined explicitly, not forwarded with
    ``__getattr__``: ``ProvenanceService`` detects them with runtime Protocol
    ``isinstance`` checks, which ignore ``__getattr__``, so forwarding would
    silently disable the candidate-evidence path for *every* assertion
    (review of #6). Refs still point at hidden ids (the source went away; the
    pointer did not) — ``get`` returning ``None`` is the perturbation.
    """

    def __init__(self, inner: EvidenceLookup, hidden: Iterable[str]) -> None:
        self._inner = inner
        self._hidden = frozenset(hidden)

    def get(self, evidence_id: str) -> Evidence | None:
        return None if evidence_id in self._hidden else self._inner.get(evidence_id)

    def refs_for(
        self, subject_id: str, relationship: EvidenceRelationship | None = None
    ) -> list[EvidenceRef]:
        fn = getattr(self._inner, "refs_for", None)
        return list(fn(subject_id, relationship)) if fn is not None else []

    def subjects_for(
        self, evidence_id: str, relationship: EvidenceRelationship | None = None
    ) -> list[str]:
        fn = getattr(self._inner, "subjects_for", None)
        if fn is None or evidence_id in self._hidden:
            return []
        return list(fn(evidence_id, relationship))

    def redaction(self, evidence_id: str) -> tuple[str | None, str | None] | None:
        fn = getattr(self._inner, "redaction", None)
        result: tuple[str | None, str | None] | None = fn(evidence_id) if fn is not None else None
        return result


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    baseline: Baseline
    perturbation: Perturbation
    retrieved: tuple[str, ...]
    cited: tuple[str, ...]
    recall_at_k: float
    gold_citation_precision: float
    chain_completeness: float
    faithfulness: float
    abstained: bool
    ungrounded_citations: int


class BaselineSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline: Baseline
    perturbation: Perturbation
    cases: int
    recall_at_k: float
    gold_citation_precision: float
    chain_completeness: float
    faithfulness: float
    abstention_rate: float
    ungrounded_citation_rate: float
    faithful_answer_rate: float = 0.0
    """Over *all* cases, abstentions counting 0: answered-only metrics alone
    would let a system look better by abstaining."""


class HarnessReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    k: int
    verifier: str
    generator: str
    results: tuple[CaseResult, ...]
    summaries: tuple[BaselineSummary, ...] = Field(default_factory=tuple)

    def summary(self, baseline: Baseline, perturbation: Perturbation = Perturbation.NONE) -> BaselineSummary:
        return next(
            s for s in self.summaries if s.baseline is baseline and s.perturbation is perturbation
        )

    def table(self) -> str:
        head = (
            "| baseline | perturbation | recall@k | gold-cite precision* | chain completeness* "
            "| faithfulness* | faithful (all cases) | abstention | ungrounded cites* |"
        )
        rows = [head, "|---|---|---|---|---|---|---|---|---|"]
        for s in self.summaries:
            rows.append(
                f"| {s.baseline.value} | {s.perturbation.value} | {s.recall_at_k:.2f} | "
                f"{s.gold_citation_precision:.2f} | {s.chain_completeness:.2f} | "
                f"{s.faithfulness:.2f} | {s.faithful_answer_rate:.2f} | {s.abstention_rate:.2f} | "
                f"{s.ungrounded_citation_rate:.2f} |"
            )
        rows.append("")
        rows.append("\\* over answered cases only; read with the abstention rate.")
        return "\n".join(rows)


def _passages(svc: ProvenanceService, items: Sequence[RetrievedItem], docs: AssertionDocuments) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items:
        chain = svc.evidence_chain(item.assertion_id, with_lineage=False)
        texts = [
            t for link in chain.links
            if link.relationship in GROUNDING_RELATIONSHIPS
            and (t := evidence_text(link.evidence))
        ]
        # B0/B1 have no provenance gate: an assertion with no visible evidence
        # still reaches the generator through its indexed document text.
        passage = "\n".join(texts) if texts else docs.texts.get(item.assertion_id, "")
        if passage:
            out.append((item.assertion_id, passage))
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def run_harness(
    cases: Sequence[EvalCase],
    catalog: AssertionCatalog,
    evidence: EvidenceLookup,
    *,
    k: int = 5,
    generator: Generator | None = None,
    verifier: Verifier | None = None,
    perturbations: Sequence[Perturbation] = (Perturbation.NONE, Perturbation.HIDE_GOLD_EVIDENCE),
    embed: Callable[[str], Sequence[float]] | None = None,
) -> HarnessReport:
    gen = generator or ExtractiveGenerator()
    ver = verifier or LexicalVerifier()
    results: list[CaseResult] = []
    # Indexes are built once and see the corpus as it was: the perturbation
    # happens at read time, like a source going away after indexing.
    docs = AssertionDocuments(catalog, ProvenanceService(catalog, evidence))
    sparse, dense = SparseRetriever(docs), DenseRetriever(docs, embed)
    graph = GraphRetriever(docs, sparse)
    for pert in perturbations:
        for case in cases:
            ev: EvidenceLookup = evidence
            if pert is Perturbation.HIDE_GOLD_EVIDENCE:
                base = ProvenanceService(catalog, evidence)
                hidden = {
                    link.evidence_id
                    for aid in case.gold_assertion_ids
                    for link in base.evidence_chain(aid, with_lineage=False).links
                }
                ev = HiddenEvidence(evidence, hidden)
            svc = ProvenanceService(catalog, ev)
            for b in Baseline:
                trace = f"{case.case_id}:{b.value}:{pert.value}"
                if b is Baseline.B0:
                    items = dense.retrieve(case.question, k)
                    ranked = items
                elif b is Baseline.B1:
                    items = fuse([sparse.retrieve(case.question, k * 2),
                                  dense.retrieve(case.question, k * 2)], k)
                    ranked = items
                else:
                    routed = ProvenanceRouter(svc, [sparse, dense, graph]).retrieve(
                        case.question, k
                    )
                    items = list(routed.items)
                    # Recall measures the retriever, so it is taken before the gate.
                    ranked = list(routed.candidates[:k])
                passages = _passages(svc, items, docs)
                answer: GroundedAnswer | None = gen(case.question, passages, trace)
                if b is Baseline.B2 and answer is not None:
                    answer = correct_answer(answer, svc, ver).answer
                results.append(_score_case(case, b, pert, ranked, answer, svc, ver, k))
    return HarnessReport(
        k=k, verifier=f"{ver.name}@{ver.version}", generator=type(gen).__name__,
        results=tuple(results), summaries=_summarise(results),
    )


def _score_case(
    case: EvalCase, b: Baseline, pert: Perturbation, items: Sequence[RetrievedItem],
    answer: GroundedAnswer | None, svc: ProvenanceService, ver: Verifier, k: int,
) -> CaseResult:
    gold = set(case.gold_assertion_ids)
    retrieved = tuple(i.assertion_id for i in items)
    recall = len(gold & set(retrieved[:k])) / len(gold) if gold else 0.0
    abstained = answer is None or not answer.sentences
    if abstained or answer is None:
        return CaseResult(
            case_id=case.case_id, baseline=b, perturbation=pert, retrieved=retrieved, cited=(),
            recall_at_k=recall, gold_citation_precision=0.0, chain_completeness=0.0,
            faithfulness=0.0, abstained=True, ungrounded_citations=0,
        )
    cited = tuple(dict.fromkeys(c.assertion_id for s in answer.sentences for c in s.citations))
    sc = score_answer(answer, svc)
    ungrounded = sum(
        1 for aid in cited if not svc.evidence_chain(aid, with_lineage=False).grounded
    )
    return CaseResult(
        case_id=case.case_id, baseline=b, perturbation=pert, retrieved=retrieved, cited=cited,
        recall_at_k=recall,
        gold_citation_precision=len(gold & set(cited)) / len(cited) if cited else 0.0,
        chain_completeness=sc.chain_completeness,
        faithfulness=verify_answer(answer, svc, ver).score.faithfulness,
        abstained=False, ungrounded_citations=ungrounded,
    )


def _summarise(results: Sequence[CaseResult]) -> tuple[BaselineSummary, ...]:
    out: list[BaselineSummary] = []
    keys = list(dict.fromkeys((r.baseline, r.perturbation) for r in results))
    for b, p in keys:
        rs = [r for r in results if r.baseline is b and r.perturbation is p]
        answered = [r for r in rs if not r.abstained]
        total_cites = sum(len(r.cited) for r in answered)
        out.append(BaselineSummary(
            baseline=b, perturbation=p, cases=len(rs),
            recall_at_k=_mean([r.recall_at_k for r in rs]),
            gold_citation_precision=_mean([r.gold_citation_precision for r in answered]),
            chain_completeness=_mean([r.chain_completeness for r in answered]),
            faithfulness=_mean([r.faithfulness for r in answered]),
            abstention_rate=_mean([1.0 if r.abstained else 0.0 for r in rs]),
            ungrounded_citation_rate=(
                sum(r.ungrounded_citations for r in answered) / total_cites if total_cites else 0.0
            ),
            faithful_answer_rate=_mean([0.0 if r.abstained else r.faithfulness for r in rs]),
        ))
    return tuple(out)
