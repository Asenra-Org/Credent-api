# =============================================================================
# CRESEM - Schedule III deterministic parser
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
"""Extract financial figures from an Indian statutory financial statement.

Schedule III of the Companies Act 2013 prescribes the format every Indian
company must use for its Balance Sheet and Statement of Profit and Loss. The
line-item captions are mandated, so they can be matched in code rather than
described to a language model.

This replaces the first and most expensive LLM call in the pipeline for the
standard case. The debt aggregation and exclusion rules below are the same ones
that were previously written as a nine-step English prompt in
``document_ingestion.parse_financial_statement`` - they were always a program,
so they are one here: written once, run identically every time, at no cost, and
with every figure traceable to the line that produced it.

An LLM is still needed for scanned documents with poor OCR, for non-statutory
formats (proprietorships and partnerships do not file under Schedule III), and
for narrative. Those are the exception path; this is the trunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LAKH = 100_000
CRORE = 10_000_000
THOUSAND = 1_000

# ---------------------------------------------------------------------------
# Schedule III captions.
#
# The first entry of each list is the caption as Schedule III prescribes it.
# The rest are the variants auditors and accounting packages actually emit -
# hyphenation, abbreviation, and the older Schedule VI wording that is still in
# circulation. Order matters only for readability; matching is exact-or-prefix
# against the whole caption, never a substring search, so "Other Income" can
# never be captured by a rule looking for "Income".
# ---------------------------------------------------------------------------

CAPTIONS: Dict[str, List[str]] = {
    # --- Statement of Profit and Loss -------------------------------------
    "total_revenue": [
        "revenue from operations", "total revenue", "total income",
        "turnover", "gross turnover", "net sales", "sales",
    ],
    "other_income": ["other income"],
    "cost_of_materials": ["cost of materials consumed", "raw material consumed"],
    "employee_cost": ["employee benefit expenses", "employee benefits expense",
                      "employee cost", "personnel expenses"],
    "finance_costs": ["finance costs", "finance cost", "interest expense",
                      "interest and finance charges", "interest paid"],
    "depreciation": ["depreciation", "depreciation and amortisation",
                     "depreciation and amortization",
                     "depreciation & amortisation expense"],
    "pbt": ["profit before tax", "profit/(loss) before tax", "pbt"],
    "tax_expense": ["tax expense", "provision for tax", "total tax expense"],
    "pat": ["profit after tax", "profit after tax (pat)", "pat",
            "profit for the period", "profit for the year",
            "net profit", "profit/(loss) for the period"],

    # --- Balance Sheet: shareholders' funds --------------------------------
    "share_capital": ["share capital", "equity share capital", "paid up capital",
                      "paid-up capital"],
    "reserves": ["reserves and surplus", "reserves & surplus", "other equity",
                 "retained earnings"],

    # --- Balance Sheet: borrowings ----------------------------------------
    # Schedule III splits borrowings across non-current and current. Both are
    # financial debt and both must be captured; see aggregate() below.
    "long_term_debt": [
        "long term borrowings", "long-term borrowings", "term loans",
        "term loan", "secured loans", "unsecured loans", "debentures",
        "non convertible debentures", "external commercial borrowings",
    ],
    "short_term_debt": [
        "short term borrowings", "short-term borrowings", "working capital loan",
        "working capital loans", "cash credit", "bank overdraft", "overdraft",
        "bill discounting", "buyers credit",
        "current maturities of long term debt",
        "current maturities of long-term debt",
    ],

    # --- Balance Sheet: current assets and liabilities ---------------------
    "current_assets": ["total current assets"],
    "inventories": ["inventories", "stock in trade", "closing stock"],
    "trade_receivables": ["trade receivables", "sundry debtors", "debtors"],
    "cash_and_bank": ["cash and bank", "cash and bank balances",
                      "cash and cash equivalents"],
    "current_liabilities": ["total current liabilities"],
    "trade_payables": ["trade payables", "sundry creditors", "creditors"],
    "other_current_liabilities": ["other current liabilities"],

    # --- Totals -----------------------------------------------------------
    "total_assets": ["total assets"],
}

# Schedule III classifies these as liabilities but not as financial debt. The
# distinction matters: including trade payables in total_debt inflates leverage
# and can turn a serviceable borrower into a rejection.
NOT_FINANCIAL_DEBT = (
    "trade payables", "sundry creditors", "deferred tax", "provision",
    "other current liabilities", "minority interest", "deferred revenue",
)

# Ratios a statement often asserts about itself. Parsing them lets the caller
# compare what the document claims against what its own figures support.
STATED_RATIOS: Dict[str, List[str]] = {
    "current_ratio": ["current ratio"],
    "debt_to_equity": ["debt to equity ratio", "debt-to-equity ratio",
                       "debt equity ratio"],
    "ebitda": ["ebitda"],
    "ebitda_margin": ["ebitda margin"],
    "dscr": ["dscr", "debt service coverage ratio"],
}

_NUMBER = re.compile(r"\(?\s*(-?[\d,]+\.?\d*)\s*\)?")
_TRAILING_NOTE = re.compile(r"\s*\(note[^)]*\)\s*$", re.IGNORECASE)


@dataclass
class Figure:
    """One extracted number and the line that produced it."""

    field: str
    value: float
    line_number: int
    source_line: str
    caption: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "line": self.line_number,
            "source": self.source_line,
            "caption": self.caption,
            # Deterministically matched from the source text, so unlike an
            # LLM-asserted citation this cannot be a fabrication.
            "verified": True,
        }


@dataclass
class ParseResult:
    figures: Dict[str, Figure] = field(default_factory=dict)
    stated_ratios: Dict[str, float] = field(default_factory=dict)
    unit: str = "Absolute INR"
    unit_multiplier: int = 1
    unmatched_captions: List[str] = field(default_factory=list)

    # -- access ------------------------------------------------------------

    def value(self, name: str) -> Optional[float]:
        f = self.figures.get(name)
        return f.value if f else None

    def sum_of(self, *names: str) -> Optional[float]:
        """Sum the named figures, or None when none of them were found.

        Returning None rather than 0.0 for a total that was never located is
        deliberate: a missing borrowings line means "unknown", and reporting it
        as zero debt would understate leverage.
        """
        present = [self.value(n) for n in names if self.value(n) is not None]
        return sum(present) if present else None

    # -- Schedule III derivations -----------------------------------------

    def total_debt(self) -> Optional[float]:
        """Long-term plus short-term borrowings, per Schedule III.

        Both halves are included whenever either is present. Trade payables,
        deferred tax and provisions are never added - see NOT_FINANCIAL_DEBT.
        """
        return self.sum_of("long_term_debt", "short_term_debt")

    def equity(self) -> Optional[float]:
        return self.sum_of("share_capital", "reserves")

    def ebitda(self) -> Optional[float]:
        """PBT + finance costs + depreciation.

        Requires PBT; the two add-backs default to zero only when PBT itself
        was found, so a wholly unparsed statement reports None rather than 0.
        """
        pbt = self.value("pbt")
        if pbt is None:
            return None
        return pbt + (self.value("finance_costs") or 0.0) + (self.value("depreciation") or 0.0)

    def current_liabilities(self) -> Optional[float]:
        """Prefer the statement's own total; otherwise build it from parts."""
        stated = self.value("current_liabilities")
        if stated is not None:
            return stated
        return self.sum_of("short_term_debt", "trade_payables",
                           "other_current_liabilities")

    def to_financial_data(self) -> Dict[str, Any]:
        """Shape the result for financial_calculator.calculate_financial_ratios."""
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
    def coverage(self) -> float:
        """Fraction of known captions located. A quick read on parse quality."""
        return len(self.figures) / len(CAPTIONS)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def detect_unit(text: str) -> Tuple[int, str]:
    """Read the unit the statement declares for itself.

    Schedule III requires the rounding-off unit to be stated in the heading, so
    this is read rather than inferred from magnitude - inference misreads a
    company reporting absolute rupees as one reporting crores.
    """
    lowered = text.lower()
    if "in lakhs" in lowered or "inr lakhs" in lowered or "rs. in lakhs" in lowered:
        return LAKH, "Lakhs"
    if "in crores" in lowered or "inr crores" in lowered or "rs. in crores" in lowered:
        return CRORE, "Crores"
    if "in thousands" in lowered or "inr thousands" in lowered:
        return THOUSAND, "Thousands"
    return 1, "Absolute INR"


