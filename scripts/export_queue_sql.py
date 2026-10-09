import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cloudos import db

with db.get_conn() as conn:
    c = conn.execute("""
        SELECT conname, contype, pg_get_constraintdef(c.oid) def
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'companies'
    """).fetchall()
    for r in c:
        print(r["conname"], r["contype"], r["def"])
