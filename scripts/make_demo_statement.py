"""Build one complete Schedule III statement for demo and testing.

Every caption the parser knows is present, so a single upload exercises the
whole extraction path. Figures are internally consistent - the balance sheet
balances and the ratios follow from the line items - except for one deliberate
discrepancy in the stated current ratio, which is what the verification report
is meant to catch.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import os
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "tests", "fixtures", "schedule_iii_complete.pdf")

# All figures in INR Lakhs.
LINES = [
    ("H1", "AUDITED FINANCIAL STATEMENTS"),
    ("H2", "Sahyadri Precision Castings Private Limited"),
    ("H2", "FY 2024-25"),
    ("", ""),
    ("H3", "COMPANY INFORMATION"),
    ("", "Company Name: Sahyadri Precision Castings Private Limited"),
    ("", "CIN: U27310MH2014PTC258871"),
    ("", "Registered Office: Plot 22, MIDC Shiroli, Kolhapur - 416122, Maharashtra"),
    ("", "Industry/Sector: Manufacturing (Precision Castings & Auto Components)"),
    ("", "Date of Incorporation: 11 June 2014"),
    ("", "Promoters: Anil Deshmukh (55%), Sunita Deshmukh (30%), Rohan Deshmukh (15%)"),
    ("", "Statutory Auditor: M/s Kulkarni & Associates, Chartered Accountants"),
    ("", "Banker: Bank of Maharashtra, Shiroli Branch"),
    ("", ""),
    ("H3", "STATEMENT OF PROFIT AND LOSS (INR Lakhs)"),
    ("", "Revenue from Operations: 3,842.00"),
    ("", "Other Income: 58.00"),
    ("", "Total Income: 3,900.00"),
    ("", "Cost of Materials Consumed: 2,196.00"),
    ("", "Changes in Inventories: -42.00"),
    ("", "Employee Benefit Expenses: 386.00"),
    ("", "Finance Costs: 148.00"),
    ("", "Depreciation and Amortisation: 214.00"),
    ("", "Other Expenses: 512.00"),
    ("", "Total Expenses: 3,414.00"),
    ("", "Profit Before Tax: 486.00"),
    ("", "Tax Expense: 128.00"),
    ("", "Profit for the Year: 358.00"),
    ("", ""),
    ("H3", "BALANCE SHEET AS AT 31 MARCH 2025 (INR Lakhs)"),
    ("H4", "EQUITY AND LIABILITIES"),
    ("", "Share Capital: 320.00"),
    ("", "Reserves and Surplus: 1,486.00"),
    ("", "Long Term Borrowings: 742.00"),
    ("", "Short Term Borrowings: 418.00"),
    ("", "Deferred Tax Liabilities: 342.00"),
    ("", "Long Term Provisions: 178.00"),
    ("", "Trade Payables: 496.00"),
    ("", "Other Current Liabilities: 132.00"),
    ("", "Total Current Liabilities: 1,046.00"),
    ("", "Total Equity and Liabilities: 4,114.00"),
    ("", ""),
    ("H4", "ASSETS"),
    ("", "Fixed Assets (Net): 1,868.00"),
    ("", "Inventories: 684.00"),
    ("", "Trade Receivables: 1,092.00"),
    ("", "Cash and Bank Balances: 214.00"),
    ("", "Other Current Assets: 256.00"),
    ("", "Total Current Assets: 2,246.00"),
    ("", "Total Assets: 4,114.00"),
    ("", ""),
    ("H3", "KEY FINANCIAL RATIOS (as stated by management)"),
    # 2246 / 1046 = 2.147. Management states 2.35, computed from a current
    # liabilities figure that excludes short-term borrowings. The verification
    # report exists to surface exactly this.
    ("", "Current Ratio: 2.35"),
    ("", "Debt to Equity Ratio: 0.64"),
    ("", "EBITDA: 848.00 Lakhs"),
    ("", "EBITDA Margin: 22.1%"),
    ("", "Net Profit Margin: 9.3%"),
    ("", "DSCR: 2.14"),
    ("", "Return on Capital Employed: 19.8%"),
    ("", ""),
    ("H3", "CREDIT FACILITY REQUESTED"),
    ("", "Facility Type: Term Loan and Working Capital"),
    ("", "Amount Requested: INR 900 Lakhs"),
    ("", "Purpose: Induction furnace capacity expansion and working capital"),
    ("", "Tenor: 7 years with 12 month moratorium"),
    ("", "Security: Hypothecation of plant and machinery, personal guarantee"),
    ("", "Existing Lenders: Bank of Maharashtra (CC 400L), Bajaj Finance (TL 180L)"),
    ("", ""),
    ("H3", "COMPLIANCE"),
    ("", "GSTIN: 27AAECS4471K1ZP"),
    ("", "GST Returns: GSTR-3B filed up to March 2025, no defaults"),
    ("", "CIBIL Commercial Rank: CMR-3"),
    ("", "EPFO/ESIC: No defaults reported"),
    ("", "SMA Classification: Standard, no SMA-0/1/2 flags"),
]


def build():
    c = canvas.Canvas(OUT, pagesize=A4)
    width, height = A4
    left, y = 20 * mm, height - 22 * mm

    for kind, text in LINES:
        if y < 24 * mm:
            c.showPage()
            y = height - 22 * mm
        if kind == "H1":
            c.setFont("Helvetica-Bold", 14); y -= 2
        elif kind == "H2":
            c.setFont("Helvetica-Bold", 11)
        elif kind == "H3":
            y -= 3
            c.setFont("Helvetica-Bold", 10.5)
        elif kind == "H4":
            c.setFont("Helvetica-Bold", 9.5)
        else:
            c.setFont("Helvetica", 9.5)
        c.drawString(left, y, text)
        y -= 13 if kind in ("H1", "H2", "H3") else 11.5

    c.save()
    print(f"written: {OUT}")


if __name__ == "__main__":
    build()
