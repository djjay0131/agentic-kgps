"""Correction loop: re-cite, regenerate, or abstain (wave 3; ADR-0007).

Given a ``GroundedAnswer`` and a verifier, every sentence the verifier does
not find supported by its cited evidence is repaired or removed:

1. **Re-cite** — ``retrieve(sentence) -> assertion ids`` proposes other
   canonical assertions; the first whose grounding evidence entails the
   sentence (and passes the drop-the-evidence control) replaces the citations.
2. **Regenerate** — ``regenerate(sentence, evidence_texts) -> new sentence``
   rewrites it to what the cited evidence says; kept only if the verifier
   then finds the new sentence entailed.
3. **Abstain** — otherwise the sentence is dropped. If nothing survives, the
   result abstains outright (``answer is None``), which is what a
   ``ProvenanceEnvelope`` with ``abstained=True`` should carry.

Callbacks are plain callables so KGPS stays model-agnostic and read-only.
Rounds repeat until every sentence is supported or ``max_rounds`` is hit.
"""

from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from kgps.grounding import Citation, CitedSentence, GroundedAnswer
from kgps.service import ProvenanceService
from kgps.telemetry import span
from kgps.verify import (
    AnswerVerification,
    Verdict,
    Verifier,
    cited_links,
    evidence_text,
    safe_check,
    verify_answer,
)

Retriever = Callable[[str], Sequence[str]]
Regenerator = Callable[[str, Sequence[str]], str | None]


class CorrectionAction(StrEnum):
    RECITE = "RECITE"
    REGENERATE = "REGENERATE"
    ABSTAIN = "ABSTAIN"


class SentenceCorrection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    round: int
    original_text: str
    action: CorrectionAction
    new_text: str | None = None
    new_citations: tuple[str, ...] = ()
    reason: str = ""


class CorrectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    original: GroundedAnswer
    answer: GroundedAnswer | None
    corrections: tuple[SentenceCorrection, ...]
    initial: AnswerVerification
    final: AnswerVerification | None
    rounds: int

    @property
    def abstained(self) -> bool:
        return self.answer is None

    @property
    def changed(self) -> bool:
        return bool(self.corrections)


def _entailing_texts(
    svc: ProvenanceService, verifier: Verifier, text: str, assertion_id: str
) -> bool:
    entailed = contradicted = False
    for link in cited_links(svc, assertion_id, ()):
        ev = evidence_text(link.evidence)
        if ev is None:
            continue
        verdict = safe_check(verifier, text, ev).verdict
        if verdict is Verdict.CONTRADICTED:
            contradicted = True
        elif verdict is Verdict.ENTAILED and not entailed:
            # Try the next link if this support is only prior knowledge.
            entailed = safe_check(verifier, text, "").verdict is not Verdict.ENTAILED
    return entailed and not contradicted


def _evidence_texts(svc: ProvenanceService, sentence: CitedSentence) -> list[str]:
    texts: list[str] = []
    for c in sentence.citations:
        for link in cited_links(svc, c.assertion_id, c.evidence_ids):
            ev = evidence_text(link.evidence)
            if ev is not None and ev not in texts:
                texts.append(ev)
    return texts


def _rebuild(answer: GroundedAnswer, parts: Sequence[tuple[str, tuple[Citation, ...]]]) -> GroundedAnswer | None:
    if not parts:
        return None
    sentences: list[CitedSentence] = []
    pieces: list[str] = []
    cursor = 0
    for text, citations in parts:
        if pieces:
            cursor += 1  # the joining space
        sentences.append(
            CitedSentence(text=text, start=cursor, end=cursor + len(text), citations=citations)
        )
        pieces.append(text)
        cursor += len(text)
    return answer.model_copy(
        update={"text": " ".join(pieces), "sentences": tuple(sentences)}
    )


def _to_fix(v: AnswerVerification, drop_uncited: bool) -> set[int]:
    bad = set(v.unsupported)
    if drop_uncited:
        bad |= set(v.uncited)
    return bad


