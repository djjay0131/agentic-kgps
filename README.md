# agentic-kgps — Knowledge Graph Provenance Service (PA-AKG)

The read side of the KG stack, and the implementation of **PA-AKG**
(Provenance-Aware Agentic Knowledge Graph Systems).

```
KGIS  ingests  → candidate ledger + evidence registry ┐
KGCS  curates  → canonical graph + decision audit     ├→ KGPS explains → agents · apps · evaluation
```

KGIS and KGCS **record** provenance. KGPS **serves** it:

| Question | Call |
|---|---|
| Why is this assertion here? | `ProvenanceService.explain(assertion_id)` |
| What evidence backs it, down to the source span? | `evidence_chain(assertion_id)` |
| What was it derived from? | `lineage(assertion_id)` |
| What must be re-validated if this source changes? | `impacted_by(evidence_id)` |
| Is this generated answer grounded? | `build_provenance_graph(answer)`, `score_answer(answer)` |

Incomplete provenance is returned as named gaps (`GapKind`), never raised;
`grounded` is false only for blocking gaps (no evidence, dangling refs, nothing
PRESENT). KGPS never writes canonical state (ADR-0001).

## Quick start

```python
from kgis.evidence.store import SqliteEvidenceRegistry
from kgps import GraphAssertionIndex, ProvenanceService

svc = ProvenanceService(GraphAssertionIndex(graph_reader), SqliteEvidenceRegistry("evidence.db"))
print(svc.explain("as_01J...").summary)
```

Inter-agent messages use `ProvenanceEnvelope`, which refuses to be built
without citations, an answer, or an explicit abstention.

### Servers

```bash
pip install 'agentic-kgps[mcp,http,otel]'
export KGPS_SERVICE_FACTORY=myapp.wiring:provenance_service   # or:
export KGPS_EVIDENCE_DB=evidence.db KGPS_ASSERTIONS_JSONL=assertions.jsonl
kgps-mcp                 # MCP tools for agents (stdio)
kgps-http --port 8765    # REST; or mount kgps.http.create_router(svc) in your app
```

## Status

v0.1.0 wave 1 (provenance query, grounding contracts); v0.2.0 wave 2a
(upstream contracts adopted); v0.3.0 wave 2b (MCP, HTTP, OpenTelemetry).
Next: verifier and correction loop (wave 3), retrieval routing and
evaluation (wave 4). See
`llm/plans/2026-10-07-kgps-roadmap.md`.

## Develop

```bash
pip install "agentic-kgis @ git+https://github.com/djjay0131/agentic-kgis.git@main"
pip install -e '.[dev]'
pytest && ruff check src tests && mypy src
```

## Docs

- Design (authority): `llm/specs/2026-10-07-kgps-design.md`
- ADRs: `llm/governance/adr/`
- Origin: `docs/origin/` (PA-AKG proposal lineage, 2026-10-07 gap analysis)
