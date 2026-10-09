"""The provenance query surface (PA-AKG read side; design spec §4).

``ProvenanceService`` answers questions over records KGIS and KGCS already
keep, and never writes anything back (ADR-0001):

* ``evidence_chain(assertion_id)`` — which evidence backs this assertion,
  resolved and span-anchored, with every defect named as a gap. Evidence is
  taken from the assertion's own ``evidence_refs`` and, when the evidence
  store exposes candidate-keyed refs, from its ``source_candidate_ids``
  (ADR-0028 join; decision D-007).
* ``lineage(assertion_id)`` — the derivation DAG behind it, walked through
  ``Derivation.inputs`` plus the ``source_candidate_ids`` edges.
* ``successors(assertion_id)`` — the forward ``superseded_by`` chain.
* ``impacted_by(evidence_id)`` — the reverse: which assertions need
  re-validation if this evidence is retracted or revised.
* ``explain(assertion_id)`` — a short readable summary plus the full chain.

Gaps are data (ADR-0003): no query raises because provenance is missing.
"""

from collections import deque

from kg_contracts.assertions import Assertion, CurationStatus
from kg_contracts.evidence import EvidenceAvailability, EvidenceRef, EvidenceRelationship

from kgps.models import (
    GROUNDING_RELATIONSHIPS,
    CurationDecision,
    EvidenceChain,
    EvidenceLink,
    Explanation,
    GapKind,
    ImpactReport,
    LineageNode,
    ProvenanceGap,
)
from kgps.ports import (
    AssertionCatalog,
    CandidateRefLookup,
    CurationAuditLookup,
    EvidenceLookup,
    EvidenceSubjectLookup,
)
from kgps.spans import span_for
from kgps.telemetry import traced

DEFAULT_MAX_DEPTH = 16
_GROUNDING = GROUNDING_RELATIONSHIPS


class _StoreError(Exception):
    """A read from a store failed; converted into a STORE_ERROR gap (ADR-0003)."""


def _source_candidates(assertion: Assertion) -> tuple[str, ...]:
    return tuple(getattr(assertion, "source_candidate_ids", ()))