def _caption_and_remainder(line: str) -> Optional[Tuple[str, str]]:
    """Split a statement line into its caption and the text holding the value."""
    if ":" in line:
        caption, remainder = line.split(":", 1)
    else:
        # Layouts without a colon: the caption is the leading run of words and
        # the value is the first number after it.
        m = re.match(r"^([A-Za-z][A-Za-z&()/\.\-\s]{2,60}?)\s{2,}(.+)$", line)
        if not m:
            return None
        caption, remainder = m.group(1), m.group(2)
    caption = _TRAILING_NOTE.sub("", caption).strip().lower()
    return (caption, remainder) if caption else None


def _matches(caption: str, candidates: List[str]) -> Optional[str]:
    for cand in candidates:
        if caption == cand or caption.startswith(cand + " ") or caption.startswith(cand + "("):
            return cand
    return None


def _read_number(remainder: str, multiplier: int) -> Optional[float]:
    m = _NUMBER.search(remainder)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    # Accounting convention: a figure in parentheses is negative.
    if "(" in remainder.split(m.group(1))[0][-2:] and value > 0:
        value = -value
    return value * multiplier


def parse_schedule_iii(text: str) -> ParseResult:
    """Parse an Indian statutory financial statement. No LLM, no network.

    The first match for a field wins. Statements list summary captions before
    their supporting schedules, so the earlier line is the headline figure.
    """
    if not text or not text.strip():
        return ParseResult(unmatched_captions=sorted(CAPTIONS))

    multiplier, unit_name = detect_unit(text)
    result = ParseResult(unit=unit_name, unit_multiplier=multiplier)

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        split = _caption_and_remainder(line)
        if not split:
            continue
        caption, remainder = split

        for name, candidates in CAPTIONS.items():
            if name in result.figures:
                continue
            matched = _matches(caption, candidates)
            if not matched:
                continue
            value = _read_number(remainder, multiplier)
            if value is None:
                continue
            result.figures[name] = Figure(
                field=name, value=value, line_number=line_number,
                source_line=line, caption=matched,
            )
            break

        # Ratios are unitless, so they are read at face value.
        for name, candidates in STATED_RATIOS.items():
            if name in result.stated_ratios:
                continue
            if _matches(caption, candidates):
                value = _read_number(remainder, 1)
                if value is not None:
                    # EBITDA is a currency amount, not a ratio.
                    result.stated_ratios[name] = (
                        value * multiplier if name == "ebitda" else value
                    )
                break

    result.unmatched_captions = sorted(set(CAPTIONS) - set(result.figures))
    return result


