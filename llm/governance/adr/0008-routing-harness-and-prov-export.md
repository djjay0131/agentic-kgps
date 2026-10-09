# ADR-0008: Provenance-gated retrieval routing, a computed B0/B1/B2 harness, and PROV-O export

Status: Accepted
Date: 2026-10-09

## Context

Design spec §6 and the PA-AKG proposal compare three systems — vanilla RAG
(B0), hybrid retrieval without provenance routing (B1) and full PA-AKG (B2) —
on fidelity, faithfulness, minimality and failure recovery, and call for a
PROV-O export so provenance can leave the stack. The comparison must be
reproducible here without model weights, and every reported number computed.

## Decision

1. **Retriever contract** — `retrieve(query, k) -> [RetrievedItem]` with a
   `RetrievalMode`. Reference implementations index *assertion documents*
   (rendered statement + visible text of present grounding evidence): BM25
   sparse, dense over any embedder (`HashingEmbedder` default, deterministic),
   and graph (seed retriever + one hop over derivation inputs/consumers,
   supersession and shared subject/object identities).
2. **Rule-based router** — sparse for exact tokens (quotes, numbers, acronyms,
   identifiers), graph for relational/why/lineage questions, dense always;
   rankings fused by reciprocal rank (deterministic tie-break). The decision
   carries a human-readable reason.
3. **Provenance gate** (what makes B2 differ from B1) — `ProvenanceRouter`
   replaces superseded records with their current successor (only when the
   successor resolves), then drops anything not ACTIVE (revoked, rejected) or
   not grounded, before anything reaches a generator. It never raises: a
   failing or missing retriever is recorded in `errors` and the decision's
   modes are rewritten to those actually used. Graph expansion skips identity
   groups above `max_identity_group` (hub identities would be O(n²)).
4. **Harness** — the same cases through B0 (dense), B1 (sparse+dense RRF) and
   B2 (routed + gate + verify + correct), each also under a
   `hide_gold_evidence` perturbation (evidence disappears after indexing).
   Metrics are computed: recall@k (on the ranking **before** the gate, so it
   measures retrieval for every baseline), gold-citation precision, chain
   completeness, faithfulness (verifier of ADR-0007), ungrounded-citation rate —
   these over answered cases — plus abstention rate and **faithful answer rate
   over all cases** (abstention = 0), so abstaining cannot flatter a system.
   The perturbation hides evidence content (`get` → None) but keeps refs and
   the candidate path (`HiddenEvidence` implements the optional registry
   methods explicitly: runtime Protocol checks ignore `__getattr__`). Evidence
   shared with non-gold assertions disappears for them too — as when a source
   really goes away. Generators are callables; `ExtractiveGenerator` is the deterministic
   floor.
5. **PROV-O JSON-LD export** — output spans, assertions, evidence, agents
   (actors; models as `prov:SoftwareAgent` with version), qualified derivations
   with the KG relationship, lineage, supersession as `prov:wasRevisionOf`,
   KGCS decisions as activities, gaps as `KIND @ subject` literals. IRIs are
   `urn:kgps:<kind>:<id>` by default (configurable base); agents are namespaced
   by role (actor, authority, model, producer). Evidence text, quotes, answer
   text **and gap details** (which can quote store errors) are excluded unless
   `include_quotes` is set. Validated by parsing with rdflib in
   tests.

## Rationale

Deterministic references make the B0/B1/B2 gap measurable in CI today and
leave model retrievers/generators as drop-ins. The gate is the smallest
mechanism that turns "has provenance" into "uses provenance".

## Alternatives Considered

### Learned router

Better routing, but needs training data we do not have; the rule router is a
transparent baseline the harness can compare against.

### Nanopublication packaging now

Needs signing keys and a publication target (owner decisions); the PROV-O
graph is the content a nanopub would carry.

## Consequences

### Positive

- One command gives a B0/B1/B2 table; perturbation shows B2 keeps zero
  ungrounded citations while B0/B1 do not.

### Negative / Tradeoffs

- The extractive generator and hashing embedder are floors, not competitive
  systems; absolute numbers mean little until real models and the agentic-kg
  gold set are plugged in (wave 5).

### Risks

- `AssertionDocuments` reads every assertion's chain at build time; large
  graphs need an incremental index (future work).

## Impacted Areas

- [x] AI architecture
- [x] Data architecture
- [x] Integrations
- [x] Implementation

## Related Documents

- Design spec §6, §8; ADR-0001, ADR-0007; decision log D-030 … D-032

## Related Issues / PRs

- agentic-kgps#1

## Supersedes

None.

## Superseded By

None.
