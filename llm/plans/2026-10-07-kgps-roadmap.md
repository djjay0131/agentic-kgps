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
- [ ] `kgps.mcp` server (stdio) with `kg_explain`, `kg_evidence_chain`, `kg_lineage`, `kg_impacted_by`, `kg_score_answer`
- [ ] FastAPI router `kgps.http` mountable in agentic-kg
- [ ] OpenTelemetry GenAI spans (optional extra)
- [ ] KGCS audit join in `explain` (after U3/U5)
- [ ] Neo4j `AssertionCatalog`/`EvidenceLookup` adapter for agentic-kg (pre-adoption bridge)

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
