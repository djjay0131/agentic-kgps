# Governance Delta: agentic-kgps

Status: Draft (repository bootstrapped 2026-10-07; awaiting owner review)
Last updated: 2026-10-07
Governance: agentic-governance v0.9

This file localizes [agentic-governance](https://github.com/djjay0131/agentic-governance)
for this project. It was bootstrapped by hand from `agentic-kgcs`'s delta;
running `/governance:audit` against it is the first follow-up.

## Mission

KGPS (Knowledge Graph Provenance Service) is the read side of the KG stack
and the implementation of PA-AKG (Provenance-Aware Agentic Knowledge Graph
Systems). It reads what KGIS ingests and what KGCS curates and answers: why
is this assertion here, what evidence and derivations does it rest on, what
must be re-validated if a source changes, and is a generated answer actually
grounded in the graph. It never mutates canonical state.

## Design-Authority Document

`llm/specs/2026-10-07-kgps-design.md`

## Project Principles

1. Read-only over the canonical graph, the ledger and the evidence registry
   (ADR-0001). Discoveries leave as proposals to KGCS / KGIS.
2. Provenance gaps are data, never exceptions; blocking vs non-blocking is
   explicit (ADR-0003).
3. Depend on `kg_contracts` types through narrow read ports (ADR-0002).
4. Superseded and revoked records stay explainable.
5. Every chain ends at evidence content or a resolvable span; `DERIVED_FROM`
   is never silently treated as `SUPPORTS`.
6. Metrics are computed; LLM judgements are calibrated against a human sample
   before they are reported.

## Domain Review Questions

- Does any KGPS path write to the canonical graph, ledger or registry? (Must be no.)
- Does a missing or weak link surface as a named gap rather than an exception or a silent pass?
- Does the change keep `DERIVED_FROM` distinct from verified `SUPPORTS`?
- Can every inter-agent message still be refused when it carries no evidence and no abstention?
- Are new metrics computable without an uncalibrated LLM judge?

## Repository Layout

- Governance directory: `llm/governance/`
- ADR directory: `llm/governance/adr/`
- Spec directory: `llm/specs/`
- Plans directory: `llm/plans/`
- Memory-bank path: `llm/memory_bank/`
- Artifacts directory (the data plane): `docs/`

## Roadmap

Path: `llm/plans/2026-10-07-kgps-roadmap.md` (waves, mirroring design spec §9).

## Canon Location

- Canon checkout: `~/code/agentic-governance`
- Canon repository: `https://github.com/djjay0131/agentic-governance`
- Plugin registered: `repo` (`.claude/settings.json`)

## Governance Check Command

`node "${CLAUDE_PLUGIN_ROOT}/scripts/governance-checks.mjs" --layout` when the
governance plugin is loaded; from a plain shell, `plugin/scripts/governance-checks.mjs
--layout` under the `Canon checkout` above.

CI wiring: `.github/workflows/governance-checks.yml` (copied from agentic-kgcs,
pinned to agentic-governance v0.9.0).

## L0 Path Allowlist

```l0-allowlist
allow llm/memory_bank/** path-only
allow llm/governance/adr/README.md index-table-rows
allow llm/governance/adr/[0-9][0-9][0-9][0-9]-*.md status-line-only
allow llm/** link-target-only
allow docs/** link-target-only
deny src/**
deny .github/**
deny llm/governance/governance-delta.md
deny llm/governance/adr/0000-template.md
```

## Platform Enforcement Reality

Not yet verified. The repo was created 2026-10-07 with an initial commit on
`main`; branch protection, required checks and `delete_branch_on_merge` have
not been configured. Until they are (owner settings change, mirroring
agentic-kgcs: PRs required, `test` required, no force push), every rule here
is convention-only.

## Steward Activation Status

Status: INACTIVE

## Milestone Labels

- `wave-1-provenance-query`
- `wave-2-exposure`
- `wave-3-verifier`
- `wave-4-retrieval-eval`
- `wave-5-case-studies`

## Special Labels

- `upstream` (needs a KGIS/KGCS change; design spec §7)

## Constitution Adjustments

None.

## Related Repos

- `agentic-kgis` — `kg_contracts` (only dependency), evidence registry, ledger, `kg_eval`.
- `agentic-kgcs` — curation, audit records, re-curation triggers KGPS proposes into.
- `agentic-kg`, `baseball-ai` — consumers.
- `amazon-vt-cfp-2026` — PA-AKG proposal (origin).
