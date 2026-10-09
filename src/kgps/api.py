"""Transport-neutral JSON surface over ``ProvenanceService`` (wave 2b).

The MCP server (``kgps.mcp_server``) and the HTTP router (``kgps.http``) are
thin adapters over this one class, so both expose identical payloads and
neither re-implements a query (decision D-014).

Every method returns a JSON-able ``dict``. Pydantic ``@property`` values that
callers need (``grounded``, ``blocking``, ``needs_revalidation``, the score
ratios) are not part of ``model_dump`` and are added explicitly here.

Unknown ids are not errors (ADR-0003): ``explain("nope")`` returns a payload
with ``grounded: false`` and an ``UNKNOWN_ASSERTION`` gap. Only malformed
*input* (an answer that fails validation) raises ``ValueError``.
"""

from typing import Any

from pydantic import ValidationError

from kgps.export import chain_to_prov, graph_to_prov
from kgps.grounding import GroundedAnswer, build_provenance_graph, score_answer, sentence_node
from kgps.models import EvidenceChain, ProvenanceGap
from kgps.service import ProvenanceService
from kgps.telemetry import span
from kgps.verify import LexicalVerifier, Verifier, verify_answer

JSON = dict[str, Any]


def _gaps(gaps: tuple[ProvenanceGap, ...]) -> list[JSON]:
    return [{**g.model_dump(mode="json"), "blocking": g.blocking} for g in gaps]


def chain_payload(chain: EvidenceChain) -> JSON:
    body = chain.model_dump(mode="json", exclude={"gaps"})
    body["gaps"] = _gaps(chain.gaps)
    body["grounded"] = chain.grounded
    body["source_candidate_ids"] = list(chain.source_candidate_ids)
    body["current_assertion_id"] = chain.current_assertion_id
    body["present_evidence_ids"] = [ev.evidence_id for ev in chain.present_evidence]
    for link, dumped in zip(chain.links, body["links"], strict=True):
        dumped["resolved"] = link.resolved
        dumped["model_version"] = link.model_version
    return body


def parse_answer(answer: JSON | GroundedAnswer) -> GroundedAnswer:
    if isinstance(answer, GroundedAnswer):
        return answer
    try:
        return GroundedAnswer.model_validate(answer)
    except ValidationError as exc:
        raise ValueError(f"invalid GroundedAnswer: {exc}") from exc


class ProvenanceAPI:
    """JSON payloads for every KGPS query. Read-only (ADR-0001)."""

    def __init__(self, service: ProvenanceService, verifier: Verifier | None = None) -> None:
        self.service = service
        # The server-side judge; hosts with a model pass an LLMJudgeVerifier.
        self.verifier: Verifier = verifier if verifier is not None else LexicalVerifier()

    def explain(self, assertion_id: str) -> JSON:
        exp = self.service.explain(assertion_id)
        return {
            "assertion_id": exp.assertion_id,
            "summary": exp.summary,
            "grounded": exp.grounded,
            "chain": chain_payload(exp.chain),
        }

    def evidence_chain(self, assertion_id: str, with_lineage: bool = True) -> JSON:
        return chain_payload(self.service.evidence_chain(assertion_id, with_lineage=with_lineage))

    def lineage(self, assertion_id: str) -> JSON:
        # Through evidence_chain so lookup failures surface as gaps (ADR-0003).
        with span("lineage", {"kgps.assertion_id": assertion_id}):
            chain = self.service.evidence_chain(assertion_id)
        return {
            "assertion_id": assertion_id,
            "lineage": [n.model_dump(mode="json") for n in chain.lineage],
            "gaps": _gaps(chain.gaps),
        }

    def successors(self, assertion_id: str) -> JSON:
        with span("successors", {"kgps.assertion_id": assertion_id}):
            chain = self.service.evidence_chain(assertion_id, with_lineage=False)
        return {
            "assertion_id": assertion_id,
            "successors": list(chain.successors),
            "current_assertion_id": chain.current_assertion_id,
            "gaps": _gaps(chain.gaps),
        }

    def impacted_by(self, evidence_id: str) -> JSON:
        report = self.service.impacted_by(evidence_id)
        body = report.model_dump(mode="json", exclude={"errors"})
        body["errors"] = _gaps(report.errors)
        body["needs_revalidation"] = list(report.needs_revalidation)
        return body

    def provenance_graph(self, answer: JSON | GroundedAnswer) -> JSON:
        parsed = parse_answer(answer)
        with span("provenance_graph", {"kgps.trace_id": parsed.trace_id}):
            graph = build_provenance_graph(parsed, self.service)
        return {
            "trace_id": graph.trace_id,
            "edges": [e.model_dump(mode="json") for e in graph.edges],
            "chains": {k: chain_payload(v) for k, v in graph.chains.items()},
        }

    def score_answer(self, answer: JSON | GroundedAnswer) -> JSON:
        parsed = parse_answer(answer)
        with span("score_answer", {"kgps.trace_id": parsed.trace_id}) as current:
            score = score_answer(parsed, self.service)
            if current is not None:
                current.set_attribute("kgps.chain_completeness", score.chain_completeness)
        body = score.model_dump(mode="json")
        body.update(
            trace_id=parsed.trace_id,
            citation_coverage=score.citation_coverage,
            chain_completeness=score.chain_completeness,
            span_anchoring=score.span_anchoring,
        )
        return body

    def verify_answer(self, answer: JSON | GroundedAnswer) -> JSON:
        """Wave 3: does each sentence's cited evidence entail it? (ADR-0007)"""
        parsed = parse_answer(answer)
        result = verify_answer(parsed, self.service, self.verifier)
        body = result.model_dump(mode="json")
        sc = result.score
        body["score"].update(
            faithfulness=sc.faithfulness, citation_precision=sc.citation_precision
        )
        body["unsupported"] = list(result.unsupported)
        for sent, dumped in zip(result.sentences, body["sentences"], strict=True):
            dumped["supported"] = sent.supported
            dumped["contradicted"] = sent.contradicted
            for check, d in zip(sent.checks, dumped["checks"], strict=True):
                d["supports"], d["leaky"] = check.supports, check.leaky
        return body

    def prov(self, assertion_id: str) -> JSON:
        """PROV-O JSON-LD for one assertion's chain (wave 4; no evidence text)."""
        return chain_to_prov(self.service.evidence_chain(assertion_id))

    def answer_prov(self, answer: JSON | GroundedAnswer) -> JSON:
        """PROV-O JSON-LD for an answer: output spans → assertions → evidence."""
        parsed = parse_answer(answer)
        graph = build_provenance_graph(parsed, self.service)
        texts = {sentence_node(i, s): s.text for i, s in enumerate(parsed.sentences)}
        # Text stays out by default, as for evidence (export.py); spans identify sentences.
        return graph_to_prov(
            graph, produced_by=parsed.produced_by, sentences=texts, include_quotes=False
        )
