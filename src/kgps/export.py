"""PROV-O as JSON-LD (wave 4; ADR-0008).

Exports what KGPS reconstructs — an assertion's evidence chain, or a whole
answer's ``ProvenanceGraph`` — in W3C PROV-O, so provenance can leave the KG
stack: nanopublication tooling, triple stores, RO-Crate packages, reviewers.

Mapping:

=============================  ==================================================
KGPS                           PROV-O
=============================  ==================================================
answer sentence (out span)     ``prov:Entity`` ``kgps:OutputSpan``,
                               ``prov:wasGeneratedBy`` the answer activity,
                               ``prov:wasDerivedFrom`` each cited assertion
answer                         ``prov:Activity`` ``kgps:AnswerGeneration``,
                               ``prov:wasAssociatedWith`` its producer agent
assertion                      ``prov:Entity`` ``kgps:Assertion`` (s/p/o, status)
assertion → evidence           ``prov:wasDerivedFrom`` plus a qualified
                               ``prov:Derivation`` with ``kgps:relationship``
evidence                       ``prov:Entity`` ``kgps:Evidence`` (locator, span,
                               availability), ``prov:wasAttributedTo`` its actor;
                               the model, if any, is a ``prov:SoftwareAgent``
derivation lineage             ``prov:wasDerivedFrom`` between assertions / inputs
supersession                   successor ``prov:wasRevisionOf`` predecessor
KGCS curation decision         ``prov:Activity`` ``kgps:CurationDecision``;
                               the assertion ``prov:wasGeneratedBy`` it
gaps                           ``kgps:gap`` literals on the subject
=============================  ==================================================

Evidence text and quotes are **not** exported unless ``include_quotes`` is
set: they may be licensed or personal source text (same rule as telemetry).
"""

from typing import Any
from urllib.parse import quote

from kgps.grounding import ProvenanceGraph, sentence_node
from kgps.models import EvidenceChain

PROV = "http://www.w3.org/ns/prov#"
KGPS = "https://w3id.org/kgps/ns#"
DEFAULT_BASE = "urn:kgps:"

CONTEXT: dict[str, Any] = {
    "prov": PROV,
    "kgps": KGPS,
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "wasDerivedFrom": {"@id": "prov:wasDerivedFrom", "@type": "@id", "@container": "@set"},
    "wasGeneratedBy": {"@id": "prov:wasGeneratedBy", "@type": "@id"},
    "wasAssociatedWith": {"@id": "prov:wasAssociatedWith", "@type": "@id"},
    "wasAttributedTo": {"@id": "prov:wasAttributedTo", "@type": "@id", "@container": "@set"},
    "wasRevisionOf": {"@id": "prov:wasRevisionOf", "@type": "@id"},
    "qualifiedDerivation": {"@id": "prov:qualifiedDerivation", "@container": "@set"},
    "entity": {"@id": "prov:entity", "@type": "@id"},
    "generatedAtTime": {"@id": "prov:generatedAtTime", "@type": "xsd:dateTime"},
    "startedAtTime": {"@id": "prov:startedAtTime", "@type": "xsd:dateTime"},
    "label": "http://www.w3.org/2000/01/rdf-schema#label",
}


def _iri(kind: str, local: str, base: str) -> str:
    return f"{base}{kind}:{quote(local, safe='')}"


