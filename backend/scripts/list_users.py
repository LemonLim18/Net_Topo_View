#!/usr/bin/env python3
"""List users in backend SQLite database for debugging/login checks."""
import sqlite3
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'db' / 'netauto.db'

if not DB.exists():
    print('DB not found at', DB)
    raise SystemExit(1)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
try:
    rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    if not rows:
        print('No users found in users table.')
    else:
        print('Users:')
        for r in rows:
            print(f" - id={r['id']} username={r['username']} role={r['role']} created_at={r['created_at']}")
except Exception as e:
    print('Error reading users table:', e)
finally:
    conn.close()
