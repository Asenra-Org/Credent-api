import os, pandas as pd
from reportlab.pdfgen import canvas

def make_pdf(path, lines):
    c = canvas.Canvas(path)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()

def create_dossier(folder_name, company_name, is_approve):
    base_path = f'C:/Users/kpvlo/Downloads/{folder_name}'
    os.makedirs(base_path, exist_ok=True)
    
    # PDF 1: Financials
    if is_approve:
        fin_lines = [
            f'{company_name} - Audited Financials 2023-24',
            'Revenue from Operations: 500.00 Cr',
            'Profit After Tax (PAT): 85.50 Cr',
            'Total Shareholder Equity: 200.00 Cr',
            'Long Term Borrowings: 10.00 Cr',
            'Short Term Borrowings: 5.00 Cr',
            'Current Assets: 150.00 Cr',
            'Current Liabilities: 50.00 Cr'
        ]
    else:
        fin_lines = [
            f'{company_name} - Audited Financials 2023-24',
            'Revenue from Operations: 120.00 Cr',
            'Profit After Tax (PAT): -45.50 Cr (LOSS)',
            'Total Shareholder Equity: -10.00 Cr',
            'Long Term Borrowings: 250.00 Cr',
            'Short Term Borrowings: 150.00 Cr',
            'Current Assets: 40.00 Cr',
            'Current Liabilities: 180.00 Cr'
        ]
    make_pdf(f'{base_path}/1_Audited_Financials.pdf', fin_lines)
    
    # PDF 2: GSTR-3B
    gst_lines = [f'GSTR-3B for {company_name}', 'Status: Filed', 'Taxable Value: ' + ('500.00 Cr' if is_approve else '90.00 Cr')]
    make_pdf(f'{base_path}/2_GSTR_3B.pdf', gst_lines)

    # Excel: CMA Data
    df = pd.DataFrame({
        'Particulars': ['Revenue', 'PAT', 'DSCR', 'Current Ratio'],
        'FY23': [400 if is_approve else 150, 70 if is_approve else -20, 3.5 if is_approve else 0.8, 2.5 if is_approve else 0.5],
        'FY24': [500 if is_approve else 120, 85 if is_approve else -45, 4.0 if is_approve else 0.5, 3.0 if is_approve else 0.2]
    })
    df.to_excel(f'{base_path}/3_CMA_Data.xlsx', index=False)

create_dossier('CRESEM_Test_Dossier_Strong_Approve', 'TechCorp Solutions', True)
create_dossier('CRESEM_Test_Dossier_HighRisk_Reject', 'Overleveraged Builders', False)
print('Dossiers created successfully!')

