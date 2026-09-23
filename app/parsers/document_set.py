# =============================================================================
# CRESEM - Multi-document evidence set
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Read one borrower's documents as a single body of evidence.

A lender does not send one file. It sends a borrower's folder: audited
financials, bank statements, GST returns, an ITR, KYC, sanction letters. Those
are one case, not six, and the value of having them together is precisely that
they can be checked against each other.

Treating each file as its own appraisal costs six times as much and, more
importantly, makes the check impossible - a figure can only be reconciled
against a second source if both are in the same run.

This module merges several ParseResults into one picture and, where two
documents state the same field differently, records that disagreement instead
of silently picking one. That disagreement is the finding a credit officer
actually needs: the balance sheet says one revenue, the GST summary says
another, and someone has to explain the gap before the file is approved.

Nothing here calls a model. Every figure keeps the document, line number and
source text it came from, so a conflict can be checked at its source in
seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.parsers.schedule_iii import (
    CAPTIONS,
    ParseResult,
    extract_entity_name,
    parse_schedule_iii,
)

# Two figures for the same field are treated as agreeing within this tolerance.
# Statements round to the nearest lakh or thousand, so exact equality across
# documents is the exception rather than the rule; a gap wider than this is a
# difference in substance, not in rounding.
AGREEMENT_TOLERANCE = 0.01

# Fields where a disagreement bears directly on the credit decision, and so is
# reported as a conflict rather than a note.
MATERIAL_FIELDS = (
    "total_revenue", "pat", "long_term_debt", "short_term_debt",
    "share_capital", "reserves", "current_assets", "current_liabilities",
    "total_assets",
)


@dataclass
class SourcedFigure:
    """A figure, and the document and line it was read from."""

    value: float
    document: str
    line_number: int
    source_line: str
    caption: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "document": self.document,
            "line": self.line_number,
            "source": self.source_line,
            "caption": self.caption,
            "verified": True,
        }


@dataclass
class Conflict:
    """The same field, read differently from two documents."""

    field: str
    readings: List[SourcedFigure]

    @property
    def spread(self) -> float:
        """Relative gap between the extreme readings."""
        values = [r.value for r in self.readings]
        low, high = min(values), max(values)
        base = max(abs(low), abs(high))
        return abs(high - low) / base if base else 0.0

    @property
    def material(self) -> bool:
        return self.field in MATERIAL_FIELDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "spread": round(self.spread, 4),
            "material": self.material,
            "readings": [r.to_dict() for r in self.readings],
        }


@dataclass
class DocumentSet:
    """Several parsed documents read as one borrower's evidence."""

    documents: List[str] = field(default_factory=list)
    parsed: Dict[str, ParseResult] = field(default_factory=dict)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    figures: Dict[str, SourcedFigure] = field(default_factory=dict)
    conflicts: List[Conflict] = field(default_factory=list)
    entities: Dict[str, str] = field(default_factory=dict)
    entity_mismatch: bool = False

    # -- access ------------------------------------------------------------

    def value(self, name: str) -> Optional[float]:
        f = self.figures.get(name)
        return f.value if f else None

    def sum_of(self, *names: str) -> Optional[float]:
        present = [self.value(n) for n in names if self.value(n) is not None]
        return sum(present) if present else None

    def total_debt(self) -> Optional[float]:
        return self.sum_of("long_term_debt", "short_term_debt")

    def equity(self) -> Optional[float]:
        return self.sum_of("share_capital", "reserves")

    def ebitda(self) -> Optional[float]:
        pbt = self.value("pbt")
        if pbt is None:
            return None
        return pbt + (self.value("finance_costs") or 0.0) + (self.value("depreciation") or 0.0)

    def current_liabilities(self) -> Optional[float]:
        stated = self.value("current_liabilities")
        if stated is not None:
            return stated
        return self.sum_of("short_term_debt", "trade_payables",
                           "other_current_liabilities")

    def to_financial_data(self) -> Dict[str, Any]:
        ebitda = self.ebitda()
        return {
            "revenue": self.value("total_revenue"),
            "ebitda": ebitda,
            "total_debt": self.total_debt(),
            "total_equity": self.equity(),
            "current_assets": self.value("current_assets"),
            "current_liabilities": self.current_liabilities(),
            "inventory": self.value("inventories"),
            "net_operating_income": ebitda,
            "debt_service": self.value("finance_costs"),
        }

    def citations(self) -> Dict[str, Dict[str, Any]]:
        return {name: fig.to_dict() for name, fig in self.figures.items()}

    @property
    def material_conflicts(self) -> List[Conflict]:
        return [c for c in self.conflicts if c.material]

    def summary(self) -> Dict[str, Any]:
        return {
            # Documents that actually contributed a figure. A file that parsed
            # cleanly and yielded nothing was not "read" in any sense a credit
            # officer means by the word.
            "documents_read": len([p for p in self.parsed.values() if p.figures]),
            "documents_skipped": [{"document": d, "reason": r} for d, r in self.skipped],
            "figures_matched": len(self.figures),
            "coverage": round(len(self.figures) / len(CAPTIONS), 3),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "material_conflicts": len(self.material_conflicts),
            "borrower": self.borrower,
            "entities_named": self.entities,
            "entity_mismatch": self.entity_mismatch,
        }

    @property
    def borrower(self) -> Optional[str]:
        """The entity these documents describe, or None when they disagree.

        Returning None on a mismatch is the point. A merged set that names two
        different companies is not one borrower's file, and guessing which one
        it belongs to would attach an appraisal to the wrong company.
        """
        if self.entity_mismatch or not self.entities:
            return None
        return next(iter(self.entities.values()))


