# ADR-0007: Pluggable verifier, drop-the-evidence control, and a callback correction loop

Status: Accepted
Date: 2026-10-09

## Context

Wave 1 scored whether answers cite grounded assertions. PA-AKG's claim is
stronger: each output sentence is *entailed* by the evidence it cites, and an
answer that is not gets corrected or withheld. That needs a judge, a guard
against the judge answering from prior knowledge, metrics, a way to feed
findings back to curation, and a repair loop — without KGPS writing canonical
state (ADR-0001) or depending on any model provider.

## Decision

1. **`Verifier` protocol** — `check(claim, evidence_text) -> Judgement`
   (`ENTAILED` / `NOT_ENTAILED` / `CONTRADICTED` / `UNVERIFIABLE`, score,
   rationale), with `name` and `version` recorded on every result.
2. **Two implementations ship.** `LexicalVerifier` is a deterministic baseline
   (content-word recall with light stemming, number agreement incl. number
   words up to twenty, negation polarity); it runs in CI and is the server
   default. `LLMJudgeVerifier` wraps any `complete(prompt) -> str`; unparseable
   or failing output is `UNVERIFIABLE`, never `ENTAILED`.
3. **Only visible text is judged** — `Evidence.content`, else the typed span's
   verified `quote`. Hash-only, absent and redacted evidence is `UNVERIFIABLE`.
4. **Drop-the-evidence control** — every `ENTAILED` judgement is repeated
   against empty evidence; if it survives, the check is `leaky` and does not
   count as support.
5. **Metrics** — faithfulness (supported / cited sentences), citation precision
   (supporting / all citations), minimality (mean of 1 / distinct citations over
   supported sentences), plus counts of contradicted sentences and leaky checks.
6. **Proposals are data.** `propose_supports` turns verifier findings on
   `DERIVED_FROM` links into `SUPPORTS_UPGRADE` or `CONTRADICTION` proposals
   whose `trigger_kwargs()` match `kgcs` `CurationTrigger.of`
   (`NEW_EVIDENCE` / `CONTRADICTION_DETECTED`). The caller raises the trigger;
   KGCS decides. Upgrades also pass the control.
7. **Correction loop** — `correct_answer` repairs unsupported sentences by
   re-citing (`retrieve` callback), then regenerating (`regenerate` callback,
   kept only if the new sentence is entailed), else abstaining; after
   `max_rounds`, anything still unsupported is removed. No sentence left ⇒ the
   result abstains.

## Rationale

Keeping judges behind a protocol lets evaluation compare a free deterministic
floor with model judges on the same harness, and keeps KGPS free of provider
SDKs. The control is cheap and catches the main failure of model judges.

## Alternatives Considered

### Bundle an NLI model

Better baseline quality, but adds a heavyweight ML dependency to a read-side
service and to CI. Deferred to the evaluation work (wave 5 / harness extras).

### Let KGPS apply SUPPORTS upgrades

Violates ADR-0001 and bypasses KGCS's audit and review.

## Consequences

### Positive

- Faithfulness is measurable today, deterministically, and model judges plug in.

### Negative / Tradeoffs

- The lexical baseline is shallow: paraphrase lowers recall (false
  NOT_ENTAILED) and shared vocabulary with a changed relation can pass. It is a
  floor for calibration, not a production judge.

### Risks

- A host that wires an LLM judge must budget two calls per entailed check (the
  control).

## Impacted Areas

- [x] AI architecture
- [x] Integrations
- [x] Implementation

## Related Documents

- Design spec §5, §6; ADR-0001, ADR-0005; decision log D-027, D-028

## Related Issues / PRs

- agentic-kgps#1

## Supersedes

None.

## Superseded By

None.
