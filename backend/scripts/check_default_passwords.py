#!/usr/bin/env python3
"""Check whether 'admin' and 'engineer' accounts still use default passwords."""
import sqlite3
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'db' / 'netauto.db'

if not DB.exists():
    print('DB not found:', DB)
    raise SystemExit(1)

import importlib.util
spec = importlib.util.spec_from_file_location('auth_auth', str(ROOT / 'auth' / 'auth.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def get_hash(username):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT username, password_hash FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return row['password_hash'] if row else None

for user, pwd in [('admin', 'admin'), ('engineer', 'engineer')]:
    h = get_hash(user)
    if not h:
        print(f"User {user} not found")
        continue
    ok = mod.verify_password(pwd, h)
    print(f"User {user} uses default password '{pwd}': {ok}")