class ProvenanceService:
    def __init__(
        self,
        assertions: AssertionCatalog,
        evidence: EvidenceLookup,
        *,
        audit: CurationAuditLookup | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._assertions = assertions
        self._audit = audit
        self._evidence = evidence
        self._max_depth = max_depth
        self._candidate_refs = evidence if isinstance(evidence, CandidateRefLookup) else None
        self._subjects = evidence if isinstance(evidence, EvidenceSubjectLookup) else None

    # -- safe store access (ADR-0003: reads never raise) -----------------------

    def _safe(self, what: str, subject: str, fn, gaps: list[ProvenanceGap], default):  # type: ignore[no-untyped-def]
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — any store failure becomes a gap
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.STORE_ERROR,
                    subject_id=subject,
                    detail=f"{what} failed: {type(exc).__name__}: {exc}",
                )
            )
            return default

    # -- evidence chain ------------------------------------------------------

    @traced("evidence_chain", "kgps.assertion_id")
    def evidence_chain(self, assertion_id: str, *, with_lineage: bool = True) -> EvidenceChain:
        lookup_gaps: list[ProvenanceGap] = []
        assertion = self._safe(
            "assertion lookup", assertion_id,
            lambda: self._assertions.get_assertion(assertion_id), lookup_gaps, None,
        )
        if assertion is None:
            return EvidenceChain(
                assertion_id=assertion_id,
                assertion=None,
                gaps=tuple(lookup_gaps) + (
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
        successors, succ_gaps = self._walk_successors(assertion)
        gaps += succ_gaps
        decisions = self._decisions_for(assertion_id, gaps)
        return EvidenceChain(
            assertion_id=assertion_id,
            assertion=assertion,
            links=links,
            lineage=lineage,
            successors=successors,
            decisions=decisions,
            gaps=tuple(gaps),
        )

    def _refs_with_origin(
        self, assertion: Assertion
    ) -> tuple[list[tuple[EvidenceRef, str | None]], list[ProvenanceGap]]:
        """Assertion-cited refs first; candidate-cited refs only add new evidence ids."""
        gaps: list[ProvenanceGap] = []
        refs: list[tuple[EvidenceRef, str | None]] = [(r, None) for r in assertion.evidence_refs]
        seen = {(r.evidence_id, r.relationship) for r in assertion.evidence_refs}
        candidates = _source_candidates(assertion)
        if not candidates and assertion.derivation is None:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_SOURCE_CANDIDATE,
                    subject_id=assertion.assertion_id,
                    detail="assertion names no source candidate and no derivation (ADR-0028)",
                )
            )
        if self._candidate_refs is not None:
            added = 0
            lookup = self._candidate_refs
            for cid in candidates:
                cand_refs = self._safe(
                    "candidate refs lookup", cid, lambda c=cid: lookup.refs_for(c), gaps, []
                )
                for ref in cand_refs:
                    key = (ref.evidence_id, ref.relationship)
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append((ref, cid))
                    added += 1
            if added and not assertion.evidence_refs:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.EVIDENCE_VIA_CANDIDATE,
                        subject_id=assertion.assertion_id,
                        detail=(
                            f"assertion cites no evidence itself; {added} evidence ref(s) "
                            "recovered through its source candidate(s)"
                        ),
                    )
                )
        return refs, gaps

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
        refs, ref_gaps = self._refs_with_origin(assertion)
        gaps += ref_gaps
        if not refs:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_EVIDENCE_REFS,
                    subject_id=aid,
                    detail="assertion cites no evidence"
                    + (" (derived; see lineage)" if assertion.derivation else ""),
                )
            )
            return (), gaps

        redaction = getattr(self._evidence, "redaction", None)
        links: list[EvidenceLink] = []
        for ref, via in refs:
            ev = self._safe(
                "evidence lookup", ref.evidence_id,
                lambda r=ref: self._evidence.get(r.evidence_id), gaps, None,
            )
            span = span_for(ev) if ev is not None else None
            links.append(
                EvidenceLink(
                    evidence_id=ref.evidence_id,
                    relationship=ref.relationship,
                    evidence=ev,
                    span=span,
                    via_candidate=via,
                )
            )
            if ev is None:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.DANGLING_EVIDENCE_REF,
                        subject_id=ref.evidence_id,
                        detail=(
                            f"{aid} cites evidence the registry does not hold"
                            + (f" (via candidate {via})" if via else "")
                        ),
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
                marker = (
                    self._safe(
                        "redaction lookup", ev.evidence_id,
                        lambda e=ev: redaction(e.evidence_id), gaps, None,
                    )
                    if callable(redaction)
                    else None
                )
                if marker is not None and marker[0] is not None:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.REDACTED_EVIDENCE,
                            subject_id=ev.evidence_id,
                            detail=f"evidence content was redacted at {marker[0]}"
                            + (f" ({marker[1]})" if marker[1] else ""),
                        )
                    )
                elif ev.content is None:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.HASH_ONLY_EVIDENCE,
                            subject_id=ev.evidence_id,
                            detail="evidence has a payload hash but no inline content",
                        )
                    )
                if span is None:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.NO_SPAN,
                            subject_id=ev.evidence_id,
                            detail="evidence carries no character span",
                        )
                    )
                elif not span.typed:
                    gaps.append(
                        ProvenanceGap(
                            kind=GapKind.UNTYPED_SPAN,
                            subject_id=ev.evidence_id,
                            detail="span recovered from the locator string, not Evidence.span",
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
            and link.relationship in _GROUNDING
        ]
        if not backing:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_PRESENT_EVIDENCE,
                    subject_id=aid,
                    detail="no cited evidence is PRESENT with a SUPPORTS/DERIVED_FROM relationship",
                )
            )
        elif not any(link.relationship is EvidenceRelationship.SUPPORTS for link in backing):
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_SUPPORTS_RELATIONSHIP,
                    subject_id=aid,
                    detail=(
                        "evidence is cited as DERIVED_FROM only; nothing has verified that "
                        "it SUPPORTS the assertion (design spec §5)"
                    ),
                )
            )
        return tuple(links), gaps

    # -- successors ----------------------------------------------------------

    def _decisions_for(
        self, assertion_id: str, gaps: list[ProvenanceGap]
    ) -> tuple[CurationDecision, ...]:
        """KGCS decisions recorded for this assertion, oldest first (D-019)."""
        if self._audit is None:
            return ()
        audit = self._audit
        before = len(gaps)
        records = self._safe(
            "curation audit lookup", assertion_id,
            lambda: list(audit.records_for_assertion(assertion_id)), gaps, [],
        )
        decisions: list[CurationDecision] = []
        for record in records:
            try:
                decisions.append(CurationDecision.from_record(record))
            except Exception as exc:  # noqa: BLE001 — a malformed record is a gap
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.STORE_ERROR,
                        subject_id=assertion_id,
                        detail=f"unreadable curation audit record: {type(exc).__name__}: {exc}",
                    )
                )
        if not decisions and len(gaps) == before:
            gaps.append(
                ProvenanceGap(
                    kind=GapKind.NO_CURATION_AUDIT,
                    subject_id=assertion_id,
                    detail="no KGCS curation decision is recorded for this assertion",
                )
            )
        return tuple(decisions)

    @traced("successors", "kgps.assertion_id")
    def successors(self, assertion_id: str) -> tuple[str, ...]:
        # ADR-0003: a failing store yields an empty result, never an exception.
        # Callers that need the reason use evidence_chain(), which carries gaps.
        assertion = self._safe(
            "assertion lookup", assertion_id,
            lambda: self._assertions.get_assertion(assertion_id), [], None,
        )
        if assertion is None:
            return ()
        chain, _ = self._walk_successors(assertion)
        return chain

    def _walk_successors(
        self, assertion: Assertion
    ) -> tuple[tuple[str, ...], list[ProvenanceGap]]:
        chain: list[str] = []
        gaps: list[ProvenanceGap] = []
        seen = {assertion.assertion_id}
        current = assertion
        while True:
            nxt = getattr(current, "superseded_by", None)
            if nxt is None:
                break
            if nxt in seen or len(chain) >= self._max_depth:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.LINEAGE_CYCLE if nxt in seen else GapKind.LINEAGE_DEPTH_LIMIT,
                        subject_id=nxt,
                        detail=f"supersession chain stops at {current.assertion_id}",
                    )
                )
                break
            seen.add(nxt)
            chain.append(nxt)
            following = self._safe(
                "successor lookup", nxt, lambda n=nxt: self._assertions.get_assertion(n), gaps, None
            )
            if following is None:
                gaps.append(
                    ProvenanceGap(
                        kind=GapKind.UNRESOLVED_SUCCESSOR,
                        subject_id=nxt,
                        detail=f"{current.assertion_id} is superseded by an unknown assertion",
                    )
                )
                break
            current = following
        return tuple(chain), gaps

    # -- lineage -------------------------------------------------------------

    @traced("lineage", "kgps.assertion_id")
    def lineage(self, assertion_id: str) -> tuple[LineageNode, ...]:
        # ADR-0003: a failing store yields an empty result, never an exception.
        # Callers that need the reason use evidence_chain(), which carries gaps.
        assertion = self._safe(
            "assertion lookup", assertion_id,
            lambda: self._assertions.get_assertion(assertion_id), [], None,
        )
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
            child_depth = depth + 1
            for cid in _source_candidates(current):
                nodes.append(
                    LineageNode(
                        kind="candidate",
                        ref=cid,
                        depth=child_depth,
                        parent_ref=current.assertion_id,
                        via="source_candidate",
                    )
                )
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
                    child = self._safe(
                        "lineage lookup", item.ref,
                        lambda r=item.ref: self._assertions.get_assertion(r), gaps, None,
                    )
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
                    resolved = (
                        self._safe(
                            "lineage evidence lookup", item.ref,
                            lambda r=item.ref: self._evidence.get(r), gaps, None,
                        )
                        is not None
                    )
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
                    # artifact / candidate refs live in stores KGPS does not read
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

    @traced("impacted_by", "kgps.evidence_id")
    def impacted_by(self, evidence_id: str) -> ImpactReport:
        """Assertions that rest on ``evidence_id``, then everything derived from them."""
        citing_candidates: tuple[str, ...] = ()
        errors: list[ProvenanceGap] = []
        if self._subjects is not None:
            subjects = self._subjects
            citing_candidates = tuple(
                dict.fromkeys(
                    self._safe(
                        "reverse evidence lookup", evidence_id,
                        lambda: subjects.subjects_for(evidence_id), errors, [],
                    )
                )
            )
        candidate_set = set(citing_candidates)

        direct: list[str] = []
        via_candidates: list[str] = []
        consumers: dict[str, list[str]] = {}
        assertions = self._safe(
            "assertion scan", evidence_id,
            lambda: tuple(self._assertions.iter_assertions()), errors, (),
        )
        for a in assertions:
            if any(ref.evidence_id == evidence_id for ref in a.evidence_refs):
                direct.append(a.assertion_id)
            elif candidate_set and candidate_set.intersection(_source_candidates(a)):
                via_candidates.append(a.assertion_id)
            if a.derivation is not None:
                for item in a.derivation.inputs:
                    if item.kind == "evidence" and item.ref == evidence_id:
                        direct.append(a.assertion_id)
                    elif item.kind == "assertion":
                        consumers.setdefault(item.ref, []).append(a.assertion_id)

        direct_unique = list(dict.fromkeys(direct))
        via_unique = [a for a in dict.fromkeys(via_candidates) if a not in direct_unique]
        roots = direct_unique + via_unique
        seen = set(roots)
        transitive: list[str] = []
        queue = deque(roots)
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
            via_candidates=tuple(sorted(via_unique)),
            transitive=tuple(sorted(transitive)),
            citing_candidates=citing_candidates,
            errors=tuple(errors),
        )

    # -- explain -------------------------------------------------------------

    @traced("explain", "kgps.assertion_id")
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
    if chain.source_candidate_ids:
        lines.append("From candidate(s): " + ", ".join(chain.source_candidate_ids))
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
            model = ""
            if ev.provenance.model:
                model = f", model {ev.provenance.model}"
                if link.model_version:
                    model += f"@{link.model_version}"
            via = f", via candidate {link.via_candidate}" if link.via_candidate else ""
            quote = f' "{link.span.quote}"' if link.span and link.span.quote else ""
            lines.append(
                f"  - {link.relationship.value} {where}{quote} "
                f"(actor {ev.provenance.actor}{model}{via})"
            )
    derived = [n for n in chain.lineage if n.depth > 0 and n.via == "derivation"]
    if derived:
        lines.append(
            f"Derived via {a.derivation.method if a.derivation else '?'} "
            f"from {len(derived)} upstream record(s)."
        )
    for d in chain.decisions:
        when = f" at {d.recorded_at.isoformat()}" if d.recorded_at else ""
        extra = []
        if d.review_action:
            extra.append(f"review {d.review_action}/{d.review_status}")
        if d.consulted_adviser:
            extra.append("adviser consulted")
        tail = f" ({', '.join(extra)})" if extra else ""
        lines.append(
            f"Curated: {d.decision_kind} decision {d.final_kind or '?'}{when}, "
            f"audit {d.audit_id}{tail}."
        )
    if chain.successors:
        lines.append(
            "Superseded by " + " -> ".join(chain.successors)
            + f" (current: {chain.current_assertion_id})."
        )
    if chain.gaps:
        kinds = sorted({g.kind.value for g in chain.gaps})
        lines.append("Gaps: " + ", ".join(kinds))
    lines.append("Grounded." if chain.grounded else "NOT grounded.")
    return "\n".join(lines)
