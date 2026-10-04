import psycopg2
conn = psycopg2.connect('postgresql://neondb_owner:npg_bOzBiPtr09jL@ep-mute-snow-b4ruxw2u-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require')
cur = conn.cursor()
cur.execute("SELECT sum(prompt_tokens), sum(completion_tokens), sum(estimated_cost_inr), count(*) FROM llm_call_log WHERE case_id = 'case-task-1791108236814-0-tcnno-1791108236828'")
print(cur.fetchone())
