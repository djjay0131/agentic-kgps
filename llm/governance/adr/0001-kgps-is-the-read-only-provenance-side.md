# ADR-0001: KGPS is a separate, read-only provenance service beside KGIS and KGCS

Status: Accepted
Date: 2026-10-07

## Context

PA-AKG (Provenance-Aware Agentic Knowledge Graph Systems) was proposed in
`amazon-vt-cfp-2026` (abstract_v2; AWS Agentic AI submission 2026-05-13). An
audit on 2026-10-07 found that KGIS and KGCS *record* most of the provenance
PA-AKG needs (evidence, spans at chunk level, decision audit, bitemporal
supersession) but that nothing *serves* it: neither has a query surface for
evidence chains, lineage, impact or explanation, and no consumer attaches
evidence to generated output.

## Decision

Build PA-AKG as a third service, **KGPS** (Knowledge Graph Provenance
Service), in its own repo `agentic-kgps`. KGPS reads the KGIS evidence
registry and ledger, the canonical graph, and KGCS audit records, and never
writes to any of them. Changes it discovers (a `SUPPORTS` verdict, a
re-validation set) leave as proposals: KGCS re-curation triggers or KGIS
candidates.

## Rationale

- Keeps the KGCS principle that only KGCS executors mutate canonical state.
- Provenance reading spans both upstream services; putting it in either one
  would make that service depend on the other's stores.
- Output provenance (answers, agent messages, evaluation) is consumer-facing
  and changes faster than curation; a separate release cadence fits.

## Alternatives Considered

### Add query APIs to KGIS and KGCS

Each would expose half a chain; joins (assertion → evidence → curation
decision) would still need a third place. Rejected.

### Build it inside agentic-kg

agentic-kg is one consumer; baseball-ai and the AutoPyDep case study need the
same thing. Rejected; agentic-kg mounts KGPS instead.

## Consequences

### Positive

- A single, testable read surface for "why is this here" across all consumers.
- The PA-AKG proposal maps to a concrete repo and deliverables.

### Negative / Tradeoffs

- A third repo to keep in sync with `kg_contracts`.

### Risks

- Read ports that `kg_contracts` lacks (assertion by id) are adapted by scans
  until upstream adds them (design spec §7 U4).
