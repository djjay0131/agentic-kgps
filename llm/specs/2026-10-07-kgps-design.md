# KGPS — Knowledge Graph Provenance Service (PA-AKG) — Design

Status: Draft v0.1 (design authority for this repo)
Date: 2026-10-07
Owner: Jason Cusati
Origin: PA-AKG proposal. See `amazon-vt-cfp-2026/archive/pa-akg-proposal.md`, `abstract_v2/`, and
`aws-agentic-ai-proposal/main.tex` (submitted 2026-05-13, Brown PI).

## 1. Why this exists

KGIS and KGCS already **record** provenance:

- KGIS records evidence (PRESENT, ABSENT or ERROR), the chunk span, the payload hash, and the
  actor, model and prompt.
- KGCS records audit records, executions, semantic audits with model, prompt and policy versions,
  deterministic replay, and bitemporal supersession.

Nothing **serves** that provenance. Neither has a service surface, and neither offers an
"explain", "evidence chain" or "lineage" query. No consumer (agentic-kg, baseball-ai) can attach
evidence to what it generates. The 2026-10-07 audit (`docs/origin/gap-analysis.md`) concludes that
PA-AKG is the missing **read side** of the KG stack:

```
 sources ─► KGIS (ingest)  ─► ledger + evidence registry ─┐
                                                          ├─► KGPS (provenance read side) ─► agents / apps / eval
            KGCS (curate)  ─► canonical graph + audit ────┘
```

| Service | Question it answers | Writes canonical state? |
|---|---|---|
| KGIS | "What did the sources say, and where?" | No (it writes the ledger and evidence) |
| KGCS | "What do we accept as true, and who decided?" | Yes (sole writer) |
| **KGPS** | "Why is this here, what does it rest on, what breaks if that changes, and is this generated text actually grounded?" | **No, never** (ADR-0001) |

## 2. Scope

**In scope** (the PA-AKG contributions that map onto this stack):

1. **Provenance query.** `evidence_chain`, `lineage`, `impacted_by`, `explain` over canonical
   assertions, including superseded and revoked records.
2. **Output provenance.** Typed citations from generated sentences to assertions, and a provenance
   graph running output span → assertion → evidence span → lineage.
3. **Typed provenance routing.** `ProvenanceEnvelope`, so messages between agents carry evidence
   or an explicit abstention, never bare text.
4. **Validation.** A verifier that checks cited evidence actually *entails* each output sentence,
   and the evidence → assertion `SUPPORTS` upgrade that KGCS does not do today.
5. **The provenance evaluation harness.** Fidelity, completeness, faithfulness and minimality,
   plus the B0/B1/B2 baselines from the proposal.
6. **Exposure.** A Python library first, then MCP tools and HTTP endpoints, plus OpenTelemetry
   GenAI spans and a PROV-O export.

**Out of scope:**

- Ingestion (KGIS).
- Curation decisions and canonical writes (KGCS).
- Domain reasoning (consumers).
- The retrieval index itself. KGPS defines the retrieval-routing contract, and consumers or the
  hybrid-retrieval wave plug indexes in.

## 3. Principles

1. **Read-only.** KGPS never mutates the canonical graph, the ledger or the registry (ADR-0001).
   Anything it learns that should change the graph, such as a `SUPPORTS` verdict or a
   needs-revalidation set, leaves as a *proposal*: a KGCS re-curation trigger or a candidate.
2. **Gaps are data.** Missing provenance yields a named `ProvenanceGap`, never an exception
   (ADR-0003). There are two tiers:
   - **Blocking** gaps mean the assertion is not grounded.
   - **Non-blocking** gaps mean it is grounded but weakly anchored.
3. **Narrow read ports.** KGPS depends on `kg_contracts` types and three small protocols
   (ADR-0002), so any backend can be adapted: the memory reference store, Neo4j in agentic-kg, and
   later Spanner.
4. **Explain history too.** Superseded and revoked assertions stay explainable, because answering
   "why did we believe this then?" is half the point.
5. **The text stays under the graph.** Every chain ends at evidence content or a resolvable span.
   This follows research-synthesis cross-cutting finding 1.

## 4. Provenance query (wave 1, built)

| Call | Returns | Notes |
|---|---|---|
| `evidence_chain(assertion_id)` | `EvidenceChain` (links, lineage, gaps, `grounded`) | Each `EvidenceLink` resolves `Evidence` and parses a `SourceSpan` from the KGIS locator |
| `lineage(assertion_id)` | `LineageNode`s (a DAG, as parent edges) | Breadth-first over `Derivation.inputs`. Records cycles, depth limits and unresolved inputs as gaps |
| `impacted_by(evidence_id)` | `ImpactReport(direct, transitive)` | Reverse closure, giving the re-validation set for a retraction or source revision (research gap P3) |
| `explain(assertion_id)` | `Explanation(summary, grounded, chain)` | Readable for agents and UIs |

