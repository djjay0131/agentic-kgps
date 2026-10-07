# System patterns

- Read-only service over `kg_contracts` types (ADR-0001, ADR-0002).
- Gaps are data with a blocking split (ADR-0003); `grounded` = no blocking gap.
- Spans parsed from KGIS locators in one module (`kgps.spans`) until KGIS types them (U1).
- Lineage = breadth-first over `Derivation.inputs`; reverse lineage via a consumer index.
- Typed envelopes refuse ungrounded inter-agent messages.
