# Changelog

## 0.2.0 — 2026-10-09

Wave 2a: adopt the upstream provenance contracts (ADR-0004). Requires
`agentic-kgis>=0.6.0` (kg_contracts 2.3.0).

### Added
- `ReaderAssertionCatalog` / `catalog_for(reader)`: native `GraphReader.get_assertion`.
- Evidence recovered through `Assertion.source_candidate_ids` and the registry's
  candidate-keyed refs (`EvidenceLink.via_candidate`); candidate nodes in lineage.
- `impacted_by` maps citing candidates (`subjects_for`) to assertions
  (`ImpactReport.via_candidates`, `citing_candidates`).
- `successors()` / `EvidenceChain.successors` / `current_assertion_id` from `superseded_by`.
- Typed `Evidence.span` preferred (`SourceSpan.typed`, `quote`); `span_for(evidence)`.
- `explain` shows source candidates, model@version, quotes and the supersession chain.
- Gaps: `NO_SOURCE_CANDIDATE`, `EVIDENCE_VIA_CANDIDATE`, `UNTYPED_SPAN`,
  `REDACTED_EVIDENCE`, `UNRESOLVED_SUCCESSOR`.

### Changed
- Grounding counts only PRESENT `SUPPORTS`/`DERIVED_FROM` evidence
  (CONTEXTUALIZES no longer grounds), matching kg_eval.

## 0.1.0 — 2026-10-07

Wave 1: provenance query, grounding contracts, answer metrics.
