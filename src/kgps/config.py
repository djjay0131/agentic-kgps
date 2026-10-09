"""Build a ``ProvenanceService`` from the environment (servers; decision D-016).

Resolution order:

1. ``KGPS_SERVICE_FACTORY=package.module:callable`` — a zero-argument callable
   returning a ``ProvenanceService``. This is how a deployment wires KGPS to
   its real graph reader (Neo4j via agentic-kgcs, etc.): KGPS ships no
   database drivers of its own.
2. ``KGPS_EVIDENCE_DB`` (an agentic-kgis ``SqliteEvidenceRegistry`` file) plus
   ``KGPS_ASSERTIONS_JSONL`` (one ``Assertion`` JSON object per line) — a
   self-contained, file-backed service for exports, demos and evaluation.

``KGPS_MAX_DEPTH`` optionally bounds lineage/supersession walks.

The evidence registry is opened read-only (SQLite ``mode=ro`` URI) so a
misconfigured KGPS cannot write canonical stores (ADR-0001).
"""

import importlib
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from kg_contracts.assertions import Assertion

from kgps.ports import StaticAssertionCatalog
from kgps.service import DEFAULT_MAX_DEPTH, ProvenanceService

FACTORY_ENV = "KGPS_SERVICE_FACTORY"
EVIDENCE_DB_ENV = "KGPS_EVIDENCE_DB"
ASSERTIONS_ENV = "KGPS_ASSERTIONS_JSONL"
MAX_DEPTH_ENV = "KGPS_MAX_DEPTH"


class ConfigError(RuntimeError):
    """The environment does not describe a usable provenance service."""


def load_assertions(path: str | Path) -> StaticAssertionCatalog:
    rows: list[Assertion] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(Assertion.model_validate_json(line))
            except ValueError as exc:
                raise ConfigError(f"{path}:{lineno}: not an Assertion: {exc}") from exc
    return StaticAssertionCatalog(rows)


def open_registry_readonly(path: str | Path):  # type: ignore[no-untyped-def]
    """An agentic-kgis ``SqliteEvidenceRegistry`` over a read-only connection."""
    from kgis.evidence.store import SqliteEvidenceRegistry

    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"evidence registry not found: {p}")
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    try:
        return SqliteEvidenceRegistry(conn)
    except sqlite3.OperationalError as exc:
        conn.close()
        # The registry wanted to migrate its schema: that is KGIS's job, not ours.
        raise ConfigError(
            f"evidence registry {p} needs a schema migration; open it with agentic-kgis first"
        ) from exc


def _factory(spec: str) -> ProvenanceService:
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ConfigError(f"{FACTORY_ENV} must be 'module:callable', got {spec!r}")
    try:
        target = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"cannot load {FACTORY_ENV}={spec!r}: {exc}") from exc
    service = target()
    if not isinstance(service, ProvenanceService):
        raise ConfigError(f"{spec} returned {type(service).__name__}, not ProvenanceService")
    return service


def service_from_env(env: Mapping[str, str] | None = None) -> ProvenanceService:
    env = os.environ if env is None else env
    if spec := env.get(FACTORY_ENV):
        return _factory(spec)
    db, assertions = env.get(EVIDENCE_DB_ENV), env.get(ASSERTIONS_ENV)
    if not db or not assertions:
        raise ConfigError(
            f"set {FACTORY_ENV}=module:callable, or both {EVIDENCE_DB_ENV} and {ASSERTIONS_ENV}"
        )
    try:
        depth = int(env.get(MAX_DEPTH_ENV, DEFAULT_MAX_DEPTH))
    except ValueError as exc:
        raise ConfigError(f"{MAX_DEPTH_ENV} must be an integer") from exc
    evidence = open_registry_readonly(db)
    return ProvenanceService(load_assertions(assertions), evidence, max_depth=depth)
