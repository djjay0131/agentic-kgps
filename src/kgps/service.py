"""The provenance query surface (PA-AKG read side; design spec §4).

``ProvenanceService`` answers four questions over records KGIS and KGCS
already keep, and never writes anything back (ADR-0001):

* ``evidence_chain(assertion_id)`` — which evidence backs this assertion,
  resolved, span-anchored, with every defect named as a gap.
* ``lineage(assertion_id)`` — the derivation DAG behind it, walked through
  ``Derivation.inputs`` to evidence/artifact/candidate leaves.
* ``impacted_by(evidence_id)`` — the reverse: which assertions need
  re-validation if this evidence is retracted or revised.
* ``explain(assertion_id)`` — a short readable summary plus the full chain,
  the payload an agent or UI shows for "why is this here?".

Gaps are data (ADR-0003): no query raises because provenance is missing.
"""

from collections import deque

from kg_contracts.assertions import Assertion, CurationStatus
from kg_contracts.evidence import EvidenceAvailability, EvidenceRelationship

from kgps.models import (
    EvidenceChain,
    EvidenceLink,
    Explanation,
    GapKind,
    ImpactReport,
    LineageNode,
    ProvenanceGap,
)
from kgps.ports import AssertionCatalog, EvidenceLookup
from kgps.spans import parse_span

DEFAULT_MAX_DEPTH = 16


