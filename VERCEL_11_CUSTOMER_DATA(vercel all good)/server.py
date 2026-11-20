"""Active Genuine Fiber Parts - backend (Flask + SQLite).
Customer app: /        Manager app: /manager        API: /api/...
"""
import os, re, json, gzip, time, secrets, hashlib, threading
import urllib.request, urllib.parse
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
# Auto-load .env file if present
_env_file = os.path.join(BASE, ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, encoding="utf-8") as _ef:
            for _line in _ef:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip("'\"")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
CATALOG_PATH = os.path.join(BASE, "data", "catalog.json")   # always shipped with the project
DEV_OTP = os.environ.get("DEV_OTP", "1") == "1"            # 1 = OTP screen par batave (demo). Production ma 0 karo.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()          # Google login mate (README joo)
FAST2SMS_KEY = os.environ.get("FAST2SMS_KEY", "").strip()                  # real SMS OTP mate (optional)
CUST_SESSION_DAYS = 365                                                      # customer ek var login kare to 1 varsh sudhi login rahe
MANAGER_PHONE = "9998040666"                                # FIXED - profile ma badli shakay nahi
IST = timezone(timedelta(hours=5, minutes=30))
STATUSES = ("New", "Confirmed", "Delivered", "Cancelled")

app = Flask(__name__, static_folder=None)
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
except Exception:
    pass
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS manager(
  id INTEGER PRIMARY KEY CHECK(id=1), login_id TEXT NOT NULL, pw_hash TEXT NOT NULL,
  name TEXT NOT NULL, phone TEXT NOT NULL, photo TEXT NOT NULL DEFAULT '',
  shop_open INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS otps(
  purpose TEXT NOT NULL, phone TEXT NOT NULL, code_hash TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(purpose, phone));
CREATE TABLE IF NOT EXISTS otp_log(phone TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS proofs(token TEXT PRIMARY KEY, purpose TEXT NOT NULL, phone TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS items(
  id BIGINT PRIMARY KEY, article TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
  company TEXT NOT NULL, model TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', price INTEGER);
CREATE TABLE IF NOT EXISTS orders(
  id BIGSERIAL PRIMARY KEY, token TEXT NOT NULL, name TEXT NOT NULL,
  total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'New', created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL, customer_id BIGINT);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status, created_at);
CREATE TABLE IF NOT EXISTS order_items(
  id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  item_id BIGINT NOT NULL, article TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
  company TEXT NOT NULL, model TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL, price INTEGER);
CREATE INDEX IF NOT EXISTS ix_oi_order ON order_items(order_id);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS customers(
  id BIGSERIAL PRIMARY KEY, phone TEXT UNIQUE, google_sub TEXT UNIQUE, email TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL, last_login DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS cust_sessions(
  token TEXT PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS deleted_items(id BIGINT PRIMARY KEY, deleted_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS custom_items(
  id BIGSERIAL PRIMARY KEY, article TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, category TEXT NOT NULL,
  company TEXT NOT NULL, model TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', price INTEGER,
  created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL);
"""

class HybridRow(dict):
    """PostgreSQL row supporting both SQLite-style name and numeric indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)
    def __iter__(self):
        return iter(self.values())

class CursorCompat:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
    @property
    def rowcount(self): return self._cur.rowcount
    def fetchone(self):
        row = self._cur.fetchone()
        return HybridRow(row) if row is not None else None
    def fetchall(self): return [HybridRow(r) for r in self._cur.fetchall()]
    def __iter__(self):
        for row in self._cur:
            yield HybridRow(row)

class ConnectionCompat:
    def __init__(self, conn): self._conn = conn
    def execute(self, sql, params=()):
        sql = sql.replace('?', '%s')
        match = re.match(r"\s*INSERT\s+INTO\s+(orders|custom_items|customers)\b", sql, re.I)
        cur = self._conn.cursor()
        if match and 'RETURNING' not in sql.upper():
            cur.execute(sql + ' RETURNING id', params)
            row = cur.fetchone()
            return CursorCompat(cur, row['id'] if row else None)
        cur.execute(sql, params)
        return CursorCompat(cur)
    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        cur.executemany(sql.replace('?', '%s'), seq)
        return CursorCompat(cur)
    def commit(self): self._conn.commit()
    def close(self): self._conn.close()
    def rollback(self): self._conn.rollback()


def connect_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL is missing. Add your Neon PostgreSQL connection string in Vercel Environment Variables.')
    return ConnectionCompat(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10))


def db():
    if 'db' not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop('db', None)
    if c:
        c.close()


_base_cat = {}


def load_catalog_file():
    if "d" not in _base_cat:
        with open(CATALOG_PATH, encoding="utf-8") as fh:
            _base_cat["d"] = json.load(fh)
    return _base_cat["d"]


def init_db():
    """Create PostgreSQL schema and seed the bundled catalogue without wiping live data."""
    c = connect_db()
    try:
        raw = c._conn
        with raw.cursor() as cur:
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    cur.execute(statement)
        # Keep compatibility with an earlier PostgreSQL deployment missing this field.
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT")
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS google_sub TEXT")
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''")
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS name TEXT DEFAULT ''")
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS created_at DOUBLE PRECISION")
        c.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_login DOUBLE PRECISION")
        c.execute("UPDATE customers SET created_at = EXTRACT(EPOCH FROM NOW()) WHERE created_at IS NULL")
        c.execute("UPDATE customers SET last_login = created_at WHERE last_login IS NULL")
        c.execute("UPDATE customers SET name = '' WHERE name IS NULL")
        c.execute("UPDATE customers SET phone = '' WHERE phone IS NULL")
        c.execute("UPDATE customers SET email = '' WHERE email IS NULL")

        c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_id BIGINT")
        c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at DOUBLE PRECISION")
        c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at DOUBLE PRECISION")
        c.execute("UPDATE orders SET created_at = EXTRACT(EPOCH FROM NOW()) WHERE created_at IS NULL")
        c.execute("UPDATE orders SET updated_at = created_at WHERE updated_at IS NULL")
        c.execute("CREATE INDEX IF NOT EXISTS ix_orders_cust ON orders(customer_id, created_at)")
        c.execute("UPDATE orders SET status='Confirmed' WHERE status='New'")
        row = c.execute("SELECT login_id, pw_hash FROM manager WHERE id=1").fetchone()
        if not row:
            pw = os.environ.get("MANAGER_PASSWORD", "").strip()
            if len(pw) < 12:
                raise RuntimeError('Set MANAGER_PASSWORD (at least 12 characters) in Vercel Environment Variables before first deployment.')
            c.execute("INSERT INTO manager(id,login_id,pw_hash,name,phone) VALUES(1,?,?,?,?)", (
                (os.environ.get("MANAGER_ID", "tirth").strip() or "tirth"), generate_password_hash(pw),
                os.environ.get("MANAGER_NAME", "Hardik Viramgama"), MANAGER_PHONE))
        c.execute("UPDATE manager SET name=? WHERE id=1 AND name='Tirth Viramgama'", ("Hardik Viramgama",))
        c.execute("UPDATE manager SET phone=? WHERE id=1", (MANAGER_PHONE,))
        # Import catalogue only when the table is empty; never erase manager changes on cold starts.
        if not c.execute("SELECT 1 FROM items LIMIT 1").fetchone():
            cat = load_catalog_file()
            c.executemany("INSERT INTO items(id,article,name,category,company,model,unit,price) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", cat["items"])
        # Restore manager-created products and deleted catalogue markers from persistent tables.
        c.execute("INSERT INTO items(id,article,name,category,company,model,unit,price) SELECT id,article,name,category,company,model,unit,price FROM custom_items ON CONFLICT(id) DO UPDATE SET article=EXCLUDED.article,name=EXCLUDED.name,category=EXCLUDED.category,company=EXCLUDED.company,model=EXCLUDED.model,unit=EXCLUDED.unit,price=EXCLUDED.price")
        c.execute("DELETE FROM items WHERE id IN (SELECT id FROM deleted_items)")
        c.execute("DELETE FROM cust_sessions WHERE expires<?", (time.time(),))
        # Preserve legacy numbering: manager-created products start above one million; orders above 1000.
        c.execute("SELECT setval(pg_get_serial_sequence('custom_items','id'), GREATEST(COALESCE((SELECT MAX(id) FROM custom_items),1000000),1000000), true)")
        c.execute("SELECT setval(pg_get_serial_sequence('orders','id'), GREATEST(COALESCE((SELECT MAX(id) FROM orders),1000),1000), true)")
        c.execute("SELECT setval(pg_get_serial_sequence('customers','id'), GREATEST(COALESCE((SELECT MAX(id) FROM customers),1),1), true)")
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ---------------- helpers ----------------
def err(msg, code=400):
    return jsonify(error=msg), code


def body():
    return request.get_json(silent=True) or {}


def now():
    return time.time()


def parse_ts(ts):
    if ts is None or ts == "":
        return None
    try:
        val = float(ts)
        if val <= 0:
            return None
        if val > 1e11:  # milliseconds to seconds
            val = val / 1000.0
        return val
    except (ValueError, TypeError):
        return None


def fmt_time(ts):
    val = parse_ts(ts)
    if val is None:
        return ""
    try:
        return datetime.fromtimestamp(val, IST).strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return ""


def fmt_date(ts):
    val = parse_ts(ts)
    if val is None:
        return ""
    try:
        return datetime.fromtimestamp(val, IST).strftime("%d %b %Y")
    except Exception:
        return ""


def fmt_clock(ts):
    val = parse_ts(ts)
    if val is None:
        return ""
    try:
        return datetime.fromtimestamp(val, IST).strftime("%I:%M %p")
    except Exception:
        return ""


def order_json(o, items):
    items = items or []
    amount = sum((x.get("price") or 0) * (x.get("qty") or 0) for x in items if x.get("price") is not None)
    ts = o.get("created_at") if isinstance(o, dict) else getattr(o, "created_at", None)
    return {"id": str(o.get("id") or ""), "name": o.get("name") or o.get("cname") or "", "total": o.get("total") or 0, "amount": amount, "status": o.get("status") or "New",
            "phone": o.get("cphone") or "", "email": o.get("cemail") or "",
            "ts": int(float(ts) * 1000) if parse_ts(ts) else 0, "time": fmt_time(ts) if parse_ts(ts) else "",
            "items": [{"i": x.get("item_id"), "a": x.get("article") or "", "n": x.get("name") or "", "cat": x.get("category") or "", "co": x.get("company") or "",
                       "m": x.get("model") or "", "u": x.get("unit") or "", "q": x.get("qty") or 0, "p": x.get("price")} for x in items]}


def load_orders(where="", args=()):
    try:
        rows = db().execute(f"SELECT o.*, c.phone AS cphone, c.email AS cemail, c.name AS cname FROM orders o "
                            f"LEFT JOIN customers c ON c.id=o.customer_id {where}", args).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows if r and r.get("id")]
        if not ids:
            return []
        its = db().execute(f"SELECT * FROM order_items WHERE order_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall()
        by = {}
        for i in its:
            by.setdefault(i["order_id"], []).append(i)
        return [order_json(r, by.get(r["id"], [])) for r in rows if r]
    except Exception as e:
        app.logger.error(f"Error in load_orders: {e}")
        return []


def hash_code(phone, purpose, code):
    return hashlib.sha256(f"{purpose}:{phone}:{code}".encode()).hexdigest()


def send_sms(phone, text, code=None):
    """Real SMS: FAST2SMS_KEY environment set karo (Fast2SMS 'otp' route). Nahi to console ma print thay che."""
    print(f"[SMS -> {phone}] {text}")
    if FAST2SMS_KEY and code:
        try:
            q = urllib.parse.urlencode({"authorization": FAST2SMS_KEY, "variables_values": code, "route": "otp", "numbers": phone})
            urllib.request.urlopen("https://www.fast2sms.com/dev/bulkV2?" + q, timeout=10).read()
        except Exception as e:
            print("SMS moklava ma error:", e)


def issue_otp(purpose, phone):
    t = now(); d = db()
    d.execute("DELETE FROM otp_log WHERE ts<?", (t - 3600,))
    if d.execute("SELECT COUNT(*) FROM otp_log WHERE phone=?", (phone,)).fetchone()[0] >= 5:
        return None, "Ghana OTP magya, 1 kalak pachi try karo"
    code = str(secrets.randbelow(900000) + 100000)
    d.execute("INSERT INTO otps(purpose,phone,code_hash,expires,attempts) VALUES(?,?,?,?,0) ON CONFLICT(purpose,phone) DO UPDATE SET code_hash=EXCLUDED.code_hash,expires=EXCLUDED.expires,attempts=0", (purpose, phone, hash_code(phone, purpose, code), t + 300))
    d.execute("INSERT INTO otp_log VALUES(?,?)", (phone, t))
    d.commit()
    send_sms(phone, f"Your OTP is {code}. Valid for 5 minutes.", code)
    return code, None


def check_otp(purpose, phone, code):
    d = db()
    r = d.execute("SELECT * FROM otps WHERE purpose=? AND phone=?", (purpose, phone)).fetchone()
    if not r or r["expires"] < now() or r["attempts"] >= 5:
        return False
    d.execute("UPDATE otps SET attempts=attempts+1 WHERE purpose=? AND phone=?", (purpose, phone))
    ok = secrets.compare_digest(r["code_hash"], hash_code(phone, purpose, str(code).strip()))
    if ok:
        d.execute("DELETE FROM otps WHERE purpose=? AND phone=?", (purpose, phone))
    d.commit()
    return ok


def make_proof(purpose, phone, ttl=1800):
    tok = secrets.token_urlsafe(24)
    db().execute("INSERT INTO proofs VALUES(?,?,?,?)", (tok, purpose, phone, now() + ttl))
    db().commit()
    return tok


def use_proof(tok, purpose, phone=None, consume=True):
    r = db().execute("SELECT * FROM proofs WHERE token=?", (tok or "",)).fetchone()
    if not r or r["purpose"] != purpose or r["expires"] < now() or (phone and r["phone"] != phone):
        return None
    if consume:
        db().execute("DELETE FROM proofs WHERE token=?", (tok,)); db().commit()
    return r


def manager():
    return db().execute("SELECT * FROM manager WHERE id=1").fetchone()


_fails = {}
_hits = {}
_lock = threading.Lock()


def throttled(key, limit=5, window=900):
    with _lock:
        t = now()
        _fails[key] = [x for x in _fails.get(key, []) if x > t - window]
        return len(_fails[key]) >= limit


def add_fail(key):
    with _lock:
        _fails.setdefault(key, []).append(now())


def rate_ok(key, limit, window):
    with _lock:
        t = now()
        _hits[key] = [x for x in _hits.get(key, []) if x > t - window]
        if len(_hits[key]) >= limit:
            return False
        _hits[key].append(t)
        return True


def mgr_required(f):
    @wraps(f)
    def w(*a, **k):
        tok = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        r = db().execute("SELECT expires FROM sessions WHERE token=?", (tok,)).fetchone()
        if not tok or not r or r["expires"] < now():
            return err("Login required", 401)
        return f(*a, **k)
    return w


# ---------------- shop settings (manager profile ma thi badlay) ----------------
SETTING_KEYS = {"shop_name": 80, "address": 200, "gstin": 15, "email": 80, "whatsapp": 10, "timing": 60, "bill_note": 120, "closed_msg": 120}
SETTING_DEFAULTS = {"shop_name": "Active Genuine Fiber Parts", "bill_note": "Thank you for your order!"}


def get_settings():
    saved = {r["key"]: r["value"] for r in db().execute("SELECT key,value FROM settings")}
    return {k: (saved[k] if k in saved else SETTING_DEFAULTS.get(k, "")) for k in SETTING_KEYS}


@app.get("/api/shop")
def shop():
    st = get_settings()
    return jsonify(open=bool(manager()["shop_open"]), name=st["shop_name"], address=st["address"],
                   whatsapp=st["whatsapp"], timing=st["timing"], closedMsg=st["closed_msg"])


# ---------------- catalog (Excel items + manager e add kareli items) ----------------
_cat_cache = {}


def catalog_data():
    d = db()
    ver = tuple(d.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(updated_at),0) FROM custom_items").fetchone()) + \
        tuple(d.execute("SELECT COUNT(*), COALESCE(MAX(deleted_at),0) FROM deleted_items").fetchone())
    with _lock:
        if _cat_cache.get("ver") != ver:
            base = load_catalog_file()
            gone = {r[0] for r in d.execute("SELECT id FROM deleted_items")}
            items = [x for x in base["items"] if x[0] not in gone]
            cats, cos = list(base["cats"]), list(base["companies"])
            for r in d.execute("SELECT * FROM custom_items ORDER BY id"):
                items.append([r["id"], r["article"], r["name"], r["category"], r["company"], r["model"], r["unit"], r["price"]])
                if r["category"] not in cats:
                    cats.append(r["category"])
                if r["company"] not in cos:
                    cos.append(r["company"])
            used_c = {x[3] for x in items}; used_o = {x[4] for x in items}
            cats = [c for c in cats if c in used_c]; cos = [c for c in cos if c in used_o]     # khali folder/company na dekhay
            raw = json.dumps({"cats": cats, "companies": cos, "items": items}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            _cat_cache.clear()
            _cat_cache.update(ver=ver, cats=cats, cos=cos, raw=raw, gz=gzip.compress(raw, 6),
                              etag='"' + hashlib.md5(raw).hexdigest() + '"')
        return dict(_cat_cache)


@app.get("/api/catalog")
def catalog():
    """Whole catalog - compact arrays, gzip-ed and cached. Navi item add thay to etag badlay ane customer ne turant dekhay."""
    c = catalog_data()
    if request.headers.get("If-None-Match") == c["etag"]:
        return Response(status=304)
    use_gz = "gzip" in request.headers.get("Accept-Encoding", "")
    resp = Response(c["gz"] if use_gz else c["raw"], mimetype="application/json")
    if use_gz:
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["ETag"] = c["etag"]
    resp.headers["Cache-Control"] = "no-cache"      # browser darek var etag check kare (304 hoy to fast)
    return resp


# ---------------- customer login (phone OTP / Google) ----------------
def norm_phone(v):
    p = re.sub(r"\D", "", str(v or ""))[-10:]
    return p if re.fullmatch(r"[6-9]\d{9}", p) else None


def start_session(cid):
    tok = secrets.token_urlsafe(32)
    db().execute("INSERT INTO cust_sessions VALUES(?,?,?)", (tok, cid, now() + CUST_SESSION_DAYS * 86400)); db().commit()
    return tok


def current_customer(extend=False):
    tok = request.headers.get("X-Cust-Token", "").strip()
    if not tok:
        return None
    r = db().execute("SELECT c.*, s.expires AS s_exp FROM cust_sessions s JOIN customers c ON c.id=s.customer_id WHERE s.token=?", (tok,)).fetchone()
    if not r or r["s_exp"] < now():
        return None
    if extend:
        t = now()
        if r["s_exp"] < t + (CUST_SESSION_DAYS - 7) * 86400:     # roj vaparta rahe to login lamba chale
            db().execute("UPDATE cust_sessions SET expires=? WHERE token=?", (t + CUST_SESSION_DAYS * 86400, tok))
        last_log = r.get("last_login")
        if not last_log or (t - float(last_log)) > 300:
            db().execute("UPDATE customers SET last_login=? WHERE id=?", (t, r["id"]))
        db().commit()
    return r


def cust_json(c):
    if not c:
        return {}
    return {"id": c.get("id"), "name": c.get("name") or "", "phone": c.get("phone") or "", "email": c.get("email") or ""}


def verify_google(cred):
    if not GOOGLE_CLIENT_ID or not cred:
        return None
    try:
        with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(str(cred)), timeout=8) as r:
            d = json.loads(r.read().decode())
    except Exception:
        return None
    if d.get("aud") != GOOGLE_CLIENT_ID or d.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if str(d.get("email_verified")).lower() != "true" or float(d.get("exp", 0)) < now():
        return None
    return d


@app.get("/api/auth/config")
def auth_config():
    return jsonify(google=GOOGLE_CLIENT_ID)


@app.post("/api/auth/phone/send")
def auth_phone_send():
    phone = norm_phone(body().get("phone"))
    if not phone:
        return err("10 digit no sacho mobile number lakho")
    if not rate_ok("otp:" + (request.remote_addr or ""), 10, 3600):
        return err("Ghana OTP magya, thodi vaar pachi try karo", 429)
    code, e = issue_otp("cust_login", phone)
    if e:
        return err(e, 429)
    return jsonify(ok=True, **({"dev_otp": code} if DEV_OTP else {}))


@app.post("/api/auth/phone/verify")
def auth_phone_verify():
    b = body(); phone = norm_phone(b.get("phone"))
    if not phone or not check_otp("cust_login", phone, b.get("otp", "")):
        return err("OTP khotu che athva pura thai gayo")
    d = db(); t = now()
    name = " ".join(str(b.get("name") or "").split())[:80]
    c = d.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    if not c:
        cur = d.execute("INSERT INTO customers(phone,name,created_at,last_login) VALUES(?,?,?,?)", (phone, name, t, t))
        cid = cur.lastrowid
    else:
        cid = c["id"]
        if name:
            d.execute("UPDATE customers SET last_login=?, name=? WHERE id=?", (t, name, cid))
        else:
            d.execute("UPDATE customers SET last_login=? WHERE id=?", (t, cid))
    d.commit()
    return jsonify(token=start_session(cid), customer=cust_json(d.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()))


@app.post("/api/auth/google")
def auth_google():
    info = verify_google(body().get("credential"))
    if not info:
        return err("Google login thayu nahi, farithi try karo", 401)
    d = db(); t = now(); sub = str(info["sub"])
    c = d.execute("SELECT * FROM customers WHERE google_sub=?", (sub,)).fetchone()
    name = " ".join(str(info.get("name", "")).split())[:80]
    email = str(info.get("email", ""))[:120]
    if not c:
        cur = d.execute("INSERT INTO customers(google_sub,email,name,created_at,last_login) VALUES(?,?,?,?,?)",
                        (sub, email, name, t, t))
        cid = cur.lastrowid
    else:
        cid = c["id"]
        d.execute("UPDATE customers SET last_login=?, name=CASE WHEN name='' OR name IS NULL THEN ? ELSE name END, email=CASE WHEN email='' OR email IS NULL THEN ? ELSE email END WHERE id=?", (t, name, email, cid))
    d.commit()
    return jsonify(token=start_session(cid), customer=cust_json(d.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()))


@app.post("/api/auth/logout")
def auth_logout():
    tok = request.headers.get("X-Cust-Token", "").strip()
    db().execute("DELETE FROM cust_sessions WHERE token=?", (tok,)); db().commit()
    return jsonify(ok=True)


@app.get("/api/me")
def me_get():
    c = current_customer(extend=True)
    if not c:
        return err("Login required", 401)
    return jsonify(customer=cust_json(c))


@app.put("/api/me")
def me_put():
    c = current_customer()
    if not c:
        return err("Login required", 401)
    name = " ".join(str(body().get("name") or "").split())[:80]
    if len(name) < 2:
        return err("Naam lakho")
    db().execute("UPDATE customers SET name=? WHERE id=?", (name, c["id"])); db().commit()
    return jsonify(customer=cust_json(db().execute("SELECT * FROM customers WHERE id=?", (c["id"],)).fetchone()))


# ---------------- customer orders ----------------
@app.post("/api/orders")
def order_create():
    """Customer order (login jaruri). Order automatic CONFIRMED thai jay che."""
    cu = current_customer()
    if not cu:
        return err("Order karva pehla login karo", 401)
    if not manager()["shop_open"]:
        return err(get_settings()["closed_msg"] or "Dukan atyare band che, thodi vaar pachi order karo", 403)
    if not rate_ok("order:%s" % cu["id"], 30, 3600):
        return err("Ghana orders thaya, thodi vaar pachi try karo", 429)
    b = body()
    name = " ".join(str(b.get("name") or "").split())[:80]
    if len(name) < 2:
        return err("Customer name lakho")
    cart = b.get("items") or []
    if not isinstance(cart, list) or not cart or len(cart) > 500:
        return err("Cart khali che")
    qty = {}
    for x in cart:
        try:
            q = int(x.get("q", 0)); i = int(x.get("c", 0))
        except Exception:
            return err("Cart barabar nathi")
        if q < 1 or q > 9999 or i < 1:
            return err("Quantity barabar nathi")
        qty[i] = qty.get(i, 0) + q
    d = db()
    rows = d.execute(f"SELECT * FROM items WHERE id IN ({','.join('?' * len(qty))}) ORDER BY id", list(qty)).fetchall()
    if len(rows) != len(qty):
        return err("Koi item have available nathi, page refresh karo")
    t = now(); tok = secrets.token_urlsafe(16); total = sum(qty.values())
    cur = d.execute("INSERT INTO orders(token,name,total,status,created_at,updated_at,customer_id) VALUES(?,?,?,?,?,?,?)",
                    (tok, name, total, "Confirmed", t, t, cu["id"]))
    oid = cur.lastrowid
    d.executemany("INSERT INTO order_items(order_id,item_id,article,name,category,company,model,unit,qty,price) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  [(oid, r["id"], r["article"], r["name"], r["category"], r["company"], r["model"], r["unit"], qty[r["id"]], r["price"]) for r in rows])
    if not cu.get("name"):
        d.execute("UPDATE customers SET name=? WHERE id=?", (name, cu["id"]))
    d.commit()
    ords = load_orders("WHERE o.id=?", (oid,))
    return jsonify(order=ords[0] if ords else {"id": str(oid), "name": name, "total": total, "status": "Confirmed"}, token=tok)


@app.get("/api/orders/mine")
def orders_mine():
    """Logged-in customer na badha orders - Confirmed / Delivered / Cancelled, badha."""
    cu = current_customer()
    if not cu:
        return err("Login required", 401)
    return jsonify(orders=load_orders("WHERE o.customer_id=? ORDER BY o.created_at DESC LIMIT 300", (cu["id"],)))


@app.post("/api/orders/claim")
def orders_claim():
    """Login pehla na (is device na) juna orders ne account sathe jodo."""
    cu = current_customer()
    if not cu:
        return err("Login required", 401)
    n = 0
    for r in (body().get("orders") or [])[:50]:
        try:
            c = db().execute("UPDATE orders SET customer_id=? WHERE id=? AND token=? AND customer_id IS NULL",
                             (cu["id"], int(r.get("id")), str(r.get("token"))))
            n += c.rowcount
        except Exception:
            continue
    db().commit()
    return jsonify(claimed=n)


# ---------------- manager API ----------------
@app.post("/api/manager/login")
def m_login():
    b = body(); key = "login:" + (request.remote_addr or "")
    if throttled(key):
        return err("Ghana vakhat khotu password, 15 min pachi try karo", 429)
    m = manager()
    if str(b.get("id", "")).strip().lower() == m["login_id"].lower() and check_password_hash(m["pw_hash"], str(b.get("password", ""))):
        with _lock:
            _fails.pop(key, None)
        tok = secrets.token_urlsafe(32)
        db().execute("DELETE FROM sessions WHERE expires<?", (now(),))
        db().execute("INSERT INTO sessions VALUES(?,?)", (tok, now() + 30 * 86400)); db().commit()
        return jsonify(token=tok)
    add_fail(key)
    return err("Incorrect Manager ID or Password", 401)


@app.post("/api/manager/logout")
@mgr_required
def m_logout():
    db().execute("DELETE FROM sessions WHERE token=?", (request.headers["Authorization"][7:],)); db().commit()
    return jsonify(ok=True)


@app.get("/api/manager/me")
@mgr_required
def m_me():
    m = manager(); d = db()
    one = lambda q, a=(): d.execute(q, a).fetchone()[0]
    t0 = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    active = one("SELECT COUNT(*) FROM orders WHERE status IN('New','Confirmed')")
    cat = catalog_data()
    stats = {"active": active, "today": one("SELECT COUNT(*) FROM orders WHERE created_at>=?", (t0,)),
             "total": one("SELECT COUNT(*) FROM orders"), "delivered": one("SELECT COUNT(*) FROM orders WHERE status='Delivered'"),
             "cancelled": one("SELECT COUNT(*) FROM orders WHERE status='Cancelled'"), "customers": one("SELECT COUNT(*) FROM customers"),
             "deliveredAmount": one("SELECT COALESCE(SUM(oi.qty*oi.price),0) FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE o.status='Delivered'")}
    return jsonify(name=m["name"], phone=m["phone"], open=bool(m["shop_open"]),
                   loginId=m["login_id"], active=active, brands=cat["cos"], groups=cat["cats"],
                   itemCount=one("SELECT COUNT(*) FROM items"), customItems=one("SELECT COUNT(*) FROM custom_items"),
                   profile=get_settings(), stats=stats)


@app.put("/api/manager/profile")
@mgr_required
def m_profile():
    b = body(); m = manager()
    name = " ".join(str(b.get("name") or m["name"]).split())[:80] or m["name"]
    vals = {}
    for k, mx in SETTING_KEYS.items():          # phone number fixed che (9998040666) - bija badha fields save thay che
        if k not in b:
            continue
        v = " ".join(str(b.get(k) or "").split())
        if k == "whatsapp":
            v = re.sub(r"\D", "", v)[-10:]
            if v and len(v) != 10:
                return err("WhatsApp / business number 10 digit no hovo joiye")
        elif k == "email" and v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            return err("Email barabar nathi")
        elif k == "gstin":
            v = v.upper().replace(" ", "")
            if v and not re.fullmatch(r"[0-9A-Z]{15}", v):
                return err("GSTIN 15 character no hoy che")
        elif k == "shop_name" and len(v) < 2:
            return err("Shop nu naam lakho")
        vals[k] = v[:mx] if k != "address" else v[:mx]
    d = db()
    d.execute("UPDATE manager SET name=? WHERE id=1", (name,))
    for k, v in vals.items():
        d.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    d.commit()
    return jsonify(ok=True, name=name, profile=get_settings())


@app.post("/api/manager/credentials")
@mgr_required
def m_credentials():
    """Manager ID / password badlo (current password jaruri). Save rahe che, logout pachi pan."""
    b = body(); m = manager(); key = "cred:" + (request.remote_addr or "")
    if throttled(key):
        return err("Ghana vakhat khotu password, 15 min pachi try karo", 429)
    if not check_password_hash(m["pw_hash"], str(b.get("current", ""))):
        add_fail(key)
        return err("Current password khotu che", 401)
    new_id = str(b.get("newId") or "").strip(); new_pw = str(b.get("newPassword") or "")
    if new_id and (len(new_id) < 3 or len(new_id) > 40 or " " in new_id):
        return err("Manager ID 3 thi 40 character no, space vagar")
    if new_pw and len(new_pw) < 6:
        return err("Password ochha ma ochho 6 character no rakho")
    if (not new_id or new_id == m["login_id"]) and not new_pw:
        return err("Kai badlav karyo nathi")
    d = db()
    if new_id:
        d.execute("UPDATE manager SET login_id=? WHERE id=1", (new_id,))
    if new_pw:
        d.execute("UPDATE manager SET pw_hash=? WHERE id=1", (generate_password_hash(new_pw),))
        d.execute("DELETE FROM sessions WHERE token!=?", (request.headers["Authorization"][7:],))   # bija device logout
    d.commit()
    return jsonify(ok=True, loginId=new_id or m["login_id"])


@app.post("/api/manager/shop")
@mgr_required
def m_shop():
    db().execute("UPDATE manager SET shop_open=? WHERE id=1", (1 if body().get("open") else 0,)); db().commit()
    return jsonify(open=bool(manager()["shop_open"]))


@app.get("/api/manager/orders")
@mgr_required
def m_orders():
    st = request.args.get("status")
    if st in STATUSES:
        return jsonify(orders=load_orders("WHERE o.status=? ORDER BY o.created_at DESC LIMIT 300", (st,)))
    return jsonify(orders=load_orders("ORDER BY o.created_at DESC LIMIT 300"))


@app.patch("/api/manager/orders/<int:oid>")
@mgr_required
def m_order_set(oid):
    st = body().get("status")
    if st not in STATUSES:
        return err("Status barabar nathi")
    c = db().execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (st, now(), oid))
    db().commit()
    if not c.rowcount:
        return err("Order malyo nahi", 404)
    ords = load_orders("WHERE o.id=?", (oid,))
    if not ords:
        return err("Order malyo nahi", 404)
    return jsonify(order=ords[0])


# ---- manager: catalog ma navi item add / edit / delete ----
def clean_product(b):
    one = lambda k, mx: " ".join(str(b.get(k) or "").split())[:mx]
    p = {"article": one("article", 20), "name": one("name", 150).upper(), "category": one("category", 40).upper(),
         "company": one("company", 30).upper(), "model": one("model", 40).upper() or "ALL MODELS", "unit": one("unit", 20).upper()}
    if len(p["name"]) < 2:
        return None, "Item nu naam lakho"
    if not p["category"]:
        return None, "Category (folder) lakho"
    if not p["company"]:
        return None, "Company lakho"
    raw = b.get("price")
    if raw in (None, ""):
        p["price"] = None                       # MRP khali = "UD"
    else:
        try:
            p["price"] = int(float(raw))
            if p["price"] < 0 or p["price"] > 10_000_000:
                raise ValueError
        except Exception:
            return None, "MRP barabar nathi"
    return p, None


def product_row(r):
    return {"id": r["id"], "a": r["article"], "n": r["name"], "c": r["category"], "co": r["company"], "m": r["model"], "u": r["unit"], "p": r["price"]}


@app.get("/api/manager/products")
@mgr_required
def m_products():
    rows = db().execute("SELECT * FROM custom_items ORDER BY id DESC LIMIT 500").fetchall()
    cat = catalog_data()
    return jsonify(products=[product_row(r) for r in rows], cats=cat["cats"], companies=cat["cos"])


@app.post("/api/manager/products")
@mgr_required
def m_product_add():
    p, e = clean_product(body())
    if e:
        return err(e)
    d = db(); t = now()
    cur = d.execute("INSERT INTO custom_items(article,name,category,company,model,unit,price,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (p["article"], p["name"], p["category"], p["company"], p["model"], p["unit"], p["price"], t, t))
    pid = cur.lastrowid
    if not p["article"]:
        p["article"] = "N%d" % (pid - 1000000)
        d.execute("UPDATE custom_items SET article=? WHERE id=?", (p["article"], pid))
    d.execute("INSERT INTO items(id,article,name,category,company,model,unit,price) VALUES(?,?,?,?,?,?,?,?)",
              (pid, p["article"], p["name"], p["category"], p["company"], p["model"], p["unit"], p["price"]))
    d.commit()
    return jsonify(product=product_row(d.execute("SELECT * FROM custom_items WHERE id=?", (pid,)).fetchone()))


@app.put("/api/manager/products/<int:pid>")
@mgr_required
def m_product_edit(pid):
    d = db()
    if not d.execute("SELECT 1 FROM custom_items WHERE id=?", (pid,)).fetchone():
        return err("Aa item tame add kari nathi (Excel ni item ma badlav nathi thato)", 404)
    p, e = clean_product(body())
    if e:
        return err(e)
    p["article"] = p["article"] or "N%d" % (pid - 1000000)
    vals = (p["article"], p["name"], p["category"], p["company"], p["model"], p["unit"], p["price"])
    d.execute("UPDATE custom_items SET article=?,name=?,category=?,company=?,model=?,unit=?,price=?,updated_at=? WHERE id=?", vals + (now(), pid))
    d.execute("UPDATE items SET article=?,name=?,category=?,company=?,model=?,unit=?,price=? WHERE id=?", vals + (pid,))
    d.commit()
    return jsonify(product=product_row(d.execute("SELECT * FROM custom_items WHERE id=?", (pid,)).fetchone()))


@app.delete("/api/manager/products/<int:pid>")
@mgr_required
def m_product_del(pid):
    """Kai pan item delete (Excel ni pan). Customer ne pan turant gayab, restart pachi pan pachi na aave."""
    d = db()
    if not d.execute("SELECT 1 FROM items WHERE id=?", (pid,)).fetchone():
        return err("Item malyo nahi", 404)
    d.execute("DELETE FROM custom_items WHERE id=?", (pid,))
    if pid <= 1000000:
        d.execute("INSERT INTO deleted_items(id,deleted_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET deleted_at=EXCLUDED.deleted_at", (pid, now()))
    d.execute("DELETE FROM items WHERE id=?", (pid,))
    d.commit()
    return jsonify(ok=True)


@app.get("/api/manager/customers/<int:cid>")
@mgr_required
def m_customer(cid):
    try:
        c = db().execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not c:
            return err("Customer malyo nahi", 404)
        
        cid_val = c.get("id") or cid
        cname = (c.get("name") or "").strip()
        cphone = (c.get("phone") or "").strip()
        cemail = (c.get("email") or "").strip()
        display_name = cname if cname else (cphone if cphone else f"Customer #{cid_val}")
        
        created_val = c.get("created_at")
        last_login_val = c.get("last_login") or created_val
        
        cust_dict = {
            "id": cid_val,
            "name": display_name,
            "raw_name": cname,
            "phone": cphone,
            "email": cemail,
            "joined": fmt_time(created_val) if created_val else "—",
            "lastLogin": fmt_time(last_login_val) if last_login_val else "—",
            "loginDate": fmt_date(last_login_val) if last_login_val else "—",
            "loginTime": fmt_clock(last_login_val) if last_login_val else "—",
        }
        
        orders = []
        try:
            orders = load_orders("WHERE o.customer_id=? ORDER BY o.created_at DESC LIMIT 200", (cid,))
        except Exception as oe:
            app.logger.error(f"Error loading orders for customer {cid}: {oe}")
            orders = []
            
        return jsonify(customer=cust_dict, orders=orders)
    except Exception as e:
        app.logger.error(f"Error in m_customer {cid}: {e}")
        return err(f"Customer details load karva ma error aavi: {str(e)}", 500)


@app.get("/api/manager/customers")
@mgr_required
def m_customers():
    try:
        rows = db().execute("""SELECT c.*,
                                      (SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.id) AS n,
                                      (SELECT MAX(created_at) FROM orders o WHERE o.customer_id=c.id) AS last_order
                               FROM customers c
                               ORDER BY COALESCE(c.last_login, c.created_at, 0) DESC
                               LIMIT 500""").fetchall()
        custs = []
        for r in rows:
            cid = r.get("id")
            cname = (r.get("name") or "").strip()
            cphone = (r.get("phone") or "").strip()
            cemail = (r.get("email") or "").strip()
            
            created_val = r.get("created_at")
            last_login_val = r.get("last_login") or created_val
            
            display_name = cname if cname else (cphone if cphone else f"Customer #{cid}")
            
            custs.append({
                "id": cid,
                "name": display_name,
                "raw_name": cname,
                "phone": cphone,
                "email": cemail,
                "orders": r.get("n") or 0,
                "last": fmt_time(r.get("last_order")) if r.get("last_order") else "",
                "joined": fmt_time(created_val) if created_val else "",
                "lastLogin": fmt_time(last_login_val) if last_login_val else "",
                "loginDate": fmt_date(last_login_val) if last_login_val else "",
                "loginTime": fmt_clock(last_login_val) if last_login_val else "",
            })
        return jsonify(customers=custs)
    except Exception as e:
        app.logger.error(f"Error in m_customers: {e}")
        return jsonify(customers=[], error=f"Customers load error: {str(e)}"), 200


@app.post("/api/manager/otp")           # Settings > Account mate OTP
@mgr_required
def m_acct_otp():
    code, e = issue_otp("mgr_acct", manager()["phone"])
    if e:
        return err(e, 429)
    return jsonify(ok=True, **({"dev_otp": code} if DEV_OTP else {}))


@app.post("/api/manager/account")
@mgr_required
def m_acct():
    m = manager()
    if not check_otp("mgr_acct", m["phone"], body().get("otp", "")):
        return err("Wrong OTP")
    return jsonify(name=m["name"], id=m["login_id"], phone=m["phone"])


@app.post("/api/manager/forgot/send")
def m_forgot_send():
    phone = re.sub(r"\D", "", str(body().get("phone", "")))[-10:]
    m = manager()
    if phone != m["phone"]:
        return err("Phone number match nathi thato")
    code, e = issue_otp("mgr_reset", phone)
    if e:
        return err(e, 429)
    return jsonify(ok=True, **({"dev_otp": code} if DEV_OTP else {}))


@app.post("/api/manager/forgot/verify")
def m_forgot_verify():
    b = body(); phone = re.sub(r"\D", "", str(b.get("phone", "")))[-10:]
    if not check_otp("mgr_reset", phone, b.get("otp", "")):
        return err("Wrong OTP")
    return jsonify(proof=make_proof("mgr_reset", phone, 600))


@app.post("/api/manager/forgot/reset")
def m_forgot_reset():
    b = body(); pw = str(b.get("password", ""))
    if len(pw) < 6:
        return err("Password ochha ma ochho 6 character no rakho")
    if not use_proof(b.get("proof"), "mgr_reset"):
        return err("Session pura thai gayu, farithi OTP mangavo")
    db().execute("UPDATE manager SET pw_hash=? WHERE id=1", (generate_password_hash(pw),))
    db().execute("DELETE FROM sessions")
    tok = secrets.token_urlsafe(32)
    db().execute("INSERT INTO sessions VALUES(?,?)", (tok, now() + 30 * 86400)); db().commit()
    return jsonify(token=tok)


# ---------------- pages ----------------
@app.get("/")
def page_customer():
    r = send_from_directory(os.path.join(BASE, "static"), "customer.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/manager")
def page_manager():
    r = send_from_directory(os.path.join(BASE, "static"), "manager.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/static/<path:filename>")
def static_asset(filename):
    return send_from_directory(os.path.join(BASE, "static"), filename)


@app.after_request
def no_cache_api(r):
    if request.path.startswith(("/api/manager", "/api/orders", "/api/me", "/api/auth", "/api/shop")):
        r.headers["Cache-Control"] = "no-store"
    return r


# Vercel serverless entrypoint imports this module; schema initialization is safe and non-destructive.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
