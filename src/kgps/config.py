"""Build a ``ProvenanceService`` from the environment (servers; decision D-016).

Resolution order:

1. ``KGPS_SERVICE_FACTORY=package.module:callable`` — a zero-argument callable
   returning a ``ProvenanceService``. This is how a deployment wires KGPS to
   its real graph reader (Neo4j via agentic-kgcs, etc.): KGPS ships no
   database drivers of its own.
2. ``KGPS_EVIDENCE_DB`` (an agentic-kgis ``SqliteEvidenceRegistry`` file) plus
   ``KGPS_ASSERTIONS_JSONL`` (one ``Assertion`` JSON object per line) — a
   self-contained, file-backed service for exports, demos and evaluation.

``KGPS_AUDIT_DB`` optionally names an agentic-kgcs durable audit database;
``explain`` then reports which curation decision put each assertion there
(read directly, no ``kgcs`` import; decision D-019, ADR-0006).

``KGPS_MAX_DEPTH`` optionally bounds lineage/supersession walks (>= 1).

The evidence registry is opened read-only (SQLite ``mode=ro`` URI), one
connection per thread (``ReadOnlyRegistry``), so a misconfigured KGPS cannot
write canonical stores (ADR-0001) and concurrent requests cannot corrupt each
other's reads.

The environment is trusted operator input: ``KGPS_SERVICE_FACTORY`` imports
and calls whatever module it names, exactly like a WSGI/ASGI app path.
"""

import importlib
import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import Evidence, EvidenceRef, EvidenceRelationship

from kgps.ports import StaticAssertionCatalog
from kgps.service import DEFAULT_MAX_DEPTH, ProvenanceService

FACTORY_ENV = "KGPS_SERVICE_FACTORY"
EVIDENCE_DB_ENV = "KGPS_EVIDENCE_DB"
ASSERTIONS_ENV = "KGPS_ASSERTIONS_JSONL"
MAX_DEPTH_ENV = "KGPS_MAX_DEPTH"
AUDIT_DB_ENV = "KGPS_AUDIT_DB"


class ConfigError(RuntimeError):
    """The environment does not describe a usable provenance service."""


def load_assertions(path: str | Path) -> StaticAssertionCatalog:
    rows: list[Assertion] = []
    if not Path(path).is_file():
        raise ConfigError(f"assertions file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(Assertion.model_validate_json(line))
            except ValueError as exc:
                raise ConfigError(f"{path}:{lineno}: not an Assertion: {exc}") from exc
    return StaticAssertionCatalog(rows)


class ReadOnlyRegistry:
    """An agentic-kgis ``SqliteEvidenceRegistry`` per thread, each read-only.

    The servers call the service from worker threads (FastAPI's threadpool,
    MCP transports). Python's ``sqlite3`` connection is not safe to share
    across threads even with ``check_same_thread=False``: concurrent use
    mixes up statement state and silently returns wrong rows, i.e. wrong
    grounding verdicts (review of agentic-kgps#3; decision D-018). So every
    thread gets its own ``mode=ro`` connection, opened on first use.
    """

    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"evidence registry not found: {p}")
        self._uri = f"{p.resolve().as_uri()}?mode=ro"
        self._path = p
        self._local = threading.local()
        conn = sqlite3.connect(self._uri, uri=True)
        try:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ConfigError(f"{p} is not a SQLite evidence registry: {exc}") from exc
        finally:
            conn.close()
        if not has_table:
            raise ConfigError(f"{p} has no evidence table; is it an agentic-kgis registry?")
        self._registry()  # fail fast on schema-migration needs

    def _registry(self) -> Any:
        reg = getattr(self._local, "registry", None)
        if reg is None:
            from kgis.evidence.store import SqliteEvidenceRegistry

            conn = sqlite3.connect(self._uri, uri=True)
            try:
                reg = SqliteEvidenceRegistry(conn)
            except sqlite3.OperationalError as exc:
                conn.close()
                # The registry wanted to migrate its schema: KGIS's job, not ours.
                raise ConfigError(
                    f"evidence registry {self._path} needs a schema migration; "
                    "open it with agentic-kgis first"
                ) from exc
            self._local.registry = reg
        return reg

    def get(self, evidence_id: str) -> Evidence | None:
        result: Evidence | None = self._registry().get(evidence_id)
        return result

    def refs_for(
        self, subject_id: str, relationship: EvidenceRelationship | None = None
    ) -> list[EvidenceRef]:
        return list(self._registry().refs_for(subject_id, relationship))

    def subjects_for(
        self, evidence_id: str, relationship: EvidenceRelationship | None = None
    ) -> list[str]:
        return list(self._registry().subjects_for(evidence_id, relationship))

    def redaction(self, evidence_id: str) -> tuple[str | None, str | None] | None:
        result: tuple[str | None, str | None] | None = self._registry().redaction(evidence_id)
        return result


class ReadOnlyAudit:
    """Read KGCS's durable semantic audit store directly, ``mode=ro``, per thread.

    Satisfies ``kgps.ports.CurationAuditLookup`` and returns the stored record
    JSON as ``dict``s, which ``CurationDecision.from_record`` projects. It does
    not instantiate KGCS's ``SqliteSemanticAuditSink``: that is a *writer*
    whose constructor runs DDL, so any KGCS schema addition would make a
    read-only open fail (review of agentic-kgps#4; ADR-0006). The only
    coupling is the two table names and their join columns, which mirror
    ``SqliteSemanticAuditSink._join_refs``. No ``kgcs`` import is needed.
    """

    RECORDS = "semantic_audit_records"
    ASSERTION_REFS = "semantic_audit_assertions"
    _QUERY = (
        f"SELECT r.record_json FROM {RECORDS} r "
        f"JOIN {ASSERTION_REFS} ref ON ref.audit_id = r.audit_id "
        "WHERE ref.assertion_id = ? ORDER BY r.seq"
    )

    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"curation audit database not found: {p}")
        self._uri = f"{p.resolve().as_uri()}?mode=ro"
        self._local = threading.local()
        self._lock = threading.Lock()
        self._conns: list[sqlite3.Connection] = []
        conn = sqlite3.connect(self._uri, uri=True)
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        except sqlite3.DatabaseError as exc:
            raise ConfigError(f"{p} is not a readable SQLite database: {exc}") from exc
        finally:
            conn.close()
        missing = {self.RECORDS, self.ASSERTION_REFS} - tables
        if missing:
            raise ConfigError(
                f"{p} is not a KGCS semantic audit store (missing {', '.join(sorted(missing))})"
            )

    def _conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            # Used only by this thread; check_same_thread=False just lets close() run.
            conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
            self._local.conn = conn
            with self._lock:
                self._conns.append(conn)
        return conn

    def records_for_assertion(self, assertion_id: str) -> list[object]:
        rows = self._conn().execute(self._QUERY, (assertion_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            conn.close()


def open_registry_readonly(path: str | Path) -> ReadOnlyRegistry:
    return ReadOnlyRegistry(path)


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
    if depth < 1:
        raise ConfigError(f"{MAX_DEPTH_ENV} must be >= 1")
    evidence = open_registry_readonly(db)
    audit_db = env.get(AUDIT_DB_ENV)
    audit = ReadOnlyAudit(audit_db) if audit_db else None
    return ProvenanceService(
        load_assertions(assertions), evidence, audit=audit, max_depth=depth
    )