The gap vocabulary (`GapKind`) is:

- **Blocking:** `NO_EVIDENCE_REFS`, `DANGLING_EVIDENCE_REF`, `NO_PRESENT_EVIDENCE`,
  `UNKNOWN_ASSERTION`.
- **Non-blocking:** `EVIDENCE_ABSENT`, `EVIDENCE_ERROR`, `NO_SUPPORTS_RELATIONSHIP`,
  `CONTRADICTING_EVIDENCE`, `NO_SPAN`, `HASH_ONLY_EVIDENCE`, `NOT_ACTIVE`,
  `UNRESOLVED_LINEAGE_INPUT`, `LINEAGE_CYCLE`, `LINEAGE_DEPTH_LIMIT`.

`NO_SUPPORTS_RELATIONSHIP` is deliberate. KGIS cites every passage as `DERIVED_FROM`, which says
"extracted from" and not "verified to support". KGPS reports that difference instead of hiding it.
Closing the gap is §5's verifier.

**Ports.** These are `EvidenceLookup.get`, `AssertionLookup.get_assertion` and
`AssertionCatalog.iter_assertions`. `GraphAssertionIndex` adapts any `GraphReader` by scanning it,
which is fine for tests and small graphs. Production needs a store-native index (U4).

## 5. Output provenance and validation (wave 1 contracts built; verifier is wave 3)

- `GroundedAnswer`: the question, the text, a set of `CitedSentence`s (each with a character span
  and `Citation`s to assertions, optionally narrowed to evidence ids), `produced_by`, `model` and
  `trace_id`. The model validates that each sentence span matches the answer text.
- `build_provenance_graph(answer)` produces three kinds of edge:
  - `CITES`: output span → assertion.
  - `BACKED_BY`: assertion → evidence, with the span and the relationship.
  - `DERIVED_FROM`: lineage.
- `ProvenanceEnvelope`:
  - Fields: sender and recipient `AgentRole` (supervisor, retriever, curator, validator,
    generator), `kind`, `trace_id`, `parent_trace_id`, citations, `abstained`, rationale, answer.
  - The model refuses an envelope with no citations, no answer and no abstention. This is the
    "typed, propagated" claim of the proposal, enforced in the type itself.
- **Validator (wave 3).** For each sentence and each cited evidence, an NLI or LLM judge returns
  entailed, neutral or contradicted, and records:
  - the verifier id, model and prompt version;
  - the evidence span used;
  - a *drop-the-evidence* control (the answer must change or lose support when the cited evidence
    is withheld).

  Outcomes:
  - Contradicted or neutral sentences trigger the correction loop: re-retrieve, regenerate, or
    abstain.
  - Entailed evidence for an assertion becomes a proposed `SUPPORTS` upgrade, submitted to KGCS as
    a re-curation trigger (`NEW_EVIDENCE`). KGPS never writes it directly.

## 6. Evaluation harness (wave 1 partial, wave 3 full)

Implemented now (`score_answer`):

- **citation_coverage:** the share of sentences that cite anything.
- **chain_completeness:** the share of sentences whose every citation resolves to a grounded chain.
- **span_anchoring:** the share of grounded citations that carry a character span.

Planned, per the proposal and research memo 05:

| Metric | What it measures |
|---|---|
| Fidelity | A reconstructable span → path → claim chain |
| Faithfulness | Entailment, plus the drop-the-evidence test |
| Minimality | No superfluous citations |
| Correctness | Human-judged, on a stratified sample |
| Failure recovery | Behaviour under perturbed or missing evidence (CRAG-style) |

Baselines:

- **B0:** vanilla RAG.
- **B1:** hybrid retrieval without provenance routing.
- **B2:** full PA-AKG.

Data and plumbing:

- Gold data comes from the agentic-kg ground-truth chain: 8 papers, `quoted_text`, reconciled.
- The harness extends `kg_eval` and fixes its `SUPPORTS`-only `_unsupported_assertions` (U6).
- Every number is computed, never LLM-judged without a human calibration sample. This is the
  honest-null stance.

## 7. Upstream prerequisites (issues to file in KGIS/KGCS; not KGPS code)

