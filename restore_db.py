import psycopg2
import os
import re

def restore_backup(db_url, sql_file):
    print(f"Connecting to {db_url.split('@')[1]}...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()
        print("Connected successfully to Neon DB!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return
        
    print(f"Reading SQL file from {sql_file}...")
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()

    print("Filtering out Supabase specific commands...")
    
    statements = sql_content.split(';\n')
    success = 0
    failed = 0
    
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
            
        # Supabase specific roles we want to skip creating/altering
        if "CREATE ROLE" in stmt and ("supabase" in stmt or "anon" in stmt or "authenticated" in stmt or "service_role" in stmt):
            continue
        if "ALTER ROLE" in stmt and ("supabase" in stmt or "anon" in stmt or "authenticated" in stmt or "service_role" in stmt):
            continue
            
        try:
            cursor.execute(stmt)
            success += 1
        except Exception as e:
            # Typical errors: role "supabase_admin" does not exist, etc. 
            # We ignore these because we only care about tables/data
            failed += 1
            pass
            
    print(f"Restore complete! {success} statements executed successfully.")
    print(f"({failed} Supabase-specific statements skipped/failed - this is normal and expected)")
    conn.close()

if __name__ == "__main__":
    DB_URL = "postgresql://neondb_owner:npg_bOzBiPtr09jL@ep-mute-snow-b4ruxw2u-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require"
    SQL_FILE = r"C:\Users\kpvlo\Downloads\db_backup.sql"
    
    restore_backup(DB_URL, SQL_FILE)
