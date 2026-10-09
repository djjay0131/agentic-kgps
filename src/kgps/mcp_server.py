"""MCP tools for agents (wave 2b; ``pip install agentic-kgps[mcp]``).

Seven read-only tools over ``ProvenanceAPI``:

=====================  ==================================================
``kg_explain``         why is this assertion in the graph? summary + chain
``kg_evidence_chain``  resolved, span-anchored evidence with named gaps
``kg_lineage``         derivation DAG (and source-candidate edges)
``kg_successors``      forward ``superseded_by`` chain + current record
``kg_impacted_by``     assertions to re-validate if this evidence changes
``kg_provenance_graph``  answer sentence → assertion → evidence span graph
``kg_score_answer``    citation coverage / chain completeness / anchoring
=====================  ==================================================

Works with both MCP SDK lines (decision D-013): ``mcp>=2`` exposes
``mcp.server.mcpserver.MCPServer``; ``mcp<2`` exposes
``mcp.server.fastmcp.FastMCP``. Both share the ``tool()`` decorator and
``run(transport)`` used here, so the shim is just the import.

Run: ``kgps-mcp`` (stdio). The service comes from the environment
(``kgps.config.service_from_env``).
"""

import argparse
import importlib
from collections.abc import Callable
from typing import Any, cast

from kgps.api import JSON, ProvenanceAPI
from kgps.service import ProvenanceService

SERVER_NAME = "kgps"
TOOL_NAMES = (
    "kg_explain",
    "kg_evidence_chain",
    "kg_lineage",
    "kg_successors",
    "kg_impacted_by",
    "kg_provenance_graph",
    "kg_score_answer",
)

INSTRUCTIONS = (
    "Read-only provenance over the agentic knowledge graph (PA-AKG). Use kg_explain "
    "before citing an assertion; cite only assertions whose payload says grounded=true. "
    "Gaps are data: a result with blocking gaps means the assertion is not grounded."
)


def _server_class() -> Any:
    """``MCPServer`` (mcp>=2) or ``FastMCP`` (mcp<2)."""
    try:
        return importlib.import_module("mcp.server.mcpserver").MCPServer
    except (ImportError, AttributeError):
        pass
    try:
        return importlib.import_module("mcp.server.fastmcp").FastMCP
    except (ImportError, AttributeError) as exc:
        raise ImportError(
            "the MCP server needs the 'mcp' package: pip install 'agentic-kgps[mcp]'"
        ) from exc


def _read_only_annotations() -> Any:
    """``readOnlyHint`` for every tool (ADR-0001); None if the SDK lacks it."""
    try:
        types = importlib.import_module("mcp.types")
        return types.ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    except (ImportError, AttributeError, TypeError):
        return None


def build_server(service: ProvenanceService | ProvenanceAPI) -> Any:
    api = service if isinstance(service, ProvenanceAPI) else ProvenanceAPI(service)
    server = _server_class()(SERVER_NAME, instructions=INSTRUCTIONS)
    read_only = _read_only_annotations()
    decorator = cast(Callable[..., Callable[[Callable[..., JSON]], Callable[..., JSON]]], server.tool)

    def tool() -> Callable[[Callable[..., JSON]], Callable[..., JSON]]:
        return decorator(annotations=read_only) if read_only is not None else decorator()

    @tool()
    def kg_explain(assertion_id: str) -> JSON:
        """Explain why an assertion is in the knowledge graph: readable summary,
        grounded verdict, evidence (with source spans/quotes), lineage and gaps."""
        return api.explain(assertion_id)

    @tool()
    def kg_evidence_chain(assertion_id: str, with_lineage: bool = True) -> JSON:
        """The evidence chain behind an assertion, each link resolved to its source
        span, with every provenance defect named as a gap (blocking or not)."""
        return api.evidence_chain(assertion_id, with_lineage)

    @tool()
    def kg_lineage(assertion_id: str) -> JSON:
        """The derivation DAG behind an assertion (Derivation.inputs plus the
        source-candidate edges), as parent-linked nodes."""
        return api.lineage(assertion_id)

    @tool()
    def kg_successors(assertion_id: str) -> JSON:
        """The forward supersession chain of an assertion and its current record."""
        return api.successors(assertion_id)

    @tool()
    def kg_impacted_by(evidence_id: str) -> JSON:
        """Assertions whose grounding depends on this evidence (direct, via source
        candidates, and transitively through derivations): the re-validation set."""
        return api.impacted_by(evidence_id)

    @tool()
    def kg_provenance_graph(answer: dict[str, Any]) -> JSON:
        """Resolve a GroundedAnswer (question, text, sentences with citations,
        produced_by, trace_id) into output-span → assertion → evidence edges."""
        return api.provenance_graph(answer)

    @tool()
    def kg_score_answer(answer: dict[str, Any]) -> JSON:
        """Score a GroundedAnswer: citation coverage, chain completeness and span
        anchoring of its citations against the graph."""
        return api.score_answer(answer)

    return server


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - process entry point
    from kgps.config import service_from_env

    parser = argparse.ArgumentParser(prog="kgps-mcp", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--transport", choices=("stdio", "sse", "streamable-http"), default="stdio"
    )
    args = parser.parse_args(argv)
    build_server(service_from_env()).run(transport=args.transport)


if __name__ == "__main__":  # pragma: no cover
    main()
