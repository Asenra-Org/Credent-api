import sqlite3, json

conn = sqlite3.connect('app/database/credent.db')
c = conn.cursor()
c.execute("SELECT case_id, status, result_data, created_at FROM loan_cases WHERE case_id LIKE '%1790754041003%' ORDER BY created_at ASC")
rows = c.fetchall()
print(f"Cases in batch 1790754041003: {len(rows)}")
for r in rows:
    data = json.loads(r[2]) if r[2] else {}
    ai = data.get('ai_analysis', {})
    cam = data.get('combined_decision', {})
    comp = ai.get('company_name', 'N/A') if isinstance(ai, dict) else 'N/A'
    dec = cam.get('decision', 'N/A') if isinstance(cam, dict) else 'N/A'
    print(f"  {r[0]} | status: {r[1]} | company: {comp} | decision: {dec} | created: {r[3]}")