_ENTITY_NOISE = (
    "private limited", "pvt ltd", "pvt. ltd.", "pvt limited", "limited", "ltd",
    "llp", "&", ".", ",",
)


def _normalise_entity(name: str) -> str:
    """Reduce a company name to a comparable core.

    An auditor writes "Sahyadri Precision Castings Private Limited" on the
    balance sheet and "Sahyadri Precision Castings Pvt Ltd" on the P&L. Those
    are the same borrower, and treating them as a mismatch would make the guard
    fire on almost every genuine file, which would train people to ignore it.
    """
    core = name.lower().strip()
    for token in _ENTITY_NOISE:
        core = core.replace(token, " ")
    return " ".join(core.split())


def _agree(a: float, b: float) -> bool:
    base = max(abs(a), abs(b))
    if base == 0:
        return a == b
    return abs(a - b) / base <= AGREEMENT_TOLERANCE


def merge(documents: Dict[str, str]) -> DocumentSet:
    """Parse and merge a borrower's documents.

    ``documents`` maps a display name - the filename the lender sent - to that
    document's extracted text. The name is kept rather than a path because it is
    what appears in the report a credit officer reads.

    Where one document alone supplies a field, that reading is used. Where two
    supply it and agree, the first is used and no note is made. Where they
    disagree beyond rounding, both readings are kept and a conflict is recorded:
    choosing between them is an underwriting judgement, not a parsing decision,
    and quietly preferring one would hide exactly what the lender needs to see.
    """
    result = DocumentSet()
    readings: Dict[str, List[SourcedFigure]] = {}

    for name, text in documents.items():
        result.documents.append(name)

        if not text or not text.strip():
            result.skipped.append((name, "no extractable text"))
            continue

        # Every document is parsed and judged by what it yields, not by whether
        # it looks like a full statement first. looks_like_schedule_iii() earns
        # its place in the single-document path, where it decides whether to
        # spend a provider call; here parsing costs a fraction of a millisecond
        # and the pre-filter only does harm. A GST summary or a sanction letter
        # carrying one Schedule III caption would fail that check while holding
        # exactly the second reading a reconciliation needs.
        entity = extract_entity_name(text)
        if entity:
            result.entities[name] = entity

        parsed = parse_schedule_iii(text)
        result.parsed[name] = parsed
        if not parsed.figures:
            # A KYC page, a photograph, an unreadable scan. Not an error: it
            # simply has no Schedule III captions, and saying so is more useful
            # than reporting zero figures as though they were read.
            result.skipped.append((name, "no Schedule III captions matched"))
            continue

        for field_name, fig in parsed.figures.items():
            readings.setdefault(field_name, []).append(
                SourcedFigure(value=fig.value, document=name,
                              line_number=fig.line_number,
                              source_line=fig.source_line, caption=fig.caption)
            )

    for field_name, found in readings.items():
        result.figures[field_name] = found[0]
        if len(found) == 1:
            continue
        # Any reading that disagrees with the first makes this a conflict.
        if any(not _agree(found[0].value, other.value) for other in found[1:]):
            result.conflicts.append(Conflict(field=field_name, readings=list(found)))

    # Do these documents describe the same company?
    #
    # Merging is only meaningful for one borrower's file. A folder holding two
    # borrowers would otherwise produce a single confident picture assembled
    # from both - the worst possible failure here, because every figure in it
    # is individually traceable and the whole is still fiction. Names are
    # compared loosely, since the same company appears as "X Pvt Ltd" and
    # "X Private Limited" across its own documents.
    distinct = {_normalise_entity(e) for e in result.entities.values()}
    result.entity_mismatch = len(distinct) > 1

    result.conflicts.sort(key=lambda c: (not c.material, -c.spread))
    return result


def merge_pdfs(paths: Dict[str, str]) -> DocumentSet:
    """Convenience wrapper: read each PDF's text, then merge.

    ``paths`` maps display name to file path. A file that cannot be read is
    skipped with its reason rather than failing the whole set - one unreadable
    scan must not discard the five documents that were readable.
    """
    from pypdf import PdfReader

    texts: Dict[str, str] = {}
    unreadable: List[Tuple[str, str]] = []
    for name, path in paths.items():
        try:
            texts[name] = "\n".join(
                (page.extract_text() or "") for page in PdfReader(path).pages
            )
        except Exception as exc:
            unreadable.append((name, f"could not be read: {type(exc).__name__}"))

    merged = merge(texts)
    merged.skipped.extend(unreadable)
    merged.documents.extend(name for name, _ in unreadable)
    return merged
