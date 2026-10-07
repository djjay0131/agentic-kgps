# Gap analysis (2026-10-07)

> Data-plane copy of the audit that led to this repo. The decisions it informed are in
> `llm/specs/2026-10-07-kgps-design.md` and ADR-0001.


**Question.** Is PA-AKG (Provenance-Aware Agentic Knowledge Graph Systems) a new piece we still have to build, or have KGIS + KGCS already become it under another name? Specifically: is provenance *tracked*, and is it *surfaced* in a form we can use?

**Answer.** Provenance is **tracked but not surfaced**. KGIS and KGCS record provenance at write time, but nothing lets you read it. PA-AKG is the missing third piece: the **read/serve side** that turns stored provenance into evidence chains, explanations and grounded answers.

- **KGIS** ingests: it produces candidates and evidence.
- **KGCS** curates: it makes decisions and keeps an audit trail.
- **Nothing** answers questions like "why is this here?" or "what supports this sentence?"

## Where PA-AKG came from

- `amazon-vt-cfp-2026/archive/pa-akg-proposal.md` and `abstract_v2/main.tex` are the original framing: a hybrid KG + retrieval system with a provenance graph and validation agents.
- `amazon-vt-cfp-2026/aws-agentic-ai-proposal/` is the latest framing, submitted 2026-05-13 with Brown as PI.
  - A supervisor agent coordinates retrieval, KG-curation and validation specialists.
  - **Typed provenance routing:** every message between agents carries an evidence chain.
  - A provenance graph links each output span to a KG path and then to the source span, with a confidence for each link.
  - Two open-source deliverables: a reference implementation and a provenance evaluation harness.
  - Metrics: provenance fidelity, claim verifiability, failure recovery. Baselines are B0, B1 and B2.
  - Case studies: AutoPyDep, and knowledge accumulation over the FoSE papers.
- `agentic-kg-rtsi-dt-proposal` does not mention provenance or PA-AKG.

## What exists today (verified in code)

| PA-AKG component | KGIS (v0.3.0) | KGCS (v2.0.0) | agentic-kg |
|---|---|---|---|
| Evidence records (PRESENT/ABSENT/ERROR) and `Provenance` (actor, model, prompt_version) | **Built.** SQLite evidence registry. | Uses evidence IDs only | Has its own model: DOI + quote + confidence, on Problems only |
| Span anchoring | **Partial.** Character offsets at chunk level, stored as a string inside `fragment`. No span or quote per extracted item. | — | `char_offset_*` fields exist but are never filled; when no quote is found, the problem statement is stored as the quote |
| Bitemporal assertions, supersession | Modelled. Memory store only. No `SUPERSEDED_BY` pointer. | **Built** (RETRACT → SUPERSEDED, records are never deleted) | — |
| `Derivation` / derived_from lineage | Modelled but never populated | One-hop dependency index | Missing. The Synthesis write-back is also broken (see below). |
| Audit of curation decisions | Append-only ledger audit | **Strong.** AuditRecord, ExecutionRecord and SemanticAuditRecord, with model, prompt and policy versions and deterministic replay. All held **in memory only**. | Agent reasoning is kept only when a case is escalated to human review |
| Verifying an assertion against its evidence *text* | — | **No.** The LLM advisers receive only opaque evidence IDs (`advisers/base.py:203-212`). | Self-check flags only |
| "Why / evidence chain / lineage" query | **No** | **No** (replay runs in-process only) | **No** |
| Answer generation with citations at output-span level | — | — | **No.** R-3 "answer with provenance" sits in the backlog as a partial spec. |
| Typed evidence in messages between agents | — | — | **No.** Agents share untyped dict state; arguments are free text. |
| Hybrid retrieval routing (dense, sparse, graph) | Capability flags only | — | Dense search plus a filter blend; no router |
| OpenTelemetry GenAI tracing / PROV-O export | No | No | No |
| Provenance evaluation harness | `kg_eval` exists, but see bug 1 below | Hook for a metrics provider only | Gold set of 8 papers with quotes |
| Service surface (HTTP, MCP) | **None.** Library only; the CLI is specified but not built. | **None.** Review CLI only. | FastAPI app, but with its own embedded KG |
| Uses KGIS/KGCS? | — | — | **No.** The adoption plan dated 2026-09-17 has not been started. |

## Verdict

PA-AKG is **not** something we have built and simply not named. About 40% of its foundation exists: the evidence model, the decision audit and bitemporal supersession. Those are the hard parts to retrofit, so it was right to build them first. What PA-AKG adds is new, and it belongs in **its own layer**:

1. **A provenance query service:** `explain(assertion)`, `evidence_chain(id)`, `lineage(id, transitive)`, `impacted_by(evidence)`. It joins the KGIS evidence registry, the canonical graph and the KGCS audit records, and is exposed over HTTP and MCP.
2. **A grounded-answer pipeline:** retrieval routing, then generation with a citation on every sentence, then a provenance graph running output span → assertion → evidence span.
3. **A validation agent:** checks that each answer sentence is entailed by its cited evidence (the "drop-the-evidence" test) and triggers re-retrieval when grounding is weak. KGCS lacks the same check on the curation side.
4. **Typed provenance messages:** a Pydantic contract carried between agents (supervisor → retriever → validator).
5. **OpenTelemetry GenAI spans**, plus a PROV-O / nanopub export.
6. **A provenance evaluation harness** measuring fidelity, completeness, faithfulness and minimality. It extends `kg_eval`.

## Prerequisite fixes in KGIS and KGCS (upstream, not part of PA-AKG)

- **KGIS:** per-item span and quote anchoring; ADR-0022 (`source_version`) and ADR-0023 (`model_version`); a link from assertion back to candidate; a `superseded_by` pointer; producers should emit `SUPPORTS`; erasure should cascade into the evidence registry.
- **KGCS:** a durable audit sink; semantic audit for assertions and re-curation, not only entity resolution; adviser prompts that include the evidence text.
- **Bug 1, in `kg_eval`:** `_unsupported_assertions` counts only `SUPPORTS` refs (`kg_eval/metrics.py:316`), but KGIS producers emit only `DERIVED_FROM`. Real extraction output would therefore score 100% unsupported.
- **Bug 2, in agentic-kg:** `SynthesisAgent` calls `create_problem(id=, statement=, status=)` and `create_relation(source_id=, target_id=)`, which don't match the real signatures (`repository.py:221`, `relations.py:66`). The resulting TypeErrors are swallowed, and the unit tests mock both methods, so nothing is ever written back.

## Where PA-AKG fits

- **agentic-kg (research.ai):** the main consumer. PA-AKG *is* backlog item R-3, "NL question → answer with provenance". It also covers the Navigator, Synthesis write-back lineage (`derived_from`) and the FoSE knowledge-accumulation case study. It depends on agentic-kg adopting KGIS/KGCS first.
- **baseball-ai:** "why this plan / why this drill" explanations. Each generated practice-plan item would trace back to coach inputs, drill-library entries and sources. This also lets coach edits be recorded as lineage.
- **Proposals:** AutoPyDep (the Brown-led case study), and the construction and traffic rollouts that KGIS already plans for.
- **Papers:** this is the third paper, the "empirical P2" in `papers-to-progress-alignment.md` §4: an evaluated end-to-end provenance chain. The research synthesis notes that no published system does this.
