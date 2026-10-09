"""HTTP surface (wave 2b; ``pip install agentic-kgps[http]``).

A FastAPI ``APIRouter`` so a host app (e.g. the agentic-kg API) can mount
provenance under its own prefix, plus ``create_app`` for standalone use.

======  ======================================  ===========================
GET     /assertions/{assertion_id}/explain      ``ProvenanceAPI.explain``
GET     /assertions/{assertion_id}/chain        ``evidence_chain``
GET     /assertions/{assertion_id}/lineage      ``lineage``
GET     /assertions/{assertion_id}/successors   ``successors``
GET     /assertions/{assertion_id}/prov         PROV-O JSON-LD (wave 4)
GET     /evidence/{evidence_id}/impact          ``impacted_by``
POST    /answers/provenance                     ``provenance_graph``
POST    /answers/score                          ``score_answer``
POST    /answers/verify                         ``verify_answer`` (wave 3)
POST    /answers/prov                           PROV-O JSON-LD for an answer
GET     /health                                 liveness + version
======  ======================================  ===========================

Unknown ids answer ``200`` with ``grounded: false`` and an
``UNKNOWN_ASSERTION`` gap, matching ADR-0003 (gaps are data) and the MCP
tools (decision D-014). POST bodies are computed on, never stored (ADR-0001);
an invalid answer is a ``422``. Ids are path parameters with the ``:path``
converter because KG ids may contain ``/`` (e.g. DOIs).
"""

from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException

from kgps.api import JSON, ProvenanceAPI
from kgps.service import ProvenanceService


def create_router(service: ProvenanceService | ProvenanceAPI) -> APIRouter:
    api = service if isinstance(service, ProvenanceAPI) else ProvenanceAPI(service)
    router = APIRouter(tags=["provenance"])

    @router.get("/assertions/{assertion_id:path}/explain")
    def explain(assertion_id: str) -> JSON:
        return api.explain(assertion_id)

    @router.get("/assertions/{assertion_id:path}/chain")
    def chain(assertion_id: str, with_lineage: bool = True) -> JSON:
        return api.evidence_chain(assertion_id, with_lineage)

    @router.get("/assertions/{assertion_id:path}/lineage")
    def lineage(assertion_id: str) -> JSON:
        return api.lineage(assertion_id)

    @router.get("/assertions/{assertion_id:path}/successors")
    def successors(assertion_id: str) -> JSON:
        return api.successors(assertion_id)

    @router.get("/assertions/{assertion_id:path}/prov")
    def prov(assertion_id: str) -> JSON:
        return api.prov(assertion_id)

    @router.get("/evidence/{evidence_id:path}/impact")
    def impact(evidence_id: str) -> JSON:
        return api.impacted_by(evidence_id)

    @router.post("/answers/provenance")
    def provenance(answer: dict[str, Any] = Body(...)) -> JSON:  # noqa: B008
        try:
            return api.provenance_graph(answer)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/answers/score")
    def score(answer: dict[str, Any] = Body(...)) -> JSON:  # noqa: B008
        try:
            return api.score_answer(answer)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/answers/verify")
    def verify(answer: dict[str, Any] = Body(...)) -> JSON:  # noqa: B008
        try:
            return api.verify_answer(answer)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/answers/prov")
    def answer_prov(answer: dict[str, Any] = Body(...)) -> JSON:  # noqa: B008
        try:
            return api.answer_prov(answer)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


def create_app(service: ProvenanceService | ProvenanceAPI | None = None) -> FastAPI:
    from kgps import __version__

    if service is None:
        from kgps.config import service_from_env

        service = service_from_env()
    app = FastAPI(title="KGPS — Knowledge Graph Provenance Service", version=__version__)
    app.include_router(create_router(service))

    @app.get("/health")
    def health() -> JSON:
        return {"status": "ok", "service": "kgps", "version": __version__}

    return app


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - process entry point
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="kgps-http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