| # | Repo | Change | Why KGPS needs it |
|---|---|---|---|
| U1 | KGIS | A typed span on `Evidence`/`SourceCoordinates`, plus a per-item quote and span, not only chunk-level | Sentence-level faithfulness. Lets `spans.py` drop its string parsing |
| U2 | KGIS | ADR-0022 `source_version`; ADR-0023 `model_version` / `extractor_version` as readable fields | `explain` cannot report the model version today; it is only hashed into the id |
| U3 | KGIS/KGCS | An assertion → candidate link (`AuditRecord` candidate_id, or `Assertion.candidate_ids`) and a `superseded_by` pointer | Joining canonical assertions to ledger history and to KGCS audit |
| U4 | KGIS | `GraphReader.get_assertion(id)` (or a lookup port), plus a reverse evidence → subject index in the registry | Replaces the scan in `GraphAssertionIndex` and `impacted_by` |
| U5 | KGCS | A durable `AuditSink` / `SemanticAuditRecord` store; semantic audit for assertion and re-curation decisions | `explain` should include "who curated this, and why" |
| U6 | KGIS | `kg_eval._unsupported_assertions` counts only `SUPPORTS`, but producers emit only `DERIVED_FROM` | Real extraction scores 100% unsupported |
| U7 | KGCS | Adviser prompts carry evidence *text*, not only ids | Curation should check support, not just cite ids |
| U8 | KGIS | `erase()` cascades to the evidence registry | Passage text otherwise survives erasure |

## 8. Exposure and placement

- **Library** (`kgps`), now. Consumers import it alongside `kgis` and `kgcs`, as they do today.
- **MCP server** (wave 2). Tools `kg_explain`, `kg_evidence_chain`, `kg_lineage`,
  `kg_impacted_by` and `kg_score_answer`. This is how Claude Code, OpenCode on vt-arc, and the
  agents themselves use provenance.
- **HTTP** (wave 2). `GET /assertions/{id}/explain`, `/chain`, `/lineage`;
  `GET /evidence/{id}/impact`; `POST /answers/provenance`. Mounted into the agentic-kg FastAPI app
  as a router before it becomes a standalone deployment.
- **OpenTelemetry GenAI spans** (wave 2): `gen_ai.*` attributes plus `kgps.trace_id`,
  `kgps.citations`, `kgps.grounded`. A PROV-O / nanopub export of a `ProvenanceGraph` comes in
  wave 4.

### Consumers

- **agentic-kg (research.ai).** KGPS is backlog R-3 ("NL question → answer with provenance from
  graph"), the Navigator, the Synthesis write-back lineage (agent-derived problems get a
  `Derivation` with inputs, guarding against citation laundering), and the FoSE
  knowledge-accumulation case study.
  - Depends on agentic-kg's KGIS/KGCS adoption plan (2026-09-17).
  - Until that lands, an adapter over its Neo4j `Evidence` / `ProblemMention` can implement the
    ports.
- **baseball-ai.** "Why this drill / why this plan." Each generated practice-plan item cites
  drill-library assertions and coach inputs. Coach edits become lineage, not overwrites.
- **Proposals.** AutoPyDep, where dependency recommendations cite CVEs, semver constraints and
  tests. Also the construction and traffic rollouts planned in KGIS adopter notes.

## 9. Waves

| Wave | Content | Status |
|---|---|---|
| 1 | Models, ports, `ProvenanceService` (chain, lineage, impact, explain), grounding contracts, envelope, provenance graph, three answer metrics, tests against real KGIS evidence | **Built** (v0.1.0) |
| 2a | Adopt upstream contracts: native lookup, candidate join, typed spans, supersession (ADR-0004) | **Built** (v0.2.0) |
| 2b | MCP and HTTP surfaces; OpenTelemetry spans; KGCS audit join (ADR-0005, ADR-0006). The agentic-kg Neo4j adapter moved to wave 5 | **Built** (v0.3.0) |
| 3 | Verifier protocol (lexical baseline + LLM judge), drop-the-evidence control, correction loop, `SUPPORTS`/contradiction proposals for KGCS; faithfulness, citation precision and minimality (ADR-0007). The NLI model and calibration set moved to wave 5 | **Built** (v0.4.0) |
| 4 | Retrieval routing (dense, sparse, graph) with a provenance gate; B0/B1/B2 harness with perturbation; PROV-O JSON-LD export (ADR-0008). Nanopub packaging moved to wave 5 | **Built** (v0.5.0) |
| 5 | Case studies: agentic-kg R-3 (FoSE corpus) incl. Neo4j adapter and the harness on the ground-truth chain; NLI verifier + human calibration; nanopub packaging; baseball-ai plan explanations; AutoPyDep | Next |

## 10. Open questions

- Should a store-native `AssertionCatalog` live here (adapters) or in each consumer? The leaning is
  here, under `kgps.adapters.*`, as optional extras.
- Is the verifier an NLI model (cheap, deterministic) or an LLM judge on vt-arc, or both with
  disagreement routed to humans? The `tsaneva2025hil` evidence favours both. *Partly settled
  (ADR-0007): the protocol takes both; a deterministic lexical floor ships; the NLI model and
  the human-calibration set are wave 5.*
- Is the paper an evaluated end-to-end chain (P2 in `papers-to-progress-alignment.md`) or a
  separate tool paper? Decide after wave 3 data.
