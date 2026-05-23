# scripts/apply_schema.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from pipeline.config import DATABASE_URL

sql = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
print(f"Applying schema to: {DATABASE_URL}")
with psycopg2.connect(DATABASE_URL) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
        # Add columns to existing tables (safe to re-run)
        for stmt in [
            "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS difficulty VARCHAR(50)",
            "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS tags TEXT[]",
            "ALTER TABLE child_profiles ADD COLUMN IF NOT EXISTS avg_response_time_sec REAL",
        ]:
            cur.execute(stmt)
print("Done.")
