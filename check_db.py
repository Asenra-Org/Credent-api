import sqlite3, json
conn = sqlite3.connect('app/database/credent.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM appraisal_records')
ncols = [c[0] for c in cursor.description]
errs=0
for row in cursor.fetchall():
  record=dict(zip(ncols, row))
  for f in ['raw_document_data', 'integrity_flags', 'web_research', 'cam_report', 'financial_ratios', 'promoter_analysis', 'governance_assessment']:
    try:
      if record.get(f): json.loads(record[f])
    except Exception as e:
      print(f'Error {record["id"]} {f}: {e}')
      errs+=1
print('Errors:', errs)