def looks_like_schedule_iii(text: str) -> bool:
    """Whether the deterministic path is worth attempting on this document.

    Two independent Schedule III captions is a low bar deliberately: the cost of
    trying and failing is a few milliseconds, while wrongly routing a parseable
    statement to the LLM costs a provider call on every page.
    """
    if not text:
        return False
    lowered = text.lower()
    markers = ("balance sheet", "profit and loss", "revenue from operations",
               "shareholders' funds", "share capital", "trade payables",
               "borrowings", "reserves and surplus")
    return sum(1 for m in markers if m in lowered) >= 2


# ---------------------------------------------------------------------------
# Handoff to the ingestion pipeline
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS = (
    re.compile(r"^\s*(?:company\s*name|name\s*of\s*(?:the\s*)?company|entity\s*name)\s*[:\-]\s*(.+)$",
               re.IGNORECASE | re.MULTILINE),
    # A statutory statement names the entity in its first few lines, usually
    # ending in a recognised corporate suffix.
    re.compile(r"^\s*([A-Z][A-Za-z0-9&'.,\- ]{3,80}?(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|"
               r"Limited|Ltd\.?|LLP|Enterprises|Industries|Traders))\s*$",
               re.MULTILINE),
)


def extract_entity_name(text: str) -> Optional[str]:
    """Read the borrower's registered name without a model.

    Only an explicit caption or a line ending in a corporate suffix counts. A
    wrong name is worse than none - it attaches the appraisal to the wrong
    borrower - so anything ambiguous returns None and the LLM is asked instead.
    """
    if not text:
        return None
    for pattern in _ENTITY_PATTERNS:
        m = pattern.search(text[:2000])
        if m:
            name = _TRAILING_NOTE.sub("", m.group(1)).strip(" .-")
            if 3 < len(name) <= 120:
                return name
    return None