class _Doc:
    def __init__(self, base: str, include_quotes: bool) -> None:
        self.base = base
        self.include_quotes = include_quotes
        self.nodes: dict[str, dict[str, Any]] = {}

    def node(self, iri: str, *types: str) -> dict[str, Any]:
        n = self.nodes.setdefault(iri, {"@id": iri, "@type": []})
        for t in types:
            if t not in n["@type"]:
                n["@type"].append(t)
        return n

    def add(self, iri: str, key: str, value: Any) -> None:
        n = self.nodes[iri]
        if key in ("wasDerivedFrom", "wasAttributedTo", "qualifiedDerivation", "kgps:gap"):
            bucket = n.setdefault(key, [])
            if value not in bucket:
                bucket.append(value)
        else:
            n[key] = value

    def agent(self, name: str, software: bool = False) -> str:
        iri = _iri("agent", name, self.base)
        self.node(iri, "prov:Agent", *(["prov:SoftwareAgent"] if software else []))
        self.add(iri, "label", name)
        return iri

    def chain(self, chain: EvidenceChain) -> str:
        a_iri = _iri("assertion", chain.assertion_id, self.base)
        node = self.node(a_iri, "prov:Entity", "kgps:Assertion")
        a = chain.assertion
        if a is not None:
            node["kgps:subject"] = a.subject_identity
            node["kgps:predicate"] = a.predicate
            if a.object_identity is not None:
                node["kgps:object"] = {"@id": a.object_identity} if ":" in a.object_identity else a.object_identity
            else:
                node["kgps:objectValue"] = str(a.object_value)
            node["kgps:status"] = a.status.value
            node["kgps:curationEpoch"] = a.curation_epoch
            node["generatedAtTime"] = a.recorded_at.isoformat()
            if a.authority:
                self.add(a_iri, "wasAttributedTo", self.agent(a.authority))
        node["kgps:grounded"] = chain.grounded
        for gap in chain.gaps:
            self.add(a_iri, "kgps:gap", f"{gap.kind.value}: {gap.detail}")
        for link in chain.links:
            e_iri = _iri("evidence", link.evidence_id, self.base)
            self.add(a_iri, "wasDerivedFrom", e_iri)
            q = {
                "@type": "prov:Derivation",
                "entity": e_iri,
                "kgps:relationship": link.relationship.value,
            }
            if link.via_candidate:
                q["kgps:viaCandidate"] = link.via_candidate
            self.add(a_iri, "qualifiedDerivation", q)
            ev_node = self.node(e_iri, "prov:Entity", "kgps:Evidence")
            ev = link.evidence
            if ev is None:
                ev_node["kgps:resolved"] = False
                continue
            ev_node["kgps:sourceType"] = ev.source_type
            ev_node["kgps:sourceLocator"] = ev.source_locator
            ev_node["kgps:availability"] = ev.availability.value
            ev_node["generatedAtTime"] = ev.observed_at.isoformat()
            if ev.payload_hash:
                ev_node["kgps:payloadHash"] = ev.payload_hash
            if link.span is not None:
                ev_node["kgps:spanStart"] = link.span.start
                ev_node["kgps:spanEnd"] = link.span.end
                if self.include_quotes and link.span.quote:
                    ev_node["kgps:quote"] = link.span.quote
            if self.include_quotes and ev.content:
                ev_node["prov:value"] = ev.content
            self.add(e_iri, "wasAttributedTo", self.agent(ev.provenance.actor))
            if ev.provenance.model:
                model = ev.provenance.model
                if link.model_version:
                    model += f"@{link.model_version}"
                self.add(e_iri, "wasAttributedTo", self.agent(model, software=True))
        for n in chain.lineage:
            if n.parent_ref is None:
                continue
            parent = (
                a_iri if n.parent_ref == chain.assertion_id
                else _iri("assertion", n.parent_ref, self.base)
            )
            target = _iri(n.kind, n.ref, self.base)
            self.node(target, "prov:Entity", f"kgps:{n.kind.capitalize()}")
            self.node(parent, "prov:Entity")
            self.add(parent, "wasDerivedFrom", target)
        previous = a_iri
        for succ in chain.successors:
            s_iri = _iri("assertion", succ, self.base)
            self.node(s_iri, "prov:Entity", "kgps:Assertion")
            self.add(s_iri, "wasRevisionOf", previous)
            previous = s_iri
        for d in chain.decisions:
            d_iri = _iri("curation", d.audit_id, self.base)
            dn = self.node(d_iri, "prov:Activity", "kgps:CurationDecision")
            dn["kgps:decisionKind"] = d.decision_kind
            if d.final_kind:
                dn["kgps:finalKind"] = d.final_kind
            if d.recorded_at:
                dn["startedAtTime"] = d.recorded_at.isoformat()
            if d.role == "resulting" or (d.role is None and "wasGeneratedBy" not in node):
                node["wasGeneratedBy"] = d_iri
        return a_iri

    def document(self) -> dict[str, Any]:
        graph = []
        for n in self.nodes.values():
            if not n["@type"]:
                n = {k: v for k, v in n.items() if k != "@type"}
            graph.append(n)
        return {"@context": CONTEXT, "@graph": graph}


def chain_to_prov(
    chain: EvidenceChain, *, base: str = DEFAULT_BASE, include_quotes: bool = False
) -> dict[str, Any]:
    """PROV-O JSON-LD for one assertion's evidence chain."""
    doc = _Doc(base, include_quotes)
    doc.chain(chain)
    return doc.document()


def graph_to_prov(
    graph: ProvenanceGraph,
    *,
    produced_by: str | None = None,
    sentences: dict[str, str] | None = None,
    base: str = DEFAULT_BASE,
    include_quotes: bool = False,
) -> dict[str, Any]:
    """PROV-O JSON-LD for an answer: output spans → assertions → evidence.

    ``sentences`` optionally maps output-span node ids (``out:i@chars:s-e``) to
    their text, exported as ``prov:value`` only with ``include_quotes``.
    """
    doc = _Doc(base, include_quotes)
    act = _iri("answer", graph.trace_id, base)
    doc.node(act, "prov:Activity", "kgps:AnswerGeneration")
    if produced_by:
        doc.add(act, "wasAssociatedWith", doc.agent(produced_by, software=True))
    assertion_iris = {aid: doc.chain(chain) for aid, chain in graph.chains.items()}
    for edge in graph.edges:
        if edge.kind.value != "CITES":
            continue
        out = _iri("output", f"{graph.trace_id}/{edge.source}", base)
        on = doc.node(out, "prov:Entity", "kgps:OutputSpan")
        on["wasGeneratedBy"] = act
        on["kgps:span"] = edge.source
        if include_quotes and sentences and edge.source in sentences:
            on["prov:value"] = sentences[edge.source]
        target = assertion_iris.get(edge.target) or _iri("assertion", edge.target, base)
        doc.add(out, "wasDerivedFrom", target)
    return doc.document()


__all__ = ["CONTEXT", "chain_to_prov", "graph_to_prov", "sentence_node"]
