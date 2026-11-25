"""SQLite data store for Saira Fashion House.

Pure stdlib (sqlite3/csv). One Store object owns one connection guarded by a
lock; order creation and reservations run inside BEGIN IMMEDIATE transactions
so two customers can never reserve the same last unit.

Older shop.db files are migrated in place (missing columns are added), so an
existing database keeps working after an upgrade.
"""
import csv
import hashlib
import json
import os
import random
import secrets
import sqlite3
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "2-3Y", "4-5Y", "6-7Y", "8-9Y", "Free"]
ORDER_STATUSES = ["Placed", "Packed", "Shipped", "Delivered", "Cancelled"]
COURIERS = ["TCS", "Leopards", "M&P", "PostEx"]


def money_fmt(n):
    return f"Rs {int(n or 0):,}"


def size_key(s):
    if s in SIZE_ORDER:
        return (0, SIZE_ORDER.index(s), "")
    try:
        return (1, int(s), "")
    except (TypeError, ValueError):
        return (2, 0, str(s))


SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog(
  sku TEXT PRIMARY KEY, product_id TEXT, name TEXT, category TEXT, color TEXT,
  size TEXT, original_price INTEGER, price INTEGER, stock INTEGER,
  is_sale INTEGER, description TEXT);
CREATE TABLE IF NOT EXISTS orders(
  order_id INTEGER PRIMARY KEY, customer_name TEXT, phone TEXT, sku TEXT,
  qty INTEGER, city TEXT, status TEXT, order_date TEXT, delivered_date TEXT,
  courier TEXT, tracking_no TEXT, total INTEGER, session_id TEXT,
  address TEXT, channel TEXT);
CREATE TABLE IF NOT EXISTS reservations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, sku TEXT,
  qty INTEGER, expires_at REAL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS conversations(
  session_id TEXT PRIMARY KEY, started_at REAL, turns INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
  text TEXT, ts REAL, meta TEXT);
CREATE TABLE IF NOT EXISTS escalations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, reason TEXT,
  detail TEXT, status TEXT DEFAULT 'open', created_at REAL,
  message TEXT, order_id INTEGER, customer TEXT, resolved_at REAL);
CREATE TABLE IF NOT EXISTS exchanges(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, old_sku TEXT,
  replacement_sku TEXT, courier_charge INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session_id TEXT,
  action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE,
  phone TEXT, salt TEXT, pass_hash TEXT, created_at REAL,
  role TEXT DEFAULT 'customer');
CREATE TABLE IF NOT EXISTS auth_tokens(
  token TEXT PRIMARY KEY, user_id INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, stars INTEGER, user_name TEXT,
  created_at REAL, comment TEXT);