class ProvenanceService:
    def __init__(
        self,
        assertions: AssertionCatalog,
        evidence: EvidenceLookup,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._assertions = assertions
        self._evidence = evidence
        self._max_depth = max_depth

    # -- evidence chain ------------------------------------------------------

    def evidence_chain(self, assertion_id: str, *, with_lineage: bool = True) -> EvidenceChain:
        assertion = self._assertions.get_assertion(assertion_id)
        if assertion is None:
            return EvidenceChain(
                assertion_id=assertion_id,
                assertion=None,
                gaps=(
                    ProvenanceGap(
                        kind=GapKind.UNKNOWN_ASSERTION,
                        subject_id=assertion_id,
                        detail="no canonical assertion with this id (any status)",
                    ),
                ),
            )
        links, gaps = self._links_for(assertion)
        lineage: tuple[LineageNode, ...] = ()
        if with_lineage:
            lineage, lineage_gaps = self._walk_lineage(assertion)
            gaps += lineage_gaps
        return EvidenceChain(
            assertion_id=assertion_id,
            assertion=assertion,
            links=links,
            lineage=lineage,
            gaps=tuple(gaps),
        )

    def _links_for(
        self, assertion: Assertion
    ) -> tuple[tuple[EvidenceLink, ...], list[ProvenanceGap]]:
        aid = assertion.assertion_id
        gaps: list[ProvenanceGap] = []
        if assertion.status is not CurationStatus.ACTIVE:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NOT_ACTIVE,
                    subject_id=aid,
                    detail=f"assertion status is {assertion.status.value}",
                )
            )
        if not assertion.evidence_refs:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_EVIDENCE_REFS,
                    subject_id=aid,
                    detail="assertion cites no evidence"
                    + (" (derived; see lineage)" if assertion.derivation else ""),
                )
            )
            return (), gaps

        links: list[EvidenceLink] = []
        for ref in assertion.evidence_refs:
            ev = self._evidence.get(ref.evidence_id)
            span = parse_span(ev.source_locator) if ev is not None else None
            links.append(
                EvidenceLink(
                    evidence_id=ref.evidence_id,
                    relationship=ref.relationship,
                    evidence=ev,
                    span=span,
                )
            )
            if ev is None:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.DANGLING_EVIDENCE_REF,
                        subject_id=ref.evidence_id,
                        detail=f"{aid} cites evidence the registry does not hold",
                    )
                )
                continue
            if ev.availability is EvidenceAvailability.ABSENT:
                reason = ev.absence_reason.value if ev.absence_reason else "unknown"
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.EVIDENCE_ABSENT,
                        subject_id=ev.evidence_id,
                        detail=f"evidence is ABSENT ({reason})",
                    )
                )
            elif ev.availability is EvidenceAvailability.ERROR:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.EVIDENCE_ERROR,
                        subject_id=ev.evidence_id,
                        detail=f"evidence is ERROR ({ev.error})",
                    )
                )
            else:
                if span is None:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.NO_SPAN,
                            subject_id=ev.evidence_id,
                            detail="evidence locator carries no character span",
                        )
                    )
                if ev.content is None:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.HASH_ONLY_EVIDENCE,
                            subject_id=ev.evidence_id,
                            detail="evidence has a payload hash but no inline content",
                        )
                    )
            if ref.relationship is EvidenceRelationship.CONTRADICTS:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.CONTRADICTING_EVIDENCE,
                        subject_id=ref.evidence_id,
                        detail=f"{aid} carries evidence that contradicts it",
                    )
                )

        backing = [
            link
            for link in links
            if link.evidence is not None
            and link.evidence.availability is EvidenceAvailability.PRESENT
            and link.relationship is not EvidenceRelationship.CONTRADICTS
        ]
        if not backing:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_PRESENT_EVIDENCE,
                    subject_id=aid,
                    detail="no cited evidence is PRESENT and non-contradicting",
                )
            )
        elif not any(link.relationship is EvidenceRelationship.SUPPORTS for link in backing):
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_SUPPORTS_RELATIONSHIP,
                    subject_id=aid,
                    detail=(
                        "evidence is cited as DERIVED_FROM/CONTEXTUALIZES only; nothing "
                        "has checked that it SUPPORTS the assertion (design spec §5)"
                    ),
                )
            )
        return tuple(links), gaps

    # -- lineage -------------------------------------------------------------

    def lineage(self, assertion_id: str) -> tuple[LineageNode, ...]:
        assertion = self._assertions.get_assertion(assertion_id)
        if assertion is None:
            return ()
        nodes, _ = self._walk_lineage(assertion)
        return nodes

    def _walk_lineage(
        self, root: Assertion
    ) -> tuple[tuple[LineageNode, ...], list[ProvenanceGap]]:
        nodes: list[LineageNode] = [
            LineageNode(
                kind="assertion",
                ref=root.assertion_id,
                depth=0,
                method=root.derivation.method if root.derivation else None,
                implementation_version=(
                    root.derivation.implementation_version if root.derivation else None
                ),
            )
        ]
        gaps: list[ProvenanceGap] = []
        seen: set[str] = {root.assertion_id}
        queue: deque[tuple[Assertion, int]] = deque([(root, 0)])
        while queue:
            current, depth = queue.popleft()
            if current.derivation is None:
                continue
            if depth >= self._max_depth:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.LINEAGE_DEPTH_LIMIT,
                        subject_id=current.assertion_id,
                        detail=f"lineage walk stopped at depth {self._max_depth}",
                    )
                )
                continue
            for item in current.derivation.inputs:
                child_depth = depth + 1
                if item.kind == "assertion":
                    if item.ref in seen:
                        gaps.append(
                            ProvenanceGap(
                                kind=GapKind.LINEAGE_CYCLE,
                                subject_id=item.ref,
                                detail=f"{current.assertion_id} re-enters {item.ref}",
                            )
                        )
                        continue
                    seen.add(item.ref)
                    child = self._assertions.get_assertion(item.ref)
                    nodes.append(
                        LineageNode(
                            kind="assertion",
                            ref=item.ref,
                            depth=child_depth,
                            parent_ref=current.assertion_id,
                            method=child.derivation.method if child and child.derivation else None,
                            implementation_version=(
                                child.derivation.implementation_version
                                if child and child.derivation
                                else None
                            ),
                            resolved=child is not None,
                        )
                    )
                    if child is None:
                        gaps.append(
                            ProvenanceGap(
                                kind=GapKind.UNRESOLVED_LINEAGE_INPUT,
                                subject_id=item.ref,
                                detail=f"{current.assertion_id} derives from an unknown assertion",
                            )
                        )
                    else:
                        queue.append((child, child_depth))
                elif item.kind == "evidence":
                    resolved = self._evidence.get(item.ref) is not None
                    nodes.append(
                        LineageNode(
                            kind="evidence",
                            ref=item.ref,
                            depth=child_depth,
                            parent_ref=current.assertion_id,
                            resolved=resolved,
                        )
                    )
                    if not resolved:
                        gaps.append(
                            ProvenanceGap(
                                kind=GapKind.UNRESOLVED_LINEAGE_INPUT,
                                subject_id=item.ref,
                                detail=f"{current.assertion_id} derives from unknown evidence",
                            )
                        )
                else:
                    # artifact / candidate refs live in stores KGPS does not read yet
                    # (ledger, artifact registry); recorded as leaves, not gaps.
                    nodes.append(
                        LineageNode(
                            kind=item.kind,
                            ref=item.ref,
                            depth=child_depth,
                            parent_ref=current.assertion_id,
                        )
                    )
        return tuple(nodes), gaps

    # -- reverse lineage -----------------------------------------------------

    def impacted_by(self, evidence_id: str) -> ImpactReport:
        """Assertions that cite ``evidence_id``, then everything derived from them."""
        direct: list[str] = []
        consumers: dict[str, list[str]] = {}
        for a in self._assertions.iter_assertions():
            if any(ref.evidence_id == evidence_id for ref in a.evidence_refs):
                direct.append(a.assertion_id)
            if a.derivation is not None:
                for item in a.derivation.inputs:
                    if item.kind == "evidence" and item.ref == evidence_id:
                        direct.append(a.assertion_id)
                    elif item.kind == "assertion":
                        consumers.setdefault(item.ref, []).append(a.assertion_id)

        direct_unique = list(dict.fromkeys(direct))
        seen = set(direct_unique)
        transitive: list[str] = []
        queue = deque(direct_unique)
        while queue:
            current = queue.popleft()
            for consumer in consumers.get(current, ()):
                if consumer not in seen:
                    seen.add(consumer)
                    transitive.append(consumer)
                    queue.append(consumer)
        return ImpactReport(
            evidence_id=evidence_id,
            direct=tuple(sorted(direct_unique)),
            transitive=tuple(transitive),
        )

    # -- explain -------------------------------------------------------------

    def explain(self, assertion_id: str) -> Explanation:
        chain = self.evidence_chain(assertion_id)
        return Explanation(
            assertion_id=assertion_id,
            summary=_summarise(chain),
            grounded=chain.grounded,
            chain=chain,
        )


