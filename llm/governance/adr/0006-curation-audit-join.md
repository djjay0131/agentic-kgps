# ADR-0006: Join KGCS curation decisions into explanations through a duck-typed port

Status: Accepted
Date: 2026-10-09

## Context

KGCS records every assertion and ER decision in a durable semantic audit
(agentic-kgcs #48/#51): deterministic baseline, adviser assessments, review,
final decision, plan id, and (since ADR-0028) source candidates. "Why is this
assertion here?" is only half-answered by evidence; the other half is which
curation decision admitted, superseded or retracted it.

## Decision

1. A fourth optional port, `CurationAuditLookup.records_for_assertion(id)`,
   matches `kgcs.persistence.sqlite.SqliteSemanticAuditSink` exactly.
2. `CurationDecision.from_record` projects any record by duck typing
   (`audit_id`, `decision_kind`, `final.kind/rationale`, `review`,
   `assessments`, `plan_id`, `recorded_at`). KGPS keeps **no dependency on the
   agentic-kgcs package** (ADR-0002); adviser prompts and replay inputs stay in
   KGCS.
3. `EvidenceChain.decisions` lists them; `explain` prints a `Curated:` line per
   decision.
4. With an audit port configured, an assertion with no recorded decision gets
   a **non-blocking** `NO_CURATION_AUDIT` gap (records written before KGCS #48
   have none). Lookup failures and unreadable records are `STORE_ERROR` gaps.
5. `KGPS_AUDIT_DB` wires a per-thread `mode=ro` KGCS sink (`ReadOnlyAudit`);
   it needs `kgcs` importable and fails fast otherwise.

## Rationale

Decision provenance completes PA-AKG's chain: output → assertion → evidence
**and** assertion → curation decision → reviewer/adviser. Duck typing keeps
the dependency graph one-directional (KGPS reads KGCS's output, never its code
in the core).

## Alternatives Considered

### Depend on agentic-kgcs

Gives typed records, but couples KGPS releases to KGCS and pulls its whole
dependency tree into every consumer.

### Read the KGCS SQLite tables directly

No import needed, but duplicates KGCS's schema knowledge in KGPS.

## Consequences

### Positive

- `explain` answers "who decided" as well as "on what evidence".

### Negative / Tradeoffs

- A KGCS record-shape change can silently drop fields from the projection;
  the real-record fixture test (`tests/fixtures/kgcs_assertion_audit.json`)
  guards the shape when kgcs is installed.

### Risks

- `NO_CURATION_AUDIT` will be common on pre-#48 data; it is non-blocking.

## Impacted Areas

- [x] Data architecture
- [x] Integrations
- [x] Implementation

## Related Documents

- ADR-0002, ADR-0003, ADR-0005; decision log D-019

## Related Issues / PRs

- agentic-kgcs #48, #51; agentic-kgps #1

## Supersedes

None.

## Superseded By

None.
