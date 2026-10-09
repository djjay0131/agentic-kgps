# Active context (2026-10-09)

- Waves 1–4 built and released (v0.1.0 … v0.5.0). Every PR passed an independent review
  and a fix round; all decisions are in the claude.ai project doc `claude/kgps-decision-log.md`.
- Next is wave 5 (case studies), which needs consumer adoption and owner inputs:
  - agentic-kg: Neo4j `AssertionCatalog`/`EvidenceLookup` adapter (or kg_contracts adoption),
    mount `kgps.http.create_router`, run the harness on the 8-paper ground-truth chain.
  - an NLI verifier and a human-calibration sample before any model-judged number is reported.
  - nanopub packaging (needs signing keys and a publication target).
- Known limits: the lexical verifier and extractive generator are floors; graph expansion skips
  hub identities above 50 members; `AssertionDocuments` rebuilds by full scan.
