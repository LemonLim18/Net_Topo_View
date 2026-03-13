
import sqlite3
import os
from pathlib import Path

DB_PATH = "verification_test.db"

def setup_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY, hostname TEXT)")
    conn.execute("CREATE TABLE change_requests (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, request_id INTEGER REFERENCES change_requests(id), device_id INTEGER NOT NULL REFERENCES devices(id))")
    return conn

def resolve_device_id(conn, target_hosts):
    device_id = 0
    # 1. Try first target host
    if target_hosts:
        row = conn.execute(
            "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
            (target_hosts[0],),
        ).fetchone()
        if row:
            device_id = row[0]
    
    # 2. Fallback to any device if still not resolved
    if device_id == 0:
        row = conn.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
        if row:
            device_id = row[0]
            
    return device_id

def test_resolution():
    conn = setup_db()
    
    # Test 1: Empty DB
    print("Test 1: Empty DB - Expected: 0")
    print(f"Result: {resolve_device_id(conn, ['any'])}")
    
    # Test 2: Matching Host
    conn.execute("INSERT INTO devices (id, hostname) VALUES (26, 'Acc_Cisco_1')")
    print("Test 2: Matching Host - Expected: 26")
    print(f"Result: {resolve_device_id(conn, ['Acc_Cisco_1'])}")
    
    # Test 3: Fallback (host not found)
    print("Test 3: Fallback (host not found) - Expected: 26")
    print(f"Result: {resolve_device_id(conn, ['all'])}")
    
    # Test 4: Another device fallback
    conn.execute("INSERT INTO devices (id, hostname) VALUES (10, 'Dist_Cisco_1')")
    # Note: ORDER BY id LIMIT 1 should give 10
    print("Test 4: Fallback to lowest ID - Expected: 10")
    print(f"Result: {resolve_device_id(conn, ['nonexistent'])}")
    
    conn.close()
    os.remove(DB_PATH)

if __name__ == "__main__":
    test_resolution()
