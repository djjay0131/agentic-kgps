# PA-AKG proposal lineage

> Data-plane reference. Projects no control-plane document; the design authority is
> `llm/specs/2026-10-07-kgps-design.md`.

| Date | Version | Where (repo `djjay0131/amazon-vt-cfp-2026`) | Framing |
|---|---|---|---|
| 2026-03 | v1 abstract | `abstract/main.tex` | Hybrid KG + multi-source retrieval, provenance layer, validation agents, OTel GenAI provenance signals |
| 2026-03 | v2 abstract (SOA-grounded) | `abstract_v2/main.tex`, `abstract_v2/images/architecture-diagram.png` | Provenance graph linking each output span to KG entities/relations/source passages with confidence; Bedrock KB prototype |
| 2026-03-14 | Strategy pivot | `memory-bank/strategy-pivot.md` | PA-AKG ranked #1 (with Dr. Brown) ahead of AKG-E and SVF; later archived for non-Amazon venues (`archive/pa-akg-proposal.md`) |
| 2026-05-13 | AWS Agentic AI (ARA) submission | `aws-agentic-ai-proposal/main.tex`, `construction/design/aws-agentic-ai-proposal.md` | Multi-agent supervisor + retrieval/curation/validation specialists with *typed provenance routing*; OSS = reference impl + provenance eval harness; case studies AutoPyDep and FoSE knowledge accumulation; RQ1 verifiability, RQ2 generality; baselines B0/B1/B2 |

How the proposal maps onto KGPS: see design spec §2 (scope) and §8 (placement).
The Bedrock/Neptune substrate in the AWS submission is a deployment choice;
KGPS keeps the substrate behind `kg_contracts` ports so the same code runs on the
memory store, agentic-kg's Neo4j, or Neptune.
