# Changelog

## 0.5.0 — 2026-10-09

Wave 4: retrieval routing, evaluation harness, PROV-O export (ADR-0008).

### Added
- `kgps.routing`: `Retriever` contract; `SparseRetriever` (BM25), `DenseRetriever`
  (any embedder; model-free `HashingEmbedder` default), `GraphRetriever` (one-hop
  derivation / supersession / shared-identity expansion); rule-based `route()`;
  reciprocal-rank `fuse()`; `ProvenanceRouter` with a provenance gate (grounded only,
  supersession followed).
- `kgps.harness`: B0 (dense RAG) / B1 (hybrid) / B2 (PA-AKG) pipelines over the same
  cases, `ExtractiveGenerator` floor, `hide_gold_evidence` perturbation, computed
  metrics (recall@k, gold-citation precision, chain completeness, faithfulness,
  abstention, ungrounded-citation rate) and a Markdown `table()`.
- `kgps.export`: W3C PROV-O JSON-LD for chains and answers (no evidence text unless
  asked); `ProvenanceAPI.prov` / `answer_prov`, MCP `kg_export_prov`, HTTP
  `GET /assertions/{id}/prov`, `POST /answers/prov`.

## 0.4.0 — 2026-10-09

Wave 3: verifier and correction (ADR-0007).

### Added
- `kgps.verify`: `Verifier` protocol; deterministic `LexicalVerifier` baseline
  (content-word recall, number and negation agreement); `LLMJudgeVerifier` over any
  `complete(prompt) -> str` callable, failing closed to `UNVERIFIABLE`.
- `verify_answer` with the drop-the-evidence control (`CitationCheck.leaky`);
  `VerificationScore`: faithfulness, citation precision, minimality.
- `propose_supports`: DERIVED_FROM → SUPPORTS upgrade and contradiction proposals as
  data, with `trigger_kwargs()` for KGCS `CurationTrigger.of` (KGPS never applies them).
- `kgps.correct.correct_answer`: re-cite / regenerate / abstain loop.
- `ProvenanceAPI.verify_answer`, MCP `kg_verify_answer`, HTTP `POST /answers/verify`.

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
