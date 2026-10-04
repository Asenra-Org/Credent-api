import pypdf
reader = pypdf.PdfReader('C:/Users/kpvlo/Downloads/CRESEM_Test_Dossier_RolexRings/1_Audited_Financials_and_Profile.pdf')
for page in reader.pages:
    print(page.extract_text())