# Fields the ingestion payload expects, and where they come from here.
_PAYLOAD_MAP = {
    "total_revenue": lambda r: r.value("total_revenue"),
    "total_debt": lambda r: r.total_debt(),
    "shareholder_equity": lambda r: r.equity(),
    "current_assets": lambda r: r.value("current_assets"),
    "current_liabilities": lambda r: r.current_liabilities(),
    "ebitda": lambda r: r.ebitda(),
    "pat": lambda r: r.value("pat"),
}

# Without these two, no meaningful credit ratio can be produced, so the
# deterministic pass cannot be said to have carried the document on its own.
CORE_FIELDS = ("total_revenue", "total_debt")


def to_extraction_payload(result: ParseResult, text: str = "") -> Dict[str, Any]:
    """Shape a ParseResult like the ingestion agent's extraction dict.

    Only keys the parser genuinely established are included, so a caller can
    merge this over or under an LLM result without inventing anything. Values
    are already in rupees.
    """
    payload: Dict[str, Any] = {}
    for name, get in _PAYLOAD_MAP.items():
        value = get(result)
        if value is not None:
            payload[name] = value

    name = extract_entity_name(text)
    if name:
        payload["company_name"] = name

    sector = extract_sector(text)
    if sector:
        payload["sector"] = sector

    # Citations in the shape the pipeline already uses, but verified: each one
    # names the line it was matched from rather than asserting a page.
    citations: Dict[str, Any] = {}
    for field_name, alias in (("total_revenue", "revenue"),
                              ("long_term_debt", "debt"),
                              ("share_capital", "equity")):
        fig = result.figures.get(field_name)
        if fig:
            entry = {
                "page": None,
                "line": fig.line_number,
                "snippet": fig.source_line,
                "document": "Schedule III financial statement",
                "location": fig.caption,
                "confidence": "VERIFIED",
                "method": "deterministic",
            }
            citations[alias] = entry
            citations[field_name] = entry
    if citations:
        payload["citations"] = citations

    return payload


def carried_the_document(result: ParseResult) -> bool:
    """Whether the deterministic pass alone yields a usable financial picture."""
    return all(
        (result.total_debt() if f == "total_debt" else result.value(f)) is not None
        for f in CORE_FIELDS
    )


_SECTOR_PATTERN = re.compile(
    r"^\s*(?:industry\s*/?\s*sector|sector|industry|nature\s+of\s+business|"
    r"principal\s+business\s+activity|business\s+activity)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_sector(text: str) -> Optional[str]:
    """Read the declared industry without a model.

    Only an explicit caption counts. Inferring a sector from context is exactly
    the kind of plausible guess that has no place in a credit file, so anything
    unlabelled returns None and the caller leaves it unknown.
    """
    if not text:
        return None
    m = _SECTOR_PATTERN.search(text[:3000])
    if not m:
        return None
    sector = _TRAILING_NOTE.sub("", m.group(1)).strip(" .-")
    return sector if 2 < len(sector) <= 80 else None
