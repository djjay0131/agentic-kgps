# Architecture Decision Records — agentic-kgps

KGPS-local decisions. System-level decisions for the KG stack live in
`agentic-kgis/llm/governance/adr/`; KGCS-local ones in
`agentic-kgcs/llm/governance/adr/`. Use `0000-template.md`.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-kgps-is-the-read-only-provenance-side.md) | KGPS is a separate, read-only provenance service beside KGIS and KGCS | Accepted |
| [0002](0002-narrow-read-ports-over-kg-contracts.md) | KGPS reads through three narrow protocols over kg_contracts types | Accepted |
| [0003](0003-provenance-gaps-are-data.md) | Provenance gaps are data, with a blocking / non-blocking split | Accepted |
| [0004](0004-adopt-native-lookups-and-candidate-join.md) | Use native kg_contracts lookups and the ADR-0028 candidate join | Accepted |
