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
2. **Two implementations ship.** `LexicalVerifier` (v2) is a deterministic
   baseline judged against the best-matching evidence *sentence*: content-word
   recall with light stemming, number agreement (thousands separators removed,
   number words two..twenty), negation polarity, direction clashes
   (`rose`/`fell`, `before`/`after`, `in-`/`un-` prefixes) and reversed roles
   (`A causes B` vs `B causes A`). It runs in CI and is the server default.
   `LLMJudgeVerifier` wraps any `complete(prompt) -> str` and accepts **only** a
   reply that is exactly one JSON object (optionally fenced); prose, several
   objects, non-strings, non-finite scores or a raising callable are
   `UNVERIFIABLE`, never `ENTAILED`, so evidence text echoed by the model cannot
   become the verdict. Every call goes through `safe_check`: a raising verifier
   is an `UNVERIFIABLE` check, not a crash (ADR-0003).
3. **Only visible text is judged** — `Evidence.content`, else the typed span's
   verified `quote`. Hash-only, absent and redacted evidence is `UNVERIFIABLE`.
4. **Drop-the-evidence control** — every `ENTAILED` judgement is repeated
   against empty evidence; if it survives, the check is `leaky` and does not
   count as support. (The lexical baseline can never be leaky — empty evidence
   is `NOT_ENTAILED` by construction — so the control matters for model judges.)
5. **Support and metrics.** A sentence is supported when some cited evidence
   entails it **and none of its cited evidence contradicts it** (a contradiction
   vetoes support). Faithfulness = supported / cited sentences; citation
   precision = supporting / all citations, counted **per citation** (a citation
   narrowed to non-entailing evidence earns nothing from a sibling citation of
   the same assertion); minimality = mean of 1 / distinct cited assertions over
   supported sentences; plus counts of contradicted sentences and leaky checks.
6. **Proposals are data.** `propose_supports` turns verifier findings on
   `DERIVED_FROM` links into `SUPPORTS_UPGRADE` or `CONTRADICTION` proposals
   whose `trigger_kwargs()` match `kgcs` `CurationTrigger.of`
   (`NEW_EVIDENCE` / `CONTRADICTION_DETECTED`, `kind` as a string);
   `to_kgcs_trigger()` builds the real trigger when `kgcs` is importable. The
   caller raises it; KGCS decides. Upgrades also pass the control; `min_score`
   filters upgrades only. Lexical-baseline proposals are review leads, never
   auto-applied.
7. **Correction loop** — `correct_answer` repairs unsupported sentences by
   re-citing (`retrieve` callback), then regenerating (`regenerate` callback,
   kept only if the new sentence is entailed), else abstaining; after
   `max_rounds`, anything still unsupported is removed, re-verifying until the
   answer is stable. No sentence left ⇒ the result abstains. `drop_uncited`
   (default on, strict PA-AKG) treats uncited sentences as unsupported; off
   keeps connective prose.

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

- Design spec §5, §6; ADR-0001, ADR-0003, ADR-0005; decision log D-027 … D-029

## Related Issues / PRs

- agentic-kgps#1

## Supersedes

None.

## Superseded By

None.
