# ADR-0002: KGPS reads through three narrow protocols over kg_contracts types

Status: Accepted
Date: 2026-10-07

## Context

`kg_contracts.GraphReader` reads by identity, not by assertion id, and the
KGIS evidence registry is keyed by candidate id with no reverse index.

## Decision

KGPS defines `EvidenceLookup.get`, `AssertionLookup.get_assertion`, and
`AssertionCatalog.iter_assertions` in `kgps.ports`, using `kg_contracts`
types unchanged. `SqliteEvidenceRegistry` satisfies `EvidenceLookup` as-is;
`GraphAssertionIndex` adapts any `GraphReader` by scanning with history
switches on. Store-native adapters replace the scan per backend.

## Rationale

Depending on types, not on concrete stores, lets KGPS run over the memory
reference store, agentic-kg's Neo4j, or a future Spanner backend.

## Alternatives Considered

### Wait for kg_contracts to add get_assertion

Blocks all work on an upstream change; the port can be upstreamed later
without changing KGPS callers.

## Consequences

### Positive

- No new dependencies beyond `agentic-kgis`.

### Negative / Tradeoffs

- The scan adapter is O(graph) and snapshot-based; not for production scale.

### Risks

- Divergence if `kg_contracts` later adds an incompatible lookup; mitigated by
  proposing the port upstream (design spec §7 U4).
