# Tech context

- Python >= 3.11, pydantic v2, hatchling; dev: pytest, ruff 0.16.4, mypy strict.
- Only runtime dependency: `agentic-kgis` (installed from git in CI).
- Tests use `kgis.evidence.store.SqliteEvidenceRegistry(":memory:")`,
  `kgis.extraction` chunkers/builders, and `kg_contracts.testing` factories/memory store.