"""

MIGRATIONS = [
    ("orders", "address", "TEXT"), ("orders", "channel", "TEXT"),
    ("messages", "meta", "TEXT"),
    ("escalations", "message", "TEXT"), ("escalations", "order_id", "INTEGER"),
    ("escalations", "customer", "TEXT"), ("escalations", "resolved_at", "REAL"),
    ("users", "role", "TEXT DEFAULT 'customer'"),
    ("reviews", "comment", "TEXT"),
]


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 60000).hex()


class Store:
    def __init__(self, db_path=None, data_dir=None, seed_admin=True):
        self.db_path = str(db_path or (BASE / "shop.db"))
        self.data_dir = Path(data_dir or DATA)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            if self.conn.execute("SELECT COUNT(*) c FROM catalog").fetchone()["c"] == 0:
                self._seed()
            if seed_admin:
                self._seed_admin()

    def _migrate(self):
        for table, col, typ in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def _seed_admin(self):
        email = os.environ.get("ADMIN_EMAIL", "admin@saira.pk").lower()
        password = os.environ.get("ADMIN_PASSWORD", "Admin@1234")
        r = self.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not r:
            salt = os.urandom(16).hex()
            self.conn.execute(
                "INSERT INTO users(name,email,phone,salt,pass_hash,created_at,role)"
                " VALUES(?,?,?,?,?,?,'admin')",
                ("Store Staff", email, "", salt, _hash(password, salt), time.time()))
        else:
            self.conn.execute("UPDATE users SET role='admin' WHERE id=?", (r["id"],))

    # ------------------------------------------------------------------ seed
    def _seed(self):
        with open(self.data_dir / "catalog.csv", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.conn.executemany(
            "INSERT OR REPLACE INTO catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(r["sku"], r["product_id"], r["name"], r["category"], r["color"],
              r["size"], int(r["original_price"]), int(r["price"]),
              int(r["stock"]), 1 if r["is_sale"] == "True" else 0,
              r["description"]) for r in rows])
        with open(self.data_dir / "orders.csv", newline="", encoding="utf-8-sig") as f:
            orows = list(csv.DictReader(f))
        self.conn.executemany(
            "INSERT OR REPLACE INTO orders(order_id,customer_name,phone,sku,qty,"
            "city,status,order_date,delivered_date,courier,tracking_no,total,"
            "session_id,address,channel) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,'seed')",
            [(int(r["order_id"]), r["customer_name"], r["phone"], r["sku"],
              int(r["qty"]), r["city"], r["status"], r["order_date"],
              r["delivered_date"] or None, r["courier"] or None,
              r["tracking_no"] or None, int(r["total"])) for r in orows])

    def reset(self):
        with self.lock:
            for t in ("catalog", "orders", "reservations", "conversations",
                      "messages", "escalations", "exchanges", "audit", "kv"):
                self.conn.execute(f"DELETE FROM {t}")
            self._seed()

    def close(self):
        with self.lock:
            self.conn.close()

    # --------------------------------------------------------------- catalog
    def variant(self, sku):
        with self.lock:
            r = self.conn.execute("SELECT * FROM catalog WHERE sku=?", (sku,)).fetchone()
            return dict(r) if r else None

    def find_variant(self, name, color=None, size=None):
        q = "SELECT * FROM catalog WHERE lower(name)=lower(?)"
        args = [name]
        if color:
            q += " AND lower(color)=lower(?)"; args.append(color)
        if size:
            q += " AND upper(size)=upper(?)"; args.append(size)
        with self.lock:
            r = self.conn.execute(q + " ORDER BY sku", args).fetchone()
            return dict(r) if r else None

    def variants_of(self, name):
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM catalog WHERE lower(name)=lower(?) ORDER BY sku", (name,))]

    def product_names(self):
        with self.lock:
            return [r["name"] for r in self.conn.execute(
                "SELECT DISTINCT name FROM catalog ORDER BY name")]

    def _reserved_map(self):
        rows = self.conn.execute(
            "SELECT sku, SUM(qty) q FROM reservations WHERE active=1 AND expires_at>=?"
            " GROUP BY sku", (time.time(),))
        return {r["sku"]: r["q"] for r in rows}

    def catalog_search(self, q=None, category=None, in_stock=None, limit=50, offset=0):
        sql = "SELECT * FROM catalog WHERE 1=1"
        args = []
        if q:
            sql += (" AND (lower(name) LIKE ? OR lower(category) LIKE ? OR lower(color) LIKE ?"
                    " OR lower(sku) LIKE ?)")
            args += [f"%{q.lower()}%"] * 4
        if category:
            sql += " AND lower(category)=lower(?)"; args.append(category)
        if in_stock:
            sql += " AND stock>0"
        with self.lock:
            total = self.conn.execute(sql.replace("SELECT *", "SELECT COUNT(*) c"), args).fetchone()["c"]
            rows = [dict(r) for r in self.conn.execute(
                sql + " ORDER BY name,color,size LIMIT ? OFFSET ?", args + [limit, offset])]
            res = self._reserved_map()
        for r in rows:
            r["available"] = max(0, r["stock"] - res.get(r["sku"], 0))
        return {"total": total, "items": rows}

    def product_summaries(self):
        """One row per product: price, sale, colors, sizes, total available."""
        with self.lock:
            rows = [dict(r) for r in self.conn.execute("SELECT * FROM catalog ORDER BY sku")]
            res = self._reserved_map()
        prods = {}
        for r in rows:
            p = prods.setdefault(r["product_id"], {
                "product_id": r["product_id"], "name": r["name"], "category": r["category"],
                "price": r["price"], "original_price": r["original_price"],
                "is_sale": bool(r["is_sale"]), "description": r["description"],
                "colors": [], "sizes": [], "available": 0, "variants": 0,
                "color_stock": {}})
            p["price"] = min(p["price"], r["price"])
            p["original_price"] = max(p["original_price"], r["original_price"])
            if r["color"] not in p["colors"]:
                p["colors"].append(r["color"])
            if r["size"] not in p["sizes"]:
                p["sizes"].append(r["size"])
            avail = max(0, r["stock"] - res.get(r["sku"], 0))
            p["available"] += avail
            p["color_stock"][r["color"]] = p["color_stock"].get(r["color"], 0) + avail
            p["variants"] += 1
        out = list(prods.values())
        for p in out:
            p["sizes"].sort(key=size_key)
        return out

    def product_detail(self, product_id):
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM catalog WHERE product_id=? ORDER BY sku", (product_id,))]
            res = self._reserved_map()
        if not rows:
            return None
        for r in rows:
            r["available"] = max(0, r["stock"] - res.get(r["sku"], 0))
            r["is_sale"] = bool(r["is_sale"])
        summ = next(p for p in self.product_summaries() if p["product_id"] == product_id)
        summ["variants_list"] = sorted(rows, key=lambda r: (r["color"], size_key(r["size"])))
        return summ

    def categories(self):
        cats = {}
        for p in self.product_summaries():
            c = cats.setdefault(p["category"], {"category": p["category"], "products": 0,
                                                "min_price": p["price"], "available": 0})
            c["products"] += 1
            c["min_price"] = min(c["min_price"], p["price"])
            c["available"] += p["available"]
        return sorted(cats.values(), key=lambda c: c["category"])

    # ----------------------------------------------------------- reservations
    def _expire(self):
        self.conn.execute(
            "UPDATE reservations SET active=0 WHERE active=1 AND expires_at<?",
            (time.time(),))

    def reserved_qty(self, sku):
        with self.lock:
            self._expire()
            r = self.conn.execute(
                "SELECT COALESCE(SUM(qty),0) s FROM reservations "
                "WHERE sku=? AND active=1", (sku,)).fetchone()
            return r["s"]

    def available(self, sku):
        v = self.variant(sku)
        if not v:
            return 0
        return v["stock"] - self.reserved_qty(sku)

    def reserve(self, session_id, items, minutes=15):
        """items: list of (sku, qty). Atomic: all-or-nothing."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._expire()
                need = {}
                for sku, qty in items:
                    need[sku] = need.get(sku, 0) + qty
                for sku, qty in need.items():
                    if self.available(sku) < qty:
                        self.conn.execute("ROLLBACK")
                        return False
                exp = time.time() + minutes * 60
                for sku, qty in items:
                    self.conn.execute(
                        "INSERT INTO reservations(session_id,sku,qty,expires_at)"
                        " VALUES(?,?,?,?)", (session_id, sku, qty, exp))
                self.conn.execute("COMMIT")
                return True
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def release(self, session_id):
        with self.lock:
            self.conn.execute(
                "UPDATE reservations SET active=0 WHERE session_id=? AND active=1",
                (session_id,))

    def create_orders(self, session_id, customer, items, city, grand_total,
                      delivery=None, channel="chat"):
        """Convert this session's active reservations into orders.

        items: list of dicts {sku, qty, price}. Stock is decremented inside the
        same IMMEDIATE transaction, so reserved units cannot be double-sold.
        One order row per item; the delivery charge is added to the first.
        Returns list of new order ids.
        """
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._expire()
                ids = []
                next_id = (self.conn.execute(
                    "SELECT COALESCE(MAX(order_id),20000) m FROM orders"
                ).fetchone()["m"] + 1)
                if delivery is None:
                    delivery = grand_total - sum(i["price"] * i["qty"] for i in items)
                for n, it in enumerate(items):
                    res = self.conn.execute(
                        "SELECT id FROM reservations WHERE session_id=? AND sku=?"
                        " AND active=1 AND qty=? ORDER BY id", (session_id, it["sku"], it["qty"])
                    ).fetchone() or self.conn.execute(
                        "SELECT id FROM reservations WHERE session_id=? AND sku=?"
                        " AND active=1 ORDER BY id", (session_id, it["sku"])).fetchone()
                    if not res:
                        self.conn.execute("ROLLBACK")
                        return []
                    self.conn.execute("UPDATE reservations SET active=0 WHERE id=?",
                                      (res["id"],))
                    cur = self.conn.execute(
                        "UPDATE catalog SET stock=stock-? WHERE sku=? AND stock>=?",
                        (it["qty"], it["sku"], it["qty"]))
                    if cur.rowcount != 1:
                        self.conn.execute("ROLLBACK")
                        return []
                    total = it["price"] * it["qty"] + (delivery if n == 0 else 0)
                    self.conn.execute(
                        "INSERT INTO orders(order_id,customer_name,phone,sku,qty,"
                        "city,status,order_date,delivered_date,courier,tracking_no,"
                        "total,session_id,address,channel) VALUES(?,?,?,?,?,?,'Placed',"
                        "date('now'),NULL,NULL,NULL,?,?,?,?)",
                        (next_id, customer.get("name"), customer.get("phone"),
                         it["sku"], it["qty"], city, total, session_id,
                         customer.get("address"), channel))
                    ids.append(next_id)
                    next_id += 1
                self.conn.execute("COMMIT")
                return ids
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # ---------------------------------------------------------------- orders
    def get_order(self, order_id):
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return None
        with self.lock:
            r = self.conn.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()
            return dict(r) if r else None

    def _order_rows(self, sql, args):
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(sql, args)]
            names = {r["sku"]: dict(r) for r in self.conn.execute(
                "SELECT sku,name,color,size,price FROM catalog")}
        for r in rows:
            v = names.get(r["sku"]) or {}
            r["item"] = f"{v.get('name', r['sku'])} · {v.get('color', '')} · {v.get('size', '')}"
            r["product_name"] = v.get("name")
        return rows

    def list_orders(self, limit=100, status=None, q=None):
        sql, args = "SELECT * FROM orders WHERE 1=1", []
        if status:
            sql += " AND status=?"; args.append(status)
        if q:
            sql += " AND (CAST(order_id AS TEXT) LIKE ? OR lower(customer_name) LIKE ? OR phone LIKE ?)"
            args += [f"%{q.lower()}%"] * 3
        return self._order_rows(sql + " ORDER BY order_id DESC LIMIT ?", args + [limit])

    def update_order_status(self, order_id, status):
        if status not in ORDER_STATUSES:
            return None
        o = self.get_order(order_id)
        if not o:
            return None
        sets, args = ["status=?"], [status]
        if status == "Shipped" and not o.get("tracking_no"):
            courier = random.choice(COURIERS)
            sets += ["courier=?", "tracking_no=?"]
            args += [courier, courier.replace("&", "")[:3].upper() + str(random.randint(100000, 999999))]
        if status == "Delivered":
            sets.append("delivered_date=date('now')")
        with self.lock:
            if status == "Cancelled" and o["status"] != "Cancelled":
                self.conn.execute("UPDATE catalog SET stock=stock+? WHERE sku=?",
                                  (o["qty"], o["sku"]))
            self.conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE order_id=?",
                              args + [int(order_id)])
        return self.get_order(order_id)

    def add_exchange(self, order_id, old_sku, replacement_sku, charge):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO exchanges(order_id,old_sku,replacement_sku,"
                "courier_charge,created_at) VALUES(?,?,?,?,?)",
                (order_id, old_sku, replacement_sku, charge, time.time()))
            return cur.lastrowid

    def orders_for_phone(self, phone):
        digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
        if not digits:
            return []
        return self._order_rows("SELECT * FROM orders WHERE phone=? ORDER BY order_id DESC",
                                (digits,))

    # ------------------------------------------------------- logs / dashboard
    def log_message(self, session_id, role, text, meta=None):
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO conversations VALUES(?,?,0)", (session_id, time.time()))
            self.conn.execute(
                "UPDATE conversations SET turns=turns+1 WHERE session_id=?", (session_id,))
            cur = self.conn.execute(
                "INSERT INTO messages(session_id,role,text,ts,meta) VALUES(?,?,?,?,?)",
                (session_id, role, text, time.time(),
                 json.dumps(meta) if meta is not None else None))
            return cur.lastrowid

    def messages_for(self, session_id, after_id=0, limit=200):
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                (session_id, int(after_id or 0), limit))]
        for r in rows:
            try:
                r["meta"] = json.loads(r["meta"]) if r.get("meta") else None
            except ValueError:
                r["meta"] = None
        return rows

    def audit(self, session_id, action, detail):
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail)
        with self.lock:
            self.conn.execute(
                "INSERT INTO audit(ts,session_id,action,detail) VALUES(?,?,?,?)",
                (time.time(), session_id, action, str(detail)[:500]))

    def add_escalation(self, session_id, reason, detail, message=None,
                       order_id=None, customer=None):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO escalations(session_id,reason,detail,created_at,message,"
                "order_id,customer) VALUES(?,?,?,?,?,?,?)",
                (session_id, reason, str(detail)[:500], time.time(),
                 (message or "")[:500], order_id, customer))
            return cur.lastrowid

    def get_escalation(self, esc_id):
        with self.lock:
            r = self.conn.execute("SELECT * FROM escalations WHERE id=?", (esc_id,)).fetchone()
            return dict(r) if r else None

    def list_escalations(self, status=None, session_id=None):
        sql = "SELECT * FROM escalations WHERE 1=1"
        args = []
        if status:
            sql += " AND status=?"; args.append(status)
        if session_id:
            sql += " AND session_id=?"; args.append(session_id)
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                sql + " ORDER BY id DESC LIMIT 200", args)]

    def resolve_escalation(self, esc_id):
        with self.lock:
            cur = self.conn.execute(
                "UPDATE escalations SET status='resolved', resolved_at=? WHERE id=?",
                (time.time(), esc_id))
            return cur.rowcount == 1

    def set_kv(self, key, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)",
                              (key, json.dumps(value)))

    def get_kv(self, key):
        with self.lock:
            r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(r["value"]) if r else None

    def dashboard(self):
        with self.lock:
            c = self.conn
            conv = c.execute("SELECT COUNT(*) n FROM conversations").fetchone()["n"]
            ords = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) rev FROM orders"
                             " WHERE status!='Cancelled'").fetchone()
            new_ords = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) rev FROM orders"
                                 " WHERE channel IN ('chat','web')").fetchone()
            esc = c.execute("SELECT COUNT(*) n FROM escalations WHERE status='open'").fetchone()["n"]
            esc_sessions = c.execute(
                "SELECT COUNT(DISTINCT session_id) n FROM escalations").fetchone()["n"]
            esc_total = c.execute("SELECT COUNT(*) n FROM escalations").fetchone()["n"]
            low = [dict(r) for r in c.execute(
                "SELECT sku,name,color,size,stock FROM catalog WHERE stock BETWEEN 1 AND 4"
                " ORDER BY stock, name LIMIT 25")]
            out = [dict(r) for r in c.execute(
                "SELECT sku,name,color,size,stock FROM catalog WHERE stock<=0 ORDER BY name LIMIT 25")]
            top = [dict(r) for r in c.execute(
                "SELECT cat.name, COUNT(*) n, SUM(o.qty) units FROM orders o JOIN catalog cat"
                " ON cat.sku=o.sku GROUP BY cat.name ORDER BY n DESC, units DESC LIMIT 6")]
            by_status = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM orders GROUP BY status")}
            reasons = {r["reason"]: r["n"] for r in c.execute(
                "SELECT reason, COUNT(*) n FROM escalations GROUP BY reason ORDER BY n DESC")}
            intents = {}
            for r in c.execute("SELECT detail FROM audit WHERE action='decision'"):
                try:
                    k = json.loads(r["detail"]).get("intent", "?")
                except ValueError:
                    continue
                intents[k] = intents.get(k, 0) + 1
            stock_units = c.execute("SELECT COALESCE(SUM(stock),0) n FROM catalog").fetchone()["n"]
            recent_audit = [dict(r) for r in c.execute(
                "SELECT * FROM audit WHERE action!='decision' ORDER BY id DESC LIMIT 15")]
        handled = round(100.0 * (conv - esc_sessions) / conv, 1) if conv else 100.0
        return {
            "conversations": conv,
            "orders": ords["n"],
            "revenue": ords["rev"],
            "new_orders": new_ords["n"],
            "new_revenue": new_ords["rev"],
            "open_escalations": esc,
            "total_escalations": esc_total,
            "handled_percent": max(handled, 0.0),
            "intent_counts": intents,
            "orders_by_status": by_status,
            "escalation_reasons": reasons,
            "top_products": top,
            "low_stock": low,
            "out_of_stock": out,
            "stock_units": stock_units,
            "recent_escalations": self.list_escalations()[:10],
            "recent_audit": recent_audit,
            "reviews": self.reviews_summary(),
        }

    def units_sold(self):
        """{product name: units ordered} — used for 'best sellers'."""
        with self.lock:
            return {r["name"]: r["u"] for r in self.conn.execute(
                "SELECT c.name, SUM(o.qty) u FROM orders o JOIN catalog c ON c.sku=o.sku "
                "WHERE o.status!='Cancelled' GROUP BY c.name")}

    # ------------------------------------------------------------ inventory
    def inventory(self, q=None, category=None, level=None, limit=600):
        """Variant rows for the staff Inventory page. level: low | out | None."""
        sql, args = "SELECT * FROM catalog WHERE 1=1", []
        if q:
            sql += " AND (lower(name) LIKE ? OR lower(sku) LIKE ? OR lower(color) LIKE ?)"
            args += [f"%{q.lower()}%"] * 3
        if category:
            sql += " AND category=?"; args.append(category)
        if level == "low":
            sql += " AND stock BETWEEN 1 AND 4"
        elif level == "out":
            sql += " AND stock<=0"
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(sql + " ORDER BY name,color LIMIT ?", args + [limit])]
            res = self._reserved_map()
        rows.sort(key=lambda r: (r["name"], r["color"], size_key(r["size"])))
        for r in rows:
            r["reserved"] = res.get(r["sku"], 0)
            r["available"] = max(0, r["stock"] - r["reserved"])
            r["is_sale"] = bool(r["is_sale"])
        return rows

    def update_variant(self, sku, stock=None, add=None, price=None, original_price=None):
        """Set or add stock, change price. Returns the updated row or None."""
        v = self.variant(sku)
        if not v:
            return None
        sets, args = [], []
        if stock is not None:
            sets.append("stock=?"); args.append(max(0, int(stock)))
        elif add:
            sets.append("stock=MAX(0, stock+?)"); args.append(int(add))
        if price is not None:
            sets.append("price=?"); args.append(max(1, int(price)))
            op = original_price if original_price is not None else max(v["original_price"], int(price))
            sets.append("original_price=?"); args.append(int(op))
            sets.append("is_sale=?"); args.append(1 if int(op) > int(price) else 0)
        if not sets:
            return v
        with self.lock:
            self.conn.execute(f"UPDATE catalog SET {', '.join(sets)} WHERE sku=?", args + [sku])
        return self.variant(sku)

    def add_product(self, name, category, description, price, colors, sizes, stock,
                    original_price=None):
        """Create a product with one SKU per color x size. Returns product_id + skus."""
        name = " ".join(name.split()).title()
        with self.lock:
            if self.conn.execute("SELECT 1 FROM catalog WHERE lower(name)=lower(?)", (name,)).fetchone():
                raise ValueError("A product with this name already exists — add stock to it instead.")
            ids = [r["product_id"] for r in self.conn.execute("SELECT DISTINCT product_id FROM catalog")]
            nxt = max([int(i[1:]) for i in ids if i[1:].isdigit()] or [0]) + 1
            pid = f"P{nxt:03d}"
            skus = [r["sku"] for r in self.conn.execute("SELECT sku FROM catalog")]
            n = max([int(s[3:]) for s in skus if s[3:].isdigit()] or [0])
            op = int(original_price or price)
            rows = []
            for c in colors:
                for sz in sizes:
                    n += 1
                    rows.append((f"SKU{n:04d}", pid, name, category, c.strip().title(), sz.strip().upper()
                                 if len(sz.strip()) <= 3 else sz.strip().title(), op, int(price),
                                 int(stock), 1 if op > int(price) else 0, description))
            self.conn.executemany("INSERT INTO catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        return {"product_id": pid, "name": name, "skus": [r[0] for r in rows]}

    def insights(self):
        """Owner-facing business insights computed from orders, chats and stock."""
        with self.lock:
            c = self.conn
            daily = [dict(r) for r in c.execute(
                "SELECT order_date d, COUNT(*) n, COALESCE(SUM(total),0) rev FROM orders "
                "WHERE status!='Cancelled' AND order_date >= date((SELECT MAX(order_date) FROM orders), '-13 day') "
                "GROUP BY order_date ORDER BY order_date")]
            by_cat = [dict(r) for r in c.execute(
                "SELECT cat.category, COUNT(*) orders, SUM(o.qty) units, SUM(o.total) rev FROM orders o "
                "JOIN catalog cat ON cat.sku=o.sku WHERE o.status!='Cancelled' "
                "GROUP BY cat.category ORDER BY rev DESC")]
            by_channel = {r["ch"]: {"orders": r["n"], "revenue": r["rev"]} for r in c.execute(
                "SELECT COALESCE(channel,'seed') ch, COUNT(*) n, COALESCE(SUM(total),0) rev FROM orders "
                "WHERE status!='Cancelled' GROUP BY ch")}
            conv = c.execute("SELECT COUNT(*) n FROM conversations WHERE session_id NOT LIKE 'web-checkout%'").fetchone()["n"]
            chat_buyers = c.execute(
                "SELECT COUNT(DISTINCT session_id) n FROM orders WHERE channel='chat'").fetchone()["n"]
            totals = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) rev FROM orders "
                               "WHERE status!='Cancelled'").fetchone()
            out_n = c.execute("SELECT COUNT(*) n FROM catalog WHERE stock<=0").fetchone()["n"]
            low_n = c.execute("SELECT COUNT(*) n FROM catalog WHERE stock BETWEEN 1 AND 4").fetchone()["n"]
            audits = [dict(r) for r in c.execute(
                "SELECT action, detail, ts FROM audit WHERE action IN ('unanswered','stockout_demand') "
                "ORDER BY id DESC LIMIT 500")]
            esc = {r["reason"]: r["n"] for r in c.execute(
                "SELECT reason, COUNT(*) n FROM escalations GROUP BY reason ORDER BY n DESC")}
            open_esc = c.execute("SELECT COUNT(*) n FROM escalations WHERE status='open'").fetchone()["n"]
        demand, unanswered = {}, []
        for a in audits:
            try:
                d = json.loads(a["detail"])
            except ValueError:
                continue
            if a["action"] == "stockout_demand":
                k = f"{d['product']} · {d['color']} · {d['size']}"
                demand[k] = demand.get(k, 0) + 1
            elif d.get("message"):
                unanswered.append({"message": d["message"], "kind": d.get("kind"), "ts": a["ts"]})
        demand = sorted(({"variant": k, "requests": v} for k, v in demand.items()), key=lambda x: -x["requests"])
        aov = round(totals["rev"] / totals["n"]) if totals["n"] else 0
        conversion = round(100 * chat_buyers / conv, 1) if conv else 0.0
        tips = []
        if demand:
            tips.append({"level": "warn", "title": "Restock what customers ask for",
                         "text": f"{demand[0]['variant']} was requested {demand[0]['requests']}× while out of stock."})
        if out_n:
            tips.append({"level": "warn", "title": f"{out_n} variants are out of stock",
                         "text": "Open Inventory → filter 'Out of stock' to restock them."})
        if low_n:
            tips.append({"level": "info", "title": f"{low_n} variants are running low",
                         "text": "They have 1–4 pieces left; restock before they sell out."})
        if esc:
            top = next(iter(esc))
            tips.append({"level": "info", "title": "Most common hand-off",
                         "text": f"{top.replace('_', ' ')} ({esc[top]}×). "
                                 + ("Consider a clearer discount/sale message on the site." if "discount" in top else
                                    "Review these tickets to see if a policy update would help.")})
        if open_esc:
            tips.append({"level": "warn", "title": f"{open_esc} customer(s) waiting for staff",
                         "text": "Reply from Hand-offs — your answer appears in their chat."})
        if unanswered:
            tips.append({"level": "info", "title": "Questions the assistant couldn't answer",
                         "text": f"{len(unanswered)} recent messages were unclear or off-topic — see the list below."})
        if by_cat:
            tips.append({"level": "ok", "title": "Best-selling category",
                         "text": f"{by_cat[0]['category']} — {money_fmt(by_cat[0]['rev'])} from {by_cat[0]['units']} units."})
        return {"kpis": {"orders": totals["n"], "revenue": totals["rev"], "avg_order_value": aov,
                         "conversations": conv, "chat_orders": chat_buyers, "chat_conversion_percent": conversion,
                         "out_of_stock": out_n, "low_stock": low_n, "open_handoffs": open_esc},
                "daily": daily, "by_category": by_cat, "by_channel": by_channel,
                "stockout_demand": demand[:10], "unanswered": unanswered[:15], "tips": tips}

    # ------------------------------------------------------------- accounts
    def create_user(self, name, email, phone, password):
        salt = os.urandom(16).hex()
        with self.lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO users(name,email,phone,salt,pass_hash,created_at,role)"
                    " VALUES(?,?,?,?,?,?,'customer')",
                    (name, email.lower(), phone, salt, _hash(password, salt), time.time()))
            except sqlite3.IntegrityError:
                return None
            return cur.lastrowid

    def verify_user(self, email, password):
        with self.lock:
            r = self.conn.execute("SELECT * FROM users WHERE email=?",
                                  (email.lower().strip(),)).fetchone()
        if not r or not secrets.compare_digest(_hash(password, r["salt"]), r["pass_hash"]):
            return None
        return {"id": r["id"], "name": r["name"], "email": r["email"],
                "phone": r["phone"], "role": r["role"] or "customer"}

    def new_token(self, user_id):
        tok = secrets.token_hex(24)
        with self.lock:
            self.conn.execute("INSERT INTO auth_tokens VALUES(?,?,?)",
                              (tok, user_id, time.time()))
        return tok

    def drop_token(self, token):
        with self.lock:
            self.conn.execute("DELETE FROM auth_tokens WHERE token=?", (token,))

    def user_by_token(self, token):
        if not token:
            return None
        with self.lock:
            r = self.conn.execute(
                "SELECT u.id,u.name,u.email,u.phone,u.role FROM auth_tokens t"
                " JOIN users u ON u.id=t.user_id WHERE t.token=?",
                (token,)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["role"] = d.get("role") or "customer"
            return d

    # -------------------------------------------------------------- reviews
    def add_review(self, stars, user_name=None, comment=None):
        with self.lock:
            self.conn.execute("INSERT INTO reviews(stars,user_name,created_at,comment)"
                              " VALUES(?,?,?,?)",
                              (int(stars), user_name, time.time(), (comment or "")[:500] or None))

    def reviews_summary(self):
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT stars,user_name,comment,created_at FROM reviews ORDER BY id DESC")]
        if not rows:
            return {"count": 0, "average": None, "distribution": {}, "recent": []}
        dist = {}
        for r in rows:
            dist[r["stars"]] = dist.get(r["stars"], 0) + 1
        return {"count": len(rows),
                "average": round(sum(r["stars"] for r in rows) / len(rows), 2),
                "distribution": dist,
                "recent": [r for r in rows if r.get("comment")][:6]}
