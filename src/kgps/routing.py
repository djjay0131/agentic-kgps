"""Retrieval routing with a provenance gate (wave 4; ADR-0008).

PA-AKG's retrieval claim is that *where* evidence is retrieved from (dense,
sparse, graph) should be routed per query, and that what reaches the
generator should already be provenance-checked. This module is that
contract plus deterministic reference implementations:

* ``Retriever`` — ``retrieve(query, k) -> [RetrievedItem]`` over canonical
  assertions, tagged with its ``RetrievalMode``.
* ``SparseRetriever`` (BM25), ``DenseRetriever`` (any ``embed`` callable;
  ``HashingEmbedder`` is a model-free default so CI is deterministic) and
  ``GraphRetriever`` (seeds from another retriever, expands one hop through
  derivation inputs, supersession and shared identities).
* ``route(query)`` — a rule-based ``RoutingDecision``; ``fuse`` — reciprocal
  rank fusion across modes.
* ``ProvenanceRouter`` — route → retrieve → fuse → **provenance gate**:
  superseded records are replaced by their current successor, then anything
  not ACTIVE (revoked, rejected; ``require_active``) or not grounded
  (``require_grounded``) is dropped.

Documents are built from what KGPS can read: the assertion's statement
(``render``) plus the visible text of its present grounding evidence.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import Assertion, CurationStatus
from pydantic import BaseModel, ConfigDict, Field

from kgps.models import GROUNDING_RELATIONSHIPS
from kgps.ports import AssertionCatalog
from kgps.service import ProvenanceService
from kgps.telemetry import span
from kgps.verify import evidence_text, render_assertion


class RetrievalMode(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    GRAPH = "graph"
    FUSED = "fused"


class RetrievedItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str
    score: float
    mode: RetrievalMode
    grounded: bool | None = None
    via: str | None = None
    """For graph expansion: the seed assertion this item was reached from."""


@runtime_checkable
class Retriever(Protocol):
    mode: RetrievalMode

    def retrieve(self, query: str, k: int) -> list[RetrievedItem]: ...


_TOKEN = re.compile(r"[a-z0-9][a-z0-9%.'-]*")


def tokenize(text: str) -> list[str]:
    return [t.strip(".'-") for t in _TOKEN.findall(text.lower()) if t.strip(".'-")]


class AssertionDocuments:
    """Text per assertion: rendered statement + visible grounding evidence text.

    Built once from a catalog snapshot (call ``refresh()`` after the graph
    moves). Superseded and revoked records are kept: routing decides later.
    """

    def __init__(
        self,
        catalog: AssertionCatalog,
        svc: ProvenanceService,
        *,
        render: Callable[[Assertion], str] = render_assertion,
    ) -> None:
        self._catalog = catalog
        self._svc = svc
        self._render = render
        self.texts: dict[str, str] = {}
        self.assertions: dict[str, Assertion] = {}
        self.errors: tuple[str, ...] = ()
        self.refresh()

    def refresh(self) -> None:
        """Rebuild; a failing catalog leaves an empty index plus ``errors`` (ADR-0003)."""
        texts: dict[str, str] = {}
        assertions: dict[str, Assertion] = {}
        try:
            catalog = tuple(self._catalog.iter_assertions())
        except Exception as exc:  # noqa: BLE001
            self.texts, self.assertions = {}, {}
            self.errors = (f"assertion scan failed: {type(exc).__name__}",)
            return
        self.errors = ()
        for a in catalog:
            chain = self._svc.evidence_chain(a.assertion_id, with_lineage=False)
            parts = [self._render(a)]
            for link in chain.links:
                if link.relationship in GROUNDING_RELATIONSHIPS:
                    text = evidence_text(link.evidence)
                    if text and text not in parts:
                        parts.append(text)
            texts[a.assertion_id] = "\n".join(parts)
            assertions[a.assertion_id] = a
        self.texts, self.assertions = texts, assertions


class SparseRetriever:
    """Okapi BM25 over ``AssertionDocuments``."""

    mode = RetrievalMode.SPARSE

    def __init__(self, docs: AssertionDocuments, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._docs = docs
        self._k1, self._b = k1, b
        self._index()

    def _index(self) -> None:
        self._tf = {aid: Counter(tokenize(t)) for aid, t in self._docs.texts.items()}
        self._len = {aid: sum(c.values()) for aid, c in self._tf.items()}
        n = len(self._tf) or 1
        self._avg = (sum(self._len.values()) / n) or 1.0
        df: Counter[str] = Counter()
        for c in self._tf.values():
            df.update(c.keys())
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def retrieve(self, query: str, k: int) -> list[RetrievedItem]:
        terms = tokenize(query)
        scores: dict[str, float] = {}
        for aid, tf in self._tf.items():
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    norm = 1 - self._b + self._b * self._len[aid] / self._avg
                    s += self._idf.get(t, 0.0) * f * (self._k1 + 1) / (f + self._k1 * norm)
            if s > 0:
                scores[aid] = s
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [RetrievedItem(assertion_id=a, score=round(s, 6), mode=self.mode) for a, s in ranked]


class HashingEmbedder:
    """Deterministic, model-free embedding: hashed word and character trigrams.

    A stand-in so the dense path is testable and reproducible; deployments
    pass a real embedding function to ``DenseRetriever``.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            feats = [f"w:{tok}"] + [f"c:{tok[i:i + 3]}" for i in range(max(1, len(tok) - 2))]
            for f in feats:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
                vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class DenseRetriever:
    mode = RetrievalMode.DENSE

    def __init__(
        self,
        docs: AssertionDocuments,
        embed: Callable[[str], Sequence[float]] | None = None,
    ) -> None:
        self._embed = embed or HashingEmbedder()
        self._vectors = {aid: list(self._embed(t)) for aid, t in docs.texts.items()}

    def retrieve(self, query: str, k: int) -> list[RetrievedItem]:
        q = list(self._embed(query))
        scored = [(aid, _cosine(q, v)) for aid, v in self._vectors.items()]
        ranked = sorted((x for x in scored if x[1] > 0), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [RetrievedItem(assertion_id=a, score=round(s, 6), mode=self.mode) for a, s in ranked]


class GraphRetriever:
    """Seed with another retriever, then expand one hop over the graph.

    Neighbours: derivation inputs and consumers, supersession links, and
    assertions sharing a subject or object identity with the seed (identity
    groups larger than ``max_identity_group`` are skipped). A
    neighbour scores ``decay`` × its seed's score and records ``via``.
    """

    mode = RetrievalMode.GRAPH

    def __init__(
        self,
        docs: AssertionDocuments,
        seed: Retriever,
        *,
        decay: float = 0.5,
        max_identity_group: int = 50,
    ) -> None:
        self._docs = docs
        self._seed = seed
        self._decay = decay
        self._max_group = max_identity_group
        self._neighbours = self._build()

    def _build(self) -> dict[str, set[str]]:
        nb: dict[str, set[str]] = {aid: set() for aid in self._docs.assertions}
        by_identity: dict[str, set[str]] = {}
        for aid, a in self._docs.assertions.items():
            for ident in (a.subject_identity, a.object_identity):
                if ident:
                    by_identity.setdefault(ident, set()).add(aid)
            if a.derivation is not None:
                for item in a.derivation.inputs:
                    if item.kind == "assertion" and item.ref in nb:
                        nb[aid].add(item.ref)
                        nb[item.ref].add(aid)
            successor = getattr(a, "superseded_by", None)
            if successor and successor in nb:
                nb[aid].add(successor)
                nb[successor].add(aid)
        # Hub identities (a subject shared by thousands of assertions) would
        # make every member a neighbour of every other: O(n^2). Groups above
        # ``max_identity_group`` are not expanded; derivation and supersession
        # edges still are.
        for group in by_identity.values():
            if len(group) > self._max_group:
                continue
            for aid in group:
                nb[aid] |= group - {aid}
        return nb

    def retrieve(self, query: str, k: int) -> list[RetrievedItem]:
        seeds = self._seed.retrieve(query, k)
        best: dict[str, RetrievedItem] = {}
        for s in seeds:
            best[s.assertion_id] = RetrievedItem(
                assertion_id=s.assertion_id, score=s.score, mode=self.mode
            )
        for s in seeds:
            for n in sorted(self._neighbours.get(s.assertion_id, ())):
                score = round(s.score * self._decay, 6)
                if n not in best or best[n].score < score:
                    best[n] = RetrievedItem(
                        assertion_id=n, score=score, mode=self.mode, via=s.assertion_id
                    )
        return sorted(best.values(), key=lambda i: (-i.score, i.assertion_id))[:k]


# -- routing -----------------------------------------------------------------------

_GRAPH_CUES = re.compile(
    r"\b(why|because|derived|lineage|based on|depends?|related|connected|supersed\w*|"
    r"replac\w*|history|caused?|led to|origin)\b"
)
_SPARSE_CUES = re.compile(r"\"[^\"]+\"|\b\d[\d.,%]*\b|\b[A-Z]{2,}\d*\b|\b\w+[_:/]\w+")


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    modes: tuple[RetrievalMode, ...]
    reason: str


def route(query: str) -> RoutingDecision:
    """Rule-based router: graph for relational/why questions, sparse for exact
    tokens (quotes, numbers, acronyms, identifiers), dense always as the
    semantic backstop. Deterministic and explainable (``reason``)."""
    modes: list[RetrievalMode] = []
    reasons: list[str] = []
    if _SPARSE_CUES.search(query):
        modes.append(RetrievalMode.SPARSE)
        reasons.append("exact tokens (quotes/numbers/identifiers)")
    if _GRAPH_CUES.search(query.lower()):
        modes.append(RetrievalMode.GRAPH)
        reasons.append("relational or provenance question")
    modes.append(RetrievalMode.DENSE)
    reasons.append("semantic backstop")
    return RoutingDecision(modes=tuple(modes), reason="; ".join(reasons))


def fuse(rankings: Iterable[Sequence[RetrievedItem]], k: int, *, c: int = 60) -> list[RetrievedItem]:
    """Reciprocal rank fusion; ties broken by assertion id for determinism."""
    scores: dict[str, float] = {}
    via: dict[str, str | None] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item.assertion_id] = scores.get(item.assertion_id, 0.0) + 1.0 / (c + rank)
            via.setdefault(item.assertion_id, item.via)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [
        RetrievedItem(assertion_id=a, score=round(s, 6), mode=RetrievalMode.FUSED, via=via[a])
        for a, s in ranked
    ]


class RoutedResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    decision: RoutingDecision
    items: tuple[RetrievedItem, ...]
    candidates: tuple[RetrievedItem, ...] = ()
    """The fused ranking *before* the provenance gate (for retrieval metrics)."""
    dropped_ungrounded: tuple[str, ...] = ()
    dropped_inactive: tuple[str, ...] = ()
    replaced_superseded: dict[str, str] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()


class ProvenanceRouter:
    """route → retrieve per mode → fuse → provenance gate (ADR-0008)."""

    def __init__(
        self,
        svc: ProvenanceService,
        retrievers: Iterable[Retriever],
        *,
        require_grounded: bool = True,
        require_active: bool = True,
        follow_supersession: bool = True,
        router: Callable[[str], RoutingDecision] = route,
    ) -> None:
        self._svc = svc
        self._by_mode = {r.mode: r for r in retrievers}
        self._require_grounded = require_grounded
        self._require_active = require_active
        self._follow = follow_supersession
        self._route = router

    def retrieve(self, query: str, k: int = 5) -> RoutedResult:
        """Never raises (ADR-0003): a failing retriever is recorded in ``errors``."""
        decision = self._route(query)
        errors: list[str] = []
        used: list[RetrievalMode] = []
        with span("route", {"kgps.modes": ",".join(m.value for m in decision.modes)}):
            rankings: list[list[RetrievedItem]] = []
            for m in decision.modes:
                retriever = self._by_mode.get(m)
                if retriever is None:
                    errors.append(f"{m.value}: no retriever configured")
                    continue
                try:
                    rankings.append(list(retriever.retrieve(query, k * 2)))
                    used.append(m)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{m.value}: {type(exc).__name__}")
            fused = fuse(rankings, k * 2)
            kept: list[RetrievedItem] = []
            dropped: dict[str, None] = {}
            inactive: dict[str, None] = {}
            replaced: dict[str, str] = {}
            seen: set[str] = set()
            for item in fused:
                aid = item.assertion_id
                chain = self._svc.evidence_chain(aid, with_lineage=False)
                if self._follow and chain.current_assertion_id != aid:
                    succ = self._svc.evidence_chain(chain.current_assertion_id, with_lineage=False)
                    if succ.assertion is not None:  # never swap in an unresolvable id
                        replaced[aid] = chain.current_assertion_id
                        aid, chain = chain.current_assertion_id, succ
                if aid in seen:
                    continue
                status = chain.assertion.status if chain.assertion is not None else None
                if self._require_active and status is not CurationStatus.ACTIVE:
                    inactive.setdefault(aid, None)
                    continue
                if self._require_grounded and not chain.grounded:
                    dropped.setdefault(aid, None)
                    continue
                seen.add(aid)
                kept.append(item.model_copy(update={"assertion_id": aid, "grounded": chain.grounded}))
                if len(kept) == k:
                    break
        if used != list(decision.modes):
            decision = RoutingDecision(
                modes=tuple(used),
                reason=decision.reason + f" (used: {', '.join(m.value for m in used) or 'none'})",
            )
        return RoutedResult(
            query=query, decision=decision, items=tuple(kept), candidates=tuple(fused),
            dropped_ungrounded=tuple(dropped), dropped_inactive=tuple(inactive),
            replaced_superseded=replaced, errors=tuple(errors),
        )
