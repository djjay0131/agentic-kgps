# ADR-0003: Provenance gaps are data, with a blocking / non-blocking split

Status: Accepted
Date: 2026-10-07

## Context

Real chains are often incomplete: dangling refs, ABSENT evidence, hash-only
rows, chunk-level spans, `DERIVED_FROM` without verified `SUPPORTS`. Callers
(agents, UIs, the eval harness) must still get an answer.

## Decision

Every query returns its result plus a tuple of `ProvenanceGap(kind,
subject_id, detail)`. `GapKind` is split: blocking kinds (no refs, dangling
ref, no PRESENT non-contradicting evidence, unknown assertion) make
`grounded` false; all other kinds describe weak anchoring or lineage defects
and leave `grounded` true. Queries never raise for missing provenance.

## Rationale

Mirrors the KGIS/KGCS rule "rejections and failures are data". Making the
weakness explicit (e.g. `NO_SUPPORTS_RELATIONSHIP`) is more honest than
treating `DERIVED_FROM` as support, and gives the eval harness countable
categories.

## Alternatives Considered

### A single completeness score

Loses *why* a chain is weak; not actionable.

## Consequences

### Positive

- Gap counts are metrics for free.

### Negative / Tradeoffs

- `grounded` is a policy line; it may need to become configurable per
  consumer (e.g. require SUPPORTS for high-stakes answers).

### Risks

- New gap kinds are a contract change for consumers; additive only.
