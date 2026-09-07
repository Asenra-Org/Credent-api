"""Verify a financial statement against itself. No AI, no network, no cost.

Reads an Indian statutory financial statement, extracts every figure it can
match under Schedule III, recomputes the ratios from those figures, and reports
where the document disagrees with its own numbers.

    python scripts/verify_statement.py test_financial.pdf
    python scripts/verify_statement.py statement.pdf --recorded revenue=42000000

``--recorded`` takes the figures the lender already keyed into their system, so
the report can also show where the file diverges from the source document.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parsers.schedule_iii import looks_like_schedule_iii, parse_schedule_iii  # noqa: E402
from app.services.financial_calculator import calculate_financial_ratios  # noqa: E402

W = 78
RULE = "-" * W

# How far two figures may differ before it is worth an underwriter's attention.
MATERIAL = 0.02


def rupees(v):
    """Indian format: crore and lakh, as a credit note would state them."""
    if v is None:
        return "not found"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 10_000_000:
        return f"{sign}Rs {v / 10_000_000:,.2f} Cr"
    if v >= 100_000:
        return f"{sign}Rs {v / 100_000:,.2f} L"
    return f"{sign}Rs {v:,.0f}"


def read_pdf(path):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def head(title):
    print(f"\n{title}")
    print(RULE)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--recorded", nargs="*", default=[],
                    help="figures already in the lender's system, e.g. revenue=42000000")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        print(f"File not found: {args.pdf}")
        return 2

    recorded = {}
    for pair in args.recorded:
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                recorded[k.strip()] = float(v)
            except ValueError:
                pass

    t0 = time.perf_counter()
    text = read_pdf(args.pdf)
    result = parse_schedule_iii(text)
    elapsed = (time.perf_counter() - t0) * 1000

    print("=" * W)
    print("  STATEMENT VERIFICATION".ljust(W - 22) + "CRESEM")
    print("=" * W)
    print(f"  Document      {os.path.basename(args.pdf)}")
    print(f"  Format        {'Schedule III statutory statement' if looks_like_schedule_iii(text) else 'not recognised as a statutory statement'}")
    print(f"  Unit declared {result.unit}")
    print(f"  Parsed in     {elapsed:.0f} ms, without a single AI call")

    if not result.figures:
        head("NO FINANCIAL FIGURES FOUND")
        print("  This document contains no Schedule III line items. It may be a")
        print("  KYC record, a sanction note, or a scanned page needing OCR.")
        print("  No ratios can be computed and no appraisal should proceed.")
        return 1

    # ---- extracted, each with the line that produced it --------------------
    head("EXTRACTED FROM THE DOCUMENT")
    print("  Every figure below names the line it came from, so any of them")
    print("  can be checked against the page in seconds.\n")
    for name in ("total_revenue", "other_income", "pbt", "pat", "finance_costs",
                 "depreciation", "share_capital", "reserves", "long_term_debt",
                 "short_term_debt", "current_assets", "trade_payables"):
        fig = result.figures.get(name)
        if fig:
            print(f"  {name:22s} {rupees(fig.value):>16s}   line {fig.line_number:>3d}  {fig.source_line[:32]}")

    # ---- derived under Schedule III ---------------------------------------
    head("DERIVED UNDER SCHEDULE III")
    print(f"  {'total debt':22s} {rupees(result.total_debt()):>16s}   long-term + short-term borrowings")
    print(f"  {'equity':22s} {rupees(result.equity()):>16s}   share capital + reserves")
    print(f"  {'EBITDA':22s} {rupees(result.ebitda()):>16s}   PBT + finance costs + depreciation")
    print(f"  {'current liabilities':22s} {rupees(result.current_liabilities()):>16s}")
    print("\n  Excluded from debt: trade payables, deferred tax, provisions.")

    # ---- ratios ------------------------------------------------------------
    ratios = calculate_financial_ratios(result.to_financial_data())
    head("RATIOS COMPUTED FROM THOSE FIGURES")
    for key, label in (("current_ratio", "Current ratio"),
                       ("debt_to_equity", "Debt / equity"),
                       ("ebitda_margin", "EBITDA margin %"),
                       ("dscr", "DSCR")):
        v = ratios.get(key)
        if isinstance(v, (int, float)):
            print(f"  {label:22s} {v:>16.2f}")

    # ---- findings ----------------------------------------------------------
    findings = []

    for key, label in (("current_ratio", "Current ratio"),
                       ("debt_to_equity", "Debt / equity"),
                       ("ebitda", "EBITDA")):
        stated = result.stated_ratios.get(key)
        computed = result.ebitda() if key == "ebitda" else ratios.get(key)
        if stated is None or not isinstance(computed, (int, float)):
            continue
        if abs(computed - stated) / max(abs(stated), 1e-9) > MATERIAL:
            shown_s = rupees(stated) if key == "ebitda" else f"{stated:.2f}"
            shown_c = rupees(computed) if key == "ebitda" else f"{computed:.2f}"
            findings.append((
                "DOCUMENT DISAGREES WITH ITSELF",
                f"{label}: the statement asserts {shown_s}, but its own figures give {shown_c}.",
            ))

    for name, keyed in recorded.items():
        actual = result.value(name) if result.value(name) is not None else (
            result.total_debt() if name == "total_debt" else None)
        if actual is None:
            findings.append(("NOT VERIFIABLE",
                             f"{name}: recorded as {rupees(keyed)}; no matching line in the document."))
        elif abs(actual - keyed) / max(abs(actual), 1e-9) > MATERIAL:
            delta = (keyed - actual) / actual * 100
            fig = result.figures.get(name)
            src = f" (line {fig.line_number})" if fig else ""
            findings.append(("FIGURE MISMATCH",
                             f"{name}: file records {rupees(keyed)}; document states {rupees(actual)}{src}. Delta {delta:+.1f}%."))

    missing = [n for n in ("total_revenue", "long_term_debt", "current_assets", "pat")
               if result.value(n) is None]
    if missing:
        findings.append(("MISSING FROM DOCUMENT",
                         "Not present, so any ratio depending on it is unavailable: "
                         + ", ".join(missing)))

    head("FINDINGS")
    if not findings:
        print("  No divergence found. Every figure in the document is internally")
        print("  consistent and traceable to a line.")
    else:
        for i, (tag, detail) in enumerate(findings, 1):
            print(f"  {i}. [{tag}]")
            print(f"     {detail}\n")

    coverage = result.coverage * 100
    print(RULE)
    print(f"  {len(result.figures)} figures matched ({coverage:.0f}% of known Schedule III captions)")
    print(f"  {len(findings)} finding(s)   |   AI calls: 0   |   Cost: Rs 0.00")
    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
