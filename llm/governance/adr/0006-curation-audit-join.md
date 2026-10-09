# ADR-0006: Join KGCS curation decisions into explanations through a duck-typed port

Status: Accepted
Date: 2026-10-09

## Context

KGCS records assertion and ER decisions in a durable semantic audit
(agentic-kgcs #48/#51): deterministic baseline, adviser assessments, review,
final decision, plan id, and (since ADR-0028) source candidates. "Why is this
assertion here?" is only half-answered by evidence; the other half is which
curation decision admitted, superseded or retracted it.

## Decision

1. A fourth optional port, `CurationAuditLookup.records_for_assertion(id)`,
   matches `kgcs.persistence.sqlite.SqliteSemanticAuditSink` exactly.
2. `CurationDecision.from_record` projects a record given as stored JSON
   (`dict`) or as a typed KGCS object, by duck typing: `audit_id`,
   `decision_kind`, `final.kind` (assertion) or `final.action` (ER),
   `rationale`, `review`, `plan_id`, `recorded_at`; `consulted_adviser` is true
   only if some assessment did not abstain. `role` is `prior` / `resulting` /
   `affected` from the record's replay inputs, so a superseded assertion and its
   replacement read differently. KGPS keeps **no dependency on the agentic-kgcs
   package** (ADR-0002); adviser prompts and replay inputs stay in KGCS.
3. `EvidenceChain.decisions` lists them; `explain` prints a `Curated:` line per
   decision with the assertion's role. Only assertion decisions are reachable:
   KGCS indexes no assertion refs for ER records.
4. With an audit port configured, an assertion with no recorded decision gets
   a **non-blocking** `NO_CURATION_AUDIT` gap (records written before KGCS #48
   have none). Lookup failures and unreadable records are `STORE_ERROR` gaps.
5. `KGPS_AUDIT_DB` wires `ReadOnlyAudit`, which **reads the two KGCS tables
   directly** (`semantic_audit_records` joined to `semantic_audit_assertions`,
   mirroring `SqliteSemanticAuditSink._join_refs`) over per-thread `mode=ro`
   connections. It does not instantiate KGCS's sink: that is a writer whose
   constructor runs DDL, so any KGCS schema addition would make a read-only
   open fail (review of #4). No `kgcs` import is needed.

## Rationale

Decision provenance completes PA-AKG's chain: output → assertion → evidence
**and** assertion → curation decision → reviewer/adviser. Duck typing keeps
the dependency graph one-directional (KGPS reads KGCS's output, never its code
in the core).

## Alternatives Considered

### Depend on agentic-kgcs

Gives typed records, but couples KGPS releases to KGCS and pulls its whole
dependency tree into every consumer.

### Open KGCS's `SqliteSemanticAuditSink` read-only

The first implementation. Rejected in review: the sink's constructor runs
`CREATE … IF NOT EXISTS` DDL and a schema-version check, so read compatibility
would depend on KGCS's writer DDL never growing.

## Consequences

### Positive

- `explain` answers "who decided" as well as "on what evidence".

### Negative / Tradeoffs

- KGPS depends on two KGCS table names and their join columns. A KGCS
  record-shape change can silently drop fields from the projection; the
  real-record fixture (`tests/fixtures/kgcs_assertion_audit.json`) and, when
  kgcs is installed, a writer-store round trip guard both.

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
