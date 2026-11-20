"""Reset the local manager login (ID + password) to default / environment values.
Run from the agap folder:  python reset_manager.py
"""
import os
import sqlite3
from werkzeug.security import generate_password_hash

import server  # creates the database automatically if it does not exist

LOGIN_ID = os.environ.get("MANAGER_ID", "tirth").strip() or "tirth"
PASSWORD = os.environ.get("MANAGER_PASSWORD", "tirth@1234")

conn = sqlite3.connect(server.DB_PATH)
try:
    conn.execute("UPDATE manager SET login_id=?, pw_hash=? WHERE id=1", (LOGIN_ID, generate_password_hash(PASSWORD)))
    conn.execute("DELETE FROM sessions")
    conn.commit()
    print("Manager login reset thai gayu.")
    print("Manager ID:", LOGIN_ID)
    print("Password:", "(MANAGER_PASSWORD environment ma set che)" if "MANAGER_PASSWORD" in os.environ else "tirth@1234")
finally:
    conn.close()
