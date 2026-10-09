# Changelog

## 0.3.0 — 2026-10-09

Wave 2b: exposure (ADR-0005).

### Added
- `kgps.api.ProvenanceAPI`: JSON payloads for every query, with computed
  `grounded`, gap `blocking`, `needs_revalidation` and score ratios.
- `kgps.mcp_server` (`agentic-kgps[mcp]`, `kgps-mcp`): `kg_explain`,
  `kg_evidence_chain`, `kg_lineage`, `kg_successors`, `kg_impacted_by`,
  `kg_provenance_graph`, `kg_score_answer`. Works with mcp 1.x and 2.x.
- `kgps.http` (`agentic-kgps[http]`, `kgps-http`): `create_router()` / `create_app()`.
- `kgps.telemetry` (`agentic-kgps[otel]`): `kgps.<operation>` spans.
- `kgps.config.service_from_env()`: factory or file-backed service; the
  evidence registry is opened read-only, one connection per thread.
- KGCS curation decisions in explanations (ADR-0006): `CurationAuditLookup`,
  `CurationDecision`, `EvidenceChain.decisions`, `NO_CURATION_AUDIT` gap,
  `KGPS_AUDIT_DB`.

### Changed
- `ProvenanceService.lineage()` / `successors()` no longer raise on store
  failures (empty result; use `evidence_chain()` for the gap).

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
- `grounded` ⇔ at least one PRESENT grounding evidence; `DANGLING_EVIDENCE_REF` is
  no longer blocking on its own (amends ADR-0003).
- Store read failures become `STORE_ERROR` gaps instead of exceptions.
- Native lookup only when the adapter advertises `supports_assertion_lookup`.
- Grounding counts only PRESENT `SUPPORTS`/`DERIVED_FROM` evidence
  (CONTEXTUALIZES no longer grounds), matching kg_eval.

## 0.1.0 — 2026-10-07

Wave 1: provenance query, grounding contracts, answer metrics.
