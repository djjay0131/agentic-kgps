# KGPS roadmap (waves)

Design authority: `llm/specs/2026-10-07-kgps-design.md` §9.

## Wave 1 — provenance query (done, v0.1.0)

- [x] Models: `EvidenceChain`, `EvidenceLink`, `SourceSpan`, `LineageNode`, `ImpactReport`, `Explanation`, `ProvenanceGap`/`GapKind`
- [x] Ports + `GraphAssertionIndex` / `StaticAssertionCatalog` (ADR-0002)
- [x] `ProvenanceService`: `evidence_chain`, `lineage`, `impacted_by`, `explain`
- [x] Grounding contracts: `GroundedAnswer`, `CitedSentence`, `Citation`, `ProvenanceEnvelope`, `build_provenance_graph`, `score_answer`
- [x] Tests against real KGIS chunk evidence and the `kg_contracts` memory graph (25)

## Wave 2a — adopt upstream contracts (done, v0.2.0)

- [x] Upstream U1–U8 + SYN merged; agentic-kgis v0.5.0 / v0.6.0 released
- [x] ADR-0004: native `get_assertion`, `subjects_for`, typed spans, candidate join, successors

## Wave 2b — exposure

- [x] File upstream issues U1–U8 in agentic-kgis / agentic-kgcs
- [x] `kgps.mcp_server` (stdio/SSE/streamable-http) — seven tools, mcp 1.x and 2.x (ADR-0005)
- [x] FastAPI router `kgps.http` mountable in agentic-kg (ADR-0005)
- [x] OpenTelemetry spans (optional extra; ids and counts only)
- [x] KGCS audit join in `explain` (ADR-0006)
- [ ] ~~Neo4j adapter for agentic-kg~~ → moved to wave 5 (D-020): agentic-kg wires KGPS via `KGPS_SERVICE_FACTORY` once it reads kg_contracts records

## Wave 3 — verifier and correction

- [ ] `Verifier` protocol; NLI + LLM-judge implementations; human-calibration set
- [ ] Drop-the-evidence control
- [ ] Correction loop (re-retrieve / regenerate / abstain)
- [ ] `SUPPORTS` upgrade proposals as KGCS `NEW_EVIDENCE` triggers
- [ ] Faithfulness + minimality metrics

## Wave 4 — retrieval routing and evaluation

- [ ] Retrieval-routing contract (dense / sparse / graph) + supervisor
- [ ] B0/B1/B2 harness on the agentic-kg ground-truth chain
- [ ] PROV-O / nanopub export

## Wave 5 — case studies

- [ ] agentic-kg R-3 answer-with-provenance (FoSE corpus)
- [ ] baseball-ai practice-plan explanations
- [ ] AutoPyDep dependency recommendations
