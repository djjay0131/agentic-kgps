# ADR-0004: Use native kg_contracts lookups and the ADR-0028 candidate join

Status: Accepted
Date: 2026-10-09

## Context

ADR-0002 defined KGPS's read ports and adapted `GraphReader` by scanning,
because kg_contracts had no lookup by assertion id and the evidence registry
had no reverse index. Upstream has since shipped (agentic-kgis v0.5.0/v0.6.0):

- `GraphReader.get_assertion` and `SqliteEvidenceRegistry.subjects_for`
  (ADR-0029, #59);
- typed `Evidence.span` with verified quotes (#56) and
  `Provenance.model_version` (ADR-0023);
- `Assertion.source_candidate_ids` and `superseded_by` (ADR-0028, #58).

## Decision

1. `ReaderAssertionCatalog` delegates lookups to `GraphReader.get_assertion`
   with history switches on; `catalog_for(reader)` picks it whenever the
   reader has the method. The scan (`GraphAssertionIndex`) remains only as a
   fallback and for `iter_assertions` (reverse derivation has no upstream
   index).
2. Evidence for an assertion = its own `evidence_refs` **plus** the
   candidate-keyed refs of its `source_candidate_ids` from the registry
   (`refs_for`), de-duplicated by evidence id; recovered links are marked
   `via_candidate` and, if the assertion cites nothing itself, a
   non-blocking `EVIDENCE_VIA_CANDIDATE` gap is reported.
3. `impacted_by` uses `subjects_for` to find citing candidates and maps them
   to assertions through `source_candidate_ids` (`via_candidates`).
4. Spans: typed `Evidence.span` first; locator parsing only for pre-2.2 rows,
   flagged `UNTYPED_SPAN`.
5. Supersession: the forward `superseded_by` chain is reported as
   `successors`; dangling pointers are `UNRESOLVED_SUCCESSOR` gaps.
6. Grounding counts only PRESENT `SUPPORTS`/`DERIVED_FROM` evidence
   (CONTEXTUALIZES no longer grounds), matching agentic-kgis kg_eval (#60/#62).
7. New non-blocking gaps: `NO_SOURCE_CANDIDATE`, `EVIDENCE_VIA_CANDIDATE`,
   `UNTYPED_SPAN`, `REDACTED_EVIDENCE` (via an optional `redaction()` on the
   evidence store), `UNRESOLVED_SUCCESSOR`.

The runtime floor becomes `agentic-kgis>=0.6.0` (kg_contracts 2.3.0).

## Rationale

The upstream wave existed to make these scans and string parses unnecessary;
keeping both paths (native first, fallback second) lets KGPS read older stores.

## Alternatives Considered

### Drop the scan and locator parsing entirely

Breaks explanation of pre-2.2 evidence rows and of readers without
`get_assertion`. Rejected.

## Consequences

### Positive

- Live, indexed lookups; candidate-level evidence becomes reachable.

### Negative / Tradeoffs

- `impacted_by` still scans for derivation consumers.
- The grounding rule change can flip CONTEXTUALIZES-only assertions to
  "not grounded".

### Risks

- Candidate refs reflect the registry, not the curated assertion; marked
  `via_candidate` so consumers can weigh them.
