"""Verification: does the cited evidence *entail* what was said? (wave 3; ADR-0007)

Wave 1 measured whether answers cite grounded assertions. That is necessary
but not sufficient: a sentence can cite a perfectly grounded assertion and
still say something its evidence does not support. This module adds the
missing judgement, kept pluggable and side-effect free:

* ``Verifier`` — protocol: ``check(claim, evidence_text) -> Judgement``.
  ``LexicalVerifier`` is a deterministic baseline (content-word recall plus
  exact number and negation agreement). ``LLMJudgeVerifier`` wraps any
  ``complete(prompt) -> str`` callable — KGPS ships no model client.
* ``verify_answer`` — every citation of every sentence checked against the
  cited evidence text, with the **drop-the-evidence control**: the same claim
  is judged against empty evidence, and a verifier that still says ENTAILED is
  answering from prior knowledge, so that support is not credited.
* ``VerificationScore`` — faithfulness, citation precision, minimality.
* ``propose_supports`` — DERIVED_FROM links whose evidence a verifier finds
  entailing become ``SupportsProposal`` data (and contradictions become
  contradiction proposals), shaped as KGCS ``CurationTrigger`` inputs. KGPS
  never applies them (ADR-0001); KGCS decides.

Only evidence text KGPS can see is judged: ``Evidence.content`` or the typed
span's verified ``quote``. Hash-only evidence is UNVERIFIABLE, never guessed.
"""

import json
import re
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import Evidence, EvidenceAvailability, EvidenceRelationship
from pydantic import BaseModel, ConfigDict, Field

from kgps.grounding import GroundedAnswer
from kgps.models import GROUNDING_RELATIONSHIPS, EvidenceLink
from kgps.service import ProvenanceService
from kgps.telemetry import span


class Verdict(StrEnum):
    ENTAILED = "ENTAILED"
    NOT_ENTAILED = "NOT_ENTAILED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Judgement(BaseModel):
    """One verifier decision on (claim, evidence text)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


@runtime_checkable
class Verifier(Protocol):
    name: str
    version: str

    def check(self, claim: str, evidence_text: str) -> Judgement: ...


# -- evidence text ---------------------------------------------------------------


def evidence_text(evidence: Evidence | None) -> str | None:
    """The text a verifier may judge: content, else the typed span's quote."""
    if evidence is None or evidence.availability is not EvidenceAvailability.PRESENT:
        return None
    if evidence.content:
        return evidence.content
    span_ = getattr(evidence, "span", None)
    quote = getattr(span_, "quote", None)
    return quote or None


# -- deterministic baseline ----------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z'-]*|\d+(?:\.\d+)?%?")
_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
_NEGATIONS = frozenset({"not", "no", "never", "none", "neither", "nor", "without", "cannot"})
_STOPWORDS = frozenset(
    """a an the of to in on for by with and or as at from that this these those is are was
    were be been being it its their there which who whom whose than then into over under
    only also very more most such can could may might would should will shall do does did
    has have had our we they he she you i""".split()
)


_NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


def _tokens(text: str) -> list[str]:
    """Lower-cased words and numbers; number words up to twenty become digits."""
    words = _WORD.findall(text.lower().replace("n't", " not"))
    return [_NUMBER_WORDS.get(w, w) for w in words]


def _numbers(tokens: list[str]) -> set[str]:
    return {t for t in tokens if _NUMBER.fullmatch(t)}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