def correct_answer(
    answer: GroundedAnswer,
    svc: ProvenanceService,
    verifier: Verifier,
    *,
    retrieve: Retriever | None = None,
    regenerate: Regenerator | None = None,
    max_rounds: int = 2,
    drop_uncited: bool = True,
) -> CorrectionResult:
    """Repair or remove every sentence the verifier does not find supported.

    ``drop_uncited`` (default on, PA-AKG strict mode): a sentence with no
    citation is treated like an unsupported one — re-cited if ``retrieve``
    finds entailing evidence, otherwise dropped. Turn it off to keep
    connective prose ("In summary:") that makes no factual claim.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    with span("correct_answer", {"kgps.trace_id": answer.trace_id}):
        initial = verify_answer(answer, svc, verifier)
        current: GroundedAnswer | None = answer
        verification: AnswerVerification | None = initial
        corrections: list[SentenceCorrection] = []
        rounds = 0
        while (
            current is not None
            and verification is not None
            and _to_fix(verification, drop_uncited)
            and rounds < max_rounds
        ):
            rounds += 1
            bad = _to_fix(verification, drop_uncited)
            parts: list[tuple[str, tuple[Citation, ...]]] = []
            for i, sentence in enumerate(current.sentences):
                if i not in bad:
                    parts.append((sentence.text, sentence.citations))
                    continue
                fixed = _repair(svc, verifier, sentence, retrieve, regenerate, rounds)
                corrections.append(fixed)
                if fixed.action is CorrectionAction.RECITE:
                    parts.append(
                        (sentence.text, tuple(Citation(assertion_id=a) for a in fixed.new_citations))
                    )
                elif fixed.action is CorrectionAction.REGENERATE and fixed.new_text:
                    parts.append((fixed.new_text, sentence.citations))
            current = _rebuild(answer, parts)
            verification = verify_answer(current, svc, verifier) if current is not None else None
        # Out of rounds: prune whatever is still unsupported until the answer is
        # stable (a non-deterministic judge may flip a verdict on re-check).
        while current is not None and verification is not None:
            keep_out = _to_fix(verification, drop_uncited)
            if not keep_out:
                break
            for i in sorted(keep_out):
                corrections.append(
                    SentenceCorrection(
                        round=rounds, original_text=current.sentences[i].text,
                        action=CorrectionAction.ABSTAIN,
                        reason="still unsupported after max_rounds",
                    )
                )
            current = _rebuild(
                answer,
                [(s.text, s.citations) for i, s in enumerate(current.sentences) if i not in keep_out],
            )
            verification = verify_answer(current, svc, verifier) if current is not None else None
    return CorrectionResult(
        original=answer, answer=current, corrections=tuple(corrections),
        initial=initial, final=verification if current is not None else None, rounds=rounds,
    )


def _repair(
    svc: ProvenanceService,
    verifier: Verifier,
    sentence: CitedSentence,
    retrieve: Retriever | None,
    regenerate: Regenerator | None,
    round_: int,
) -> SentenceCorrection:
    cited = {c.assertion_id for c in sentence.citations}
    if retrieve is not None:
        try:
            candidates = list(retrieve(sentence.text))
        except Exception as exc:  # noqa: BLE001 — a failing retriever falls through
            candidates = []
            retrieve_error = f"retriever failed: {type(exc).__name__}"
        else:
            retrieve_error = ""
        for aid in candidates:
            if aid in cited:
                continue
            if _entailing_texts(svc, verifier, sentence.text, aid):
                return SentenceCorrection(
                    round=round_, original_text=sentence.text, action=CorrectionAction.RECITE,
                    new_citations=(aid,), reason="re-retrieved an entailing assertion",
                )
    else:
        retrieve_error = ""
    if regenerate is not None:
        texts = _evidence_texts(svc, sentence)
        if texts:
            try:
                new = regenerate(sentence.text, texts)
            except Exception:  # noqa: BLE001
                new = None
            if new and new.strip() and new != sentence.text:
                new = new.strip()
                verdicts = [safe_check(verifier, new, ev).verdict for ev in texts]
                ok = Verdict.ENTAILED in verdicts and Verdict.CONTRADICTED not in verdicts
                if ok and safe_check(verifier, new, "").verdict is not Verdict.ENTAILED:
                    return SentenceCorrection(
                        round=round_, original_text=sentence.text,
                        action=CorrectionAction.REGENERATE, new_text=new,
                        new_citations=tuple(c.assertion_id for c in sentence.citations),
                        reason="rewritten to what the cited evidence says",
                    )
    reason = "no entailing evidence found"
    if retrieve_error:
        reason += f" ({retrieve_error})"
    return SentenceCorrection(
        round=round_, original_text=sentence.text, action=CorrectionAction.ABSTAIN, reason=reason,
    )
