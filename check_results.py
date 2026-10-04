import sqlite3, json

conn = sqlite3.connect('app/database/credent.db')
c = conn.cursor()

date_filter = "2026-09-30"

# All today's cases
c.execute("SELECT case_id, result_data, created_at FROM loan_cases WHERE created_at >= ? ORDER BY created_at ASC", (date_filter,))
cases = c.fetchall()
print(f"TOTAL CASES TODAY: {len(cases)}")
print()

for row in cases:
    data = json.loads(row[1])
    ai = data.get('ai_analysis', {})
    cam = data.get('combined_decision', {})
    es = cam.get('executive_summary', {})
    print(f"case_id  : {row[0]}")
    print(f"Company  : {ai.get('company_name','N/A')}")
    print(f"Sector   : {ai.get('sector','N/A')}")
    print(f"Revenue  : {ai.get('total_revenue','N/A')}")
    print(f"EBITDA   : {ai.get('ebitda','N/A')}")
    print(f"PAT      : {ai.get('pat','N/A')}")
    print(f"NetWorth : {ai.get('shareholder_equity','N/A')}")
    print(f"Score    : {data.get('adjusted_score','N/A')}")
    print(f"Decision : {cam.get('decision','N/A')}")
    print(f"ES Rev   : {es.get('revenue','N/A')}")
    print(f"Strengths: {es.get('strengths',[])}")
    print(f"Concerns : {es.get('key_concerns',[])}")
    print(f"Created  : {row[2]}")
    print()

# Today's LLM calls
c.execute("""
    SELECT agent, model, prompt_tokens, completion_tokens, total_tokens, latency_ms, estimated_cost_inr, error_kind, succeeded, created_at
    FROM llm_call_log
    WHERE created_at >= ?
    ORDER BY rowid ASC
""", (date_filter,))
llm_rows = c.fetchall()

print(f"=== LLM CALLS TODAY ({len(llm_rows)} total) ===")
for r in llm_rows:
    agent, model, pt, ct, tt, lat, cost, err, ok, ts = r
    status = "OK" if ok else f"FAIL({err})"
    print(f"  [{status}] {agent or 'unattributed'} | pt={pt} ct={ct} tt={tt} lat={lat}ms | {ts}")

tp = sum(r[2] for r in llm_rows if r[2])
tc = sum(r[3] for r in llm_rows if r[3])
tt_total = sum(r[4] for r in llm_rows if r[4])
tl = sum(r[5] for r in llm_rows if r[5])
failed = sum(1 for r in llm_rows if r[7])
ok_count = sum(1 for r in llm_rows if r[8] == 1)

# Sarvam pricing: approx Rs 0.3 per 1000 tokens (estimated)
# sarvam-105b: input ~Rs 0.10/1k, output ~Rs 0.30/1k
cost_estimate = (tp * 0.10 / 1000) + (tc * 0.30 / 1000)

print()
print("=== COST SUMMARY ===")
print(f"Total LLM Calls   : {len(llm_rows)}")
print(f"  Succeeded        : {ok_count}")
print(f"  Failed/Timeout   : {failed}")
print(f"Prompt Tokens      : {tp:,}")
print(f"Completion Tokens  : {tc:,}")
print(f"Total Tokens       : {tt_total:,}")
print(f"Total Latency      : {tl/1000:.1f}s ({tl/60000:.1f} min)")
print(f"Est. Cost (INR)    : Rs {cost_estimate:.4f}  (~{cost_estimate*100:.2f} paise)")
print(f"Of your Rs 1000    : {cost_estimate/1000*100:.4f}% used today")