class LexicalVerifier:
    """Deterministic entailment baseline (no model).

    ENTAILED when every number in the claim (digits, or number words up to
    twenty) appears in the evidence, negation
    polarity agrees, and at least ``threshold`` of the claim's content words
    (stemmed) occur in the evidence. A number or polarity mismatch on an
    otherwise-overlapping pair is CONTRADICTED. This is a floor to calibrate
    model judges against, not a substitute for them.
    """

    name = "lexical"
    version = "1"

    def __init__(self, threshold: float = 0.6) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def check(self, claim: str, evidence_text: str) -> Judgement:
        claim_tokens, ev_tokens = _tokens(claim), _tokens(evidence_text)
        if not ev_tokens:
            return Judgement(verdict=Verdict.NOT_ENTAILED, score=0.0, rationale="no evidence text")
        content = {_stem(t) for t in claim_tokens if t not in _STOPWORDS and t not in _NEGATIONS}
        if not content:
            return Judgement(
                verdict=Verdict.UNVERIFIABLE, score=0.0, rationale="claim has no content words"
            )
        ev_stems = {_stem(t) for t in ev_tokens}
        recall = len(content & ev_stems) / len(content)
        claim_numbers, ev_numbers = _numbers(claim_tokens), _numbers(ev_tokens)
        missing_numbers = claim_numbers - ev_numbers
        claim_neg = bool(_NEGATIONS.intersection(claim_tokens))
        ev_neg = bool(_NEGATIONS.intersection(ev_tokens))
        overlapping = recall >= self.threshold * 0.5
        if overlapping and missing_numbers and ev_numbers:
            return Judgement(
                verdict=Verdict.CONTRADICTED,
                score=round(recall, 4),
                rationale=f"numbers {sorted(missing_numbers)} not in evidence",
            )
        if recall >= self.threshold and claim_neg != ev_neg:
            return Judgement(
                verdict=Verdict.CONTRADICTED, score=round(recall, 4), rationale="negation mismatch"
            )
        if recall >= self.threshold and not missing_numbers:
            return Judgement(
                verdict=Verdict.ENTAILED, score=round(recall, 4),
                rationale=f"content-word recall {recall:.2f}",
            )
        why = f"content-word recall {recall:.2f}"
        if missing_numbers:
            why += f"; numbers {sorted(missing_numbers)} not in evidence"
        return Judgement(verdict=Verdict.NOT_ENTAILED, score=round(recall, 4), rationale=why)


# -- pluggable model judge -----------------------------------------------------------

JUDGE_PROMPT_VERSION = "kgps-judge/1"
JUDGE_PROMPT = """You are verifying a claim against evidence. Use ONLY the evidence.
Answer with one JSON object: {{"verdict": "ENTAILED" | "NOT_ENTAILED" | "CONTRADICTED", "score": <0..1>, "rationale": "<one sentence>"}}.

Evidence:
<<<
{evidence}
>>>

Claim:
<<<
{claim}
>>>
"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LLMJudgeVerifier:
    """A model judge behind any ``complete(prompt) -> str`` callable.

    Output that is not a JSON object with a known verdict becomes
    UNVERIFIABLE (never a silent ENTAILED); a raising ``complete`` too.
    """

    def __init__(
        self,
        complete: Callable[[str], str],
        *,
        model_id: str,
        prompt_version: str = JUDGE_PROMPT_VERSION,
        template: str = JUDGE_PROMPT,
    ) -> None:
        self._complete = complete
        self.name = f"llm-judge:{model_id}"
        self.version = prompt_version
        self._template = template

    def check(self, claim: str, evidence_text: str) -> Judgement:
        prompt = self._template.format(claim=claim, evidence=evidence_text or "(none)")
        try:
            raw = self._complete(prompt)
        except Exception as exc:  # noqa: BLE001 — a failing judge is a verdict, not a crash
            return Judgement(
                verdict=Verdict.UNVERIFIABLE, score=0.0,
                rationale=f"judge failed: {type(exc).__name__}",
            )
        match = _JSON_OBJECT.search(raw or "")
        try:
            data = json.loads(match.group(0)) if match else None
            verdict = Verdict(str(data["verdict"]).upper()) if isinstance(data, dict) else None
        except (ValueError, KeyError, TypeError):
            verdict = None
        if verdict is None or verdict is Verdict.UNVERIFIABLE or not isinstance(data, dict):
            return Judgement(
                verdict=Verdict.UNVERIFIABLE, score=0.0, rationale="unparseable judge output"
            )
        try:
            score = min(1.0, max(0.0, float(data.get("score", 1.0 if verdict is Verdict.ENTAILED else 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        return Judgement(verdict=verdict, score=score, rationale=str(data.get("rationale", ""))[:500])


# -- answer verification -------------------------------------------------------------


class CitationCheck(BaseModel):
    """One (sentence, cited evidence) judgement, with the drop-the-evidence control."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int
    assertion_id: str
    evidence_id: str | None
    judgement: Judgement
    control: Judgement | None = None

    @property
    def leaky(self) -> bool:
        """The verifier says ENTAILED even with the evidence removed."""
        return self.control is not None and self.control.verdict is Verdict.ENTAILED

    @property
    def supports(self) -> bool:
        return self.judgement.verdict is Verdict.ENTAILED and not self.leaky


class SentenceVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    text: str
    checks: tuple[CitationCheck, ...] = ()

    @property
    def cited(self) -> bool:
        return bool(self.checks)

    @property
    def supported(self) -> bool:
        return any(c.supports for c in self.checks)

    @property
    def contradicted(self) -> bool:
        return any(c.judgement.verdict is Verdict.CONTRADICTED for c in self.checks)

    @property
    def supporting_citations(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.assertion_id for c in self.checks if c.supports))


class VerificationScore(BaseModel):
    """Wave 3 answer metrics (design spec §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentences: int
    cited_sentences: int
    supported_sentences: int
    contradicted_sentences: int
    citations: int
    supporting_citations: int
    leaky_checks: int
    minimality: float = Field(ge=0.0, le=1.0)

    @property
    def faithfulness(self) -> float:
        """Share of cited sentences whose cited evidence entails them."""
        return self.supported_sentences / self.cited_sentences if self.cited_sentences else 0.0

    @property
    def citation_precision(self) -> float:
        """Share of citations that actually support their sentence."""
        return self.supporting_citations / self.citations if self.citations else 0.0


class AnswerVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    verifier: str
    verifier_version: str
    sentences: tuple[SentenceVerification, ...]
    score: VerificationScore

    @property
    def unsupported(self) -> tuple[int, ...]:
        return tuple(s.index for s in self.sentences if not s.supported)


def cited_links(
    svc: ProvenanceService, assertion_id: str, evidence_ids: Sequence[str]
) -> tuple[EvidenceLink, ...]:
    chain = svc.evidence_chain(assertion_id, with_lineage=False)
    if not chain.grounded:
        return ()
    return tuple(
        link
        for link in chain.links
        if link.relationship in GROUNDING_RELATIONSHIPS
        and (not evidence_ids or link.evidence_id in evidence_ids)
    )


def verify_answer(
    answer: GroundedAnswer,
    svc: ProvenanceService,
    verifier: Verifier,
    *,
    control: bool = True,
) -> AnswerVerification:
    """Judge every citation of every sentence against the cited evidence text.

    A citation of an ungrounded assertion, or of evidence with no visible
    text, yields an UNVERIFIABLE check (it can never support). With
    ``control`` on, each ENTAILED check is re-judged against empty evidence;
    if the verdict survives, the support is attributed to prior knowledge
    (``leaky``) and not credited.
    """
    with span("verify_answer", {"kgps.trace_id": answer.trace_id, "kgps.verifier": verifier.name}):
        sentences: list[SentenceVerification] = []
        for i, sentence in enumerate(answer.sentences):
            checks: list[CitationCheck] = []
            for citation in sentence.citations:
                links = cited_links(svc, citation.assertion_id, citation.evidence_ids)
                judged = False
                for link in links:
                    text = evidence_text(link.evidence)
                    if text is None:
                        continue
                    judged = True
                    j = verifier.check(sentence.text, text)
                    ctrl = verifier.check(sentence.text, "") if (
                        control and j.verdict is Verdict.ENTAILED
                    ) else None
                    checks.append(
                        CitationCheck(
                            sentence_index=i, assertion_id=citation.assertion_id,
                            evidence_id=link.evidence_id, judgement=j, control=ctrl,
                        )
                    )
                if not judged:
                    checks.append(
                        CitationCheck(
                            sentence_index=i, assertion_id=citation.assertion_id,
                            evidence_id=None,
                            judgement=Judgement(
                                verdict=Verdict.UNVERIFIABLE, score=0.0,
                                rationale="assertion not grounded or no evidence text visible",
                            ),
                        )
                    )
            sentences.append(SentenceVerification(index=i, text=sentence.text, checks=tuple(checks)))
        score = _score(answer, sentences)
    return AnswerVerification(
        trace_id=answer.trace_id,
        verifier=verifier.name,
        verifier_version=verifier.version,
        sentences=tuple(sentences),
        score=score,
    )


def _score(answer: GroundedAnswer, sentences: Sequence[SentenceVerification]) -> VerificationScore:
    citations = sum(len(s.citations) for s in answer.sentences)
    supporting = 0
    minimal: list[float] = []
    for s, orig in zip(sentences, answer.sentences, strict=True):
        cited_ids = tuple(dict.fromkeys(c.assertion_id for c in orig.citations))
        sup = s.supporting_citations
        supporting += sum(1 for c in orig.citations if c.assertion_id in sup)
        if s.supported and cited_ids:
            # One supporting citation suffices; every extra citation is redundancy.
            minimal.append(1.0 / len(cited_ids))
    return VerificationScore(
        sentences=len(sentences),
        cited_sentences=sum(1 for s in sentences if s.cited),
        supported_sentences=sum(1 for s in sentences if s.supported),
        contradicted_sentences=sum(1 for s in sentences if s.contradicted),
        citations=citations,
        supporting_citations=supporting,
        leaky_checks=sum(1 for s in sentences for c in s.checks if c.leaky),
        minimality=sum(minimal) / len(minimal) if minimal else 0.0,
    )


# -- SUPPORTS upgrade / contradiction proposals (data for KGCS) ----------------------


class ProposalKind(StrEnum):
    SUPPORTS_UPGRADE = "SUPPORTS_UPGRADE"
    CONTRADICTION = "CONTRADICTION"


class SupportsProposal(BaseModel):
    """A verifier finding about an (assertion, evidence) edge, for KGCS to decide.

    ``trigger_kwargs()`` returns the keyword arguments of
    ``kgcs.recuration.triggers.CurationTrigger.of`` (``NEW_EVIDENCE`` for an
    upgrade, ``CONTRADICTION_DETECTED`` for a contradiction), so the caller —
    not KGPS — can raise the trigger (ADR-0001).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ProposalKind
    assertion_id: str
    identity_id: str
    evidence_id: str
    current_relationship: EvidenceRelationship
    proposed_relationship: EvidenceRelationship
    judgement: Judgement
    verifier: str
    verifier_version: str
    statement: str
    trace_id: str = ""

    def trigger_kwargs(self) -> dict[str, object]:
        kind = "NEW_EVIDENCE" if self.kind is ProposalKind.SUPPORTS_UPGRADE else (
            "CONTRADICTION_DETECTED"
        )
        return {
            "kind": kind,
            "identity_ids": (self.identity_id,),
            "assertion_ids": (self.assertion_id,),
            "evidence_ids": (self.evidence_id,),
            "reason": (
                f"kgps {self.kind.value}: {self.verifier}@{self.verifier_version} judged "
                f"{self.judgement.verdict.value} ({self.judgement.score:.2f}): "
                f"{self.judgement.rationale}"
            )[:500],
            "trace_id": self.trace_id,
        }


