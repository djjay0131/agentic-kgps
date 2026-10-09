# ADR-0005: One JSON API behind the MCP and HTTP surfaces; optional extras

Status: Accepted
Date: 2026-10-09

## Context

Wave 2b exposes provenance to agents (MCP) and to services such as the
agentic-kg API (HTTP), and instruments queries with OpenTelemetry. The core
service must stay importable with a single dependency (`agentic-kgis`), and
the MCP Python SDK split into two incompatible lines in 2026: `mcp<2`
(`mcp.server.fastmcp.FastMCP`) and `mcp>=2` (`mcp.server.mcpserver.MCPServer`).

## Decision

1. **One serialization layer.** `kgps.api.ProvenanceAPI` turns every query into
   a JSON-able `dict`, adding the computed properties (`grounded`, gap
   `blocking`, `needs_revalidation`, score ratios) that `model_dump` omits. The
   MCP tools and HTTP routes are thin adapters; neither re-implements a query.
2. **Unknown ids are data, everywhere.** Both surfaces answer an unknown
   assertion with `grounded: false` and an `UNKNOWN_ASSERTION` gap (HTTP 200),
   per ADR-0003. Only malformed *input* is an error (HTTP 422 / MCP tool error).
3. **Optional extras.** `agentic-kgps[mcp]`, `[http]`, `[otel]`. Nothing in the
   core imports them.
4. **MCP SDK shim.** `kgps.mcp_server` imports `MCPServer` and falls back to
   `FastMCP`; both share `tool()` and `run(transport)`. Tested on mcp 1.30 and
   2.2. Seven tools: `kg_explain`, `kg_evidence_chain`, `kg_lineage`,
   `kg_successors`, `kg_impacted_by`, `kg_provenance_graph`, `kg_score_answer`.
5. **Telemetry carries identifiers and counts only.** Spans are
   `kgps.<operation>` with `kgps.*` attributes (ids, `grounded`, gap and link
   counts, blocking gap kinds). Evidence content and quotes never go to a
   telemetry backend. KGPS never configures a tracer provider.
6. **Service wiring from the environment.** `KGPS_SERVICE_FACTORY=module:callable`
   for real deployments (KGPS ships no database drivers), or
   `KGPS_EVIDENCE_DB` + `KGPS_ASSERTIONS_JSONL` for a file-backed service. The
   registry is opened through a SQLite `mode=ro` URI, so a misconfigured KGPS
   cannot write it (ADR-0001), and with `check_same_thread=False` because both
   servers call the service from worker threads.
7. **No auth in KGPS.** `kgps-http` binds `127.0.0.1` by default; deployments
   mount `create_router()` inside a host app that owns authentication.

## Rationale

A single payload definition keeps agents and services seeing the same
provenance, which is the point of PA-AKG's "typed, propagated" claim.
Supporting both MCP lines avoids pinning consumers (agentic-kg) to either.

## Alternatives Considered

### Pin `mcp<2`

Simple, but strands KGPS on a deprecated line and conflicts with consumers
that have moved to 2.x.

### HTTP 404 for unknown assertions

Conventional REST, but diverges from the MCP tools and from ADR-0003, and
loses the `STORE_ERROR` / `UNKNOWN_ASSERTION` distinction.

## Consequences

### Positive

- Agents and services get identical payloads; one test suite covers both.
- Core install footprint unchanged.

### Negative / Tradeoffs

- Payload shape is now a public contract; changes need a CHANGELOG entry.

### Risks

- A host-supplied evidence store that is not thread-safe will surface as
  `STORE_ERROR` gaps under the servers. Documented in `kgps.config`.

## Impacted Areas

- [x] AI architecture
- [x] Integrations
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `llm/specs/2026-10-07-kgps-design.md` §8 (surfaces)
- Decision log D-013 … D-017

## Related Issues / PRs

- agentic-kgps#1

## Supersedes

None.

## Superseded By

None.