def _summarise(chain: EvidenceChain) -> str:
    a = chain.assertion
    if a is None:
        return f"{chain.assertion_id}: unknown assertion."
    obj = a.object_identity if a.object_identity is not None else repr(a.object_value)
    lines = [
        f"{a.subject_identity} {a.predicate} {obj} "
        f"[{a.status.value}, epoch {a.curation_epoch}, authority {a.authority}]"
    ]
    present = chain.present_evidence
    if present:
        lines.append(f"Backed by {len(present)} present evidence record(s):")
        for link in chain.links:
            ev = link.evidence
            if ev is None or ev not in present:
                continue
            where = (
                f"{link.span.locator} chars {link.span.start}-{link.span.end}"
                if link.span
                else ev.source_locator
            )
            model = f", model {ev.provenance.model}" if ev.provenance.model else ""
            lines.append(
                f"  - {link.relationship.value} {where} "
                f"(actor {ev.provenance.actor}{model})"
            )
    derived = [n for n in chain.lineage if n.depth > 0]
    if derived:
        lines.append(
            f"Derived via {a.derivation.method if a.derivation else '?'} "
            f"from {len(derived)} upstream record(s)."
        )
    if chain.gaps:
        kinds = sorted({g.kind.value for g in chain.gaps})
        lines.append("Gaps: " + ", ".join(kinds))
    lines.append("Grounded." if chain.grounded else "NOT grounded.")
    return "\n".join(lines)