def render_assertion(assertion: Assertion) -> str:
    """Default statement text: ``<subject> <predicate words> <object>``.

    Subjects are identity ids, so callers with a label lookup should pass
    their own ``render`` to ``propose_supports`` for better judgements.
    """
    obj = assertion.object_identity if assertion.object_identity is not None else assertion.object_value
    return f"{assertion.subject_identity} {assertion.predicate.replace('_', ' ')} {obj}"


def propose_supports(
    svc: ProvenanceService,
    verifier: Verifier,
    assertion_ids: Iterable[str],
    *,
    render: Callable[[Assertion], str] = render_assertion,
    trace_id: str = "",
    min_score: float = 0.0,
) -> tuple[SupportsProposal, ...]:
    """Proposals for KGCS: DERIVED_FROM → SUPPORTS where the evidence entails the
    assertion's statement; CONTRADICTS where it contradicts it. Read-only."""
    proposals: list[SupportsProposal] = []
    with span("propose_supports", {"kgps.verifier": verifier.name}):
        for aid in assertion_ids:
            chain = svc.evidence_chain(aid, with_lineage=False)
            a = chain.assertion
            if a is None:
                continue
            statement = render(a)
            for link in chain.links:
                if link.relationship is not EvidenceRelationship.DERIVED_FROM:
                    continue
                text = evidence_text(link.evidence)
                if text is None:
                    continue
                j = verifier.check(statement, text)
                if j.score < min_score:
                    continue
                if j.verdict is Verdict.ENTAILED:
                    if verifier.check(statement, "").verdict is Verdict.ENTAILED:
                        continue  # drop-the-evidence control: prior knowledge, not evidence
                    kind, proposed = ProposalKind.SUPPORTS_UPGRADE, EvidenceRelationship.SUPPORTS
                elif j.verdict is Verdict.CONTRADICTED:
                    kind, proposed = ProposalKind.CONTRADICTION, EvidenceRelationship.CONTRADICTS
                else:
                    continue
                proposals.append(
                    SupportsProposal(
                        kind=kind, assertion_id=aid, identity_id=a.subject_identity,
                        evidence_id=link.evidence_id,
                        current_relationship=link.relationship, proposed_relationship=proposed,
                        judgement=j, verifier=verifier.name, verifier_version=verifier.version,
                        statement=statement, trace_id=trace_id,
                    )
                )
    return tuple(proposals)
