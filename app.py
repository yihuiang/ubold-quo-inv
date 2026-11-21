#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UBOLD Design Lab — Quotation & Invoice system
=============================================
A tiny local web app. Standard library only — no pip install needed.

Run:
    python app.py

Then open http://localhost:5000 (it also opens automatically).
Data is stored in  ubold.db  (SQLite) in this folder.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ubold.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
STATIC_DIR = os.path.join(BASE_DIR, "public")
PORT = 5000

# Secret used to sign session tokens. Override with env APP_SECRET in production.
APP_SECRET = (os.environ.get("APP_SECRET") or "ubold-dev-secret-change-me").encode("utf-8")
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days


# ------------------------------------------------------------------
#  Auth helpers (password hashing + signed session tokens)
# ------------------------------------------------------------------
def hash_pw(password):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt.encode("utf-8"), 200000)
    return salt + "$" + dk.hex()


def verify_pw(password, stored):
    try:
        salt, h = (stored or "").split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt.encode("utf-8"), 200000)
    return hmac.compare_digest(dk.hex(), h)


def _b64(b):
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(uid):
    payload = _b64(json.dumps({"uid": uid, "iat": int(time.time())}).encode("utf-8"))
    sig = _b64(hmac.new(APP_SECRET, payload.encode("utf-8"), hashlib.sha256).digest())
    return payload + "." + sig


def parse_token(token):
    try:
        payload, sig = (token or "").split(".", 1)
        expected = _b64(hmac.new(APP_SECRET, payload.encode("utf-8"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(time.time()) - int(data.get("iat", 0)) > TOKEN_TTL:
            return None
        return data.get("uid")
    except Exception:
        return None

# ------------------------------------------------------------------
#  Database helpers
# ------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()
    conn = get_db()
    conn.executescript(schema)
    conn.commit()
    migrate(conn)
    # Seed the VJ Food Enterprise sample (matches your QT-003 / INV-003) once.
    already = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if already == 0:
        seed(conn)
    conn.close()


def migrate(conn):
    """Add newer columns to databases created before they existed."""
    qcols = {r[1] for r in conn.execute("PRAGMA table_info(quotations)").fetchall()}
    for col in ("signed_file", "signed_name", "signed_at", "final_pdf", "final_pdf_name"):
        if col not in qcols:
            conn.execute(f"ALTER TABLE quotations ADD COLUMN {col} TEXT")
    icols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    for col in ("final_pdf", "final_pdf_name"):
        if col not in icols:
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {col} TEXT")
    licols = {r[1] for r in conn.execute("PRAGMA table_info(line_items)").fetchall()}
    if "disc_type" not in licols:
        conn.execute("ALTER TABLE line_items ADD COLUMN disc_type TEXT NOT NULL DEFAULT 'amount'")
    conn.commit()


def seed(conn):
    conn.execute(
        """INSERT INTO customers(customer_id,name,company,nric_tin,phone,address)
           VALUES(?,?,?,?,?,?)""",
        ("CI-003", "Ms. Loh Jin Qi", "VJ FOOD ENTERPRISE  德食品企業",
         "031122010912", "+60 14-380 4300",
         "18, Jalan Bakawali, Taman Selatan, 83700, Yong Peng, Johor"),
    )
    conn.execute(
        """INSERT INTO quotations(quotation_id,customer_id,date,term,status,subtotal,total,notes)
           VALUES(?,?,?,?,?,?,?,?)""",
        ("QT-003", "CI-003", "2026-10-02", "50% Advance", "accepted", 1000.0, 1000.0, ""),
    )
    conn.execute(
        """INSERT INTO invoices(invoice_id,customer_id,quotation_id,date,term,status,subtotal,advance_paid,balance_due,notes)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("INV-003", "CI-003", "QT-003", "2026-11-02", "Balance Due", "partial",
         1000.0, 500.0, 500.0, ""),
    )
    items = [
        ("PACKAGE A: LOGO DESIGN\nMain logo + 2 concept options\nDelivery date: Week of 19th October 2026",
         1, "SET", 1000.0, 0.0, 0, 1000.0),
        ("PACKAGING STICKER DESIGN\ni. 110 mm x 130 mm\nii. 130 mm x 170 mm\nDelivery date: Week of 19th October 2026",
         2, "DESIGN", 350.0, 0.0, 1, 0.0),
    ]
    for doc_type, doc_id in (("quotation", "QT-003"), ("invoice", "INV-003")):
        for i, (desc, qty, uom, price, disc, foc, total) in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO line_items(doc_type,doc_id,item_no,description,qty,uom,unit_price,disc,foc,total)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (doc_type, doc_id, i, desc, qty, uom, price, disc, foc, total),
            )
    conn.commit()


def next_id(conn, table, col, prefix):
    """Next unique id like QT-004. Scans existing ids, takes max numeric suffix + 1."""
    rows = conn.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ?", (prefix + "%",)).fetchall()
    mx = 0
    for r in rows:
        m = re.search(r"(\d+)\s*$", r[0] or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"{prefix}{mx + 1:03d}"


def money(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def discount_amount(it):
    """The MYR discount for a line, whether disc is a flat amount or a percentage."""
    gross = money(it.get("qty")) * money(it.get("unit_price"))
    if it.get("disc_type") == "pct":
        return round(gross * money(it.get("disc")) / 100.0, 2)
    return money(it.get("disc"))


def compute_line_total(it):
    if it.get("foc"):
        return 0.0
    t = money(it.get("qty")) * money(it.get("unit_price")) - discount_amount(it)
    return round(max(t, 0.0), 2)


# ------------------------------------------------------------------
#  API logic
# ------------------------------------------------------------------
def list_customers(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY customer_id DESC")]


def get_customer(conn, cid):
    r = conn.execute("SELECT * FROM customers WHERE customer_id=?", (cid,)).fetchone()
    return dict(r) if r else None


def create_customer(conn, data):
    cid = next_id(conn, "customers", "customer_id", "CI-")
    conn.execute(
        """INSERT INTO customers(customer_id,name,company,nric_tin,phone,address)
           VALUES(?,?,?,?,?,?)""",
        (cid, data.get("name", ""), data.get("company", ""), data.get("nric_tin", ""),
         data.get("phone", ""), data.get("address", "")),
    )
    conn.commit()
    return get_customer(conn, cid)


def update_customer(conn, cid, data):
    conn.execute(
        """UPDATE customers SET name=?,company=?,nric_tin=?,phone=?,address=? WHERE customer_id=?""",
        (data.get("name", ""), data.get("company", ""), data.get("nric_tin", ""),
         data.get("phone", ""), data.get("address", ""), cid),
    )
    conn.commit()
    return get_customer(conn, cid)


def delete_customer(conn, cid):
    q = conn.execute("SELECT COUNT(*) FROM quotations WHERE customer_id=?", (cid,)).fetchone()[0]
    i = conn.execute("SELECT COUNT(*) FROM invoices WHERE customer_id=?", (cid,)).fetchone()[0]
    if q or i:
        parts = []
        if q:
            parts.append(f"{q} quotation(s)")
        if i:
            parts.append(f"{i} invoice(s)")
        raise LockedError(
            "Can't delete this customer — " + " and ".join(parts) +
            " are linked to it. Remove those documents first."
        )
    conn.execute("DELETE FROM customers WHERE customer_id=?", (cid,))
    conn.commit()


def save_line_items(conn, doc_type, doc_id, items):
    conn.execute("DELETE FROM line_items WHERE doc_type=? AND doc_id=?", (doc_type, doc_id))
    for i, it in enumerate(items or [], start=1):
        conn.execute(
            """INSERT INTO line_items(doc_type,doc_id,item_no,description,qty,uom,unit_price,disc,disc_type,foc,total)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_type, doc_id, i, it.get("description", ""), money(it.get("qty")),
             it.get("uom", ""), money(it.get("unit_price")), money(it.get("disc")),
             "pct" if it.get("disc_type") == "pct" else "amount",
             1 if it.get("foc") else 0, compute_line_total(it)),
        )


def get_line_items(conn, doc_type, doc_id):
    rows = conn.execute(
        "SELECT * FROM line_items WHERE doc_type=? AND doc_id=? ORDER BY item_no",
        (doc_type, doc_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ----- Payments (money received, with receipt proof) -----
def _pid(pid):
    try:
        return int(pid)
    except (TypeError, ValueError):
        return -1


def list_payments(conn, doc_type, doc_id):
    # the receipt blob is excluded so this stays light; has_receipt tells the UI a proof exists
    rows = conn.execute(
        """SELECT id,doc_type,doc_id,amount,paid_at,method,note,receipt_name,created_at,
                  (receipt IS NOT NULL) AS has_receipt
           FROM payments WHERE doc_type=? AND doc_id=? ORDER BY paid_at, id""",
        (doc_type, doc_id),
    ).fetchall()
    return [dict(r) for r in rows]


def payments_total(conn, doc_type, doc_id):
    r = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE doc_type=? AND doc_id=?",
        (doc_type, doc_id),
    ).fetchone()
    return round(float(r[0] or 0), 2)


def sync_invoice_status(conn, iid):
    """Keep an invoice's paid/partial/unpaid tag in step with money received:
       the advance recorded on the invoice plus the balance payments logged against it."""
    row = conn.execute(
        "SELECT subtotal, advance_paid FROM invoices WHERE invoice_id=?", (iid,)
    ).fetchone()
    if not row:
        return None
    sub, adv = money(row[0]), money(row[1])
    received = min(adv + payments_total(conn, "invoice", iid), sub)
    if sub > 0 and received >= sub:
        status = "paid"
    elif received > 0:
        status = "partial"
    else:
        status = "unpaid"
    conn.execute("UPDATE invoices SET status=? WHERE invoice_id=?", (status, iid))
    return status


def add_payment(conn, doc_type, doc_id, data):
    amount = money(data.get("amount"))
    if amount <= 0:
        raise ValueError("Enter a payment amount greater than 0.")
    paid_at = (data.get("paid_at") or "").strip() or \
        __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO payments(doc_type,doc_id,amount,paid_at,method,note,receipt,receipt_name)
           VALUES(?,?,?,?,?,?,?,?)""",
        (doc_type, doc_id, amount, paid_at, data.get("method", ""), data.get("note", ""),
         data.get("receipt"), data.get("receipt_name")),
    )
    status = sync_invoice_status(conn, doc_id) if doc_type == "invoice" else None
    conn.commit()
    return {"ok": True, "payments": list_payments(conn, doc_type, doc_id),
            "paid": payments_total(conn, doc_type, doc_id), "status": status}


def delete_payment(conn, pid):
    row = conn.execute("SELECT doc_type,doc_id FROM payments WHERE id=?", (_pid(pid),)).fetchone()
    conn.execute("DELETE FROM payments WHERE id=?", (_pid(pid),))
    if row:
        status = sync_invoice_status(conn, row[1]) if row[0] == "invoice" else None
        conn.commit()
        return {"ok": True, "payments": list_payments(conn, row[0], row[1]),
                "paid": payments_total(conn, row[0], row[1]), "status": status}
    conn.commit()
    return {"ok": True}


def get_payment_receipt(conn, pid):
    r = conn.execute("SELECT receipt,receipt_name FROM payments WHERE id=?", (_pid(pid),)).fetchone()
    if not r or not r[0]:
        return None
    return {"file": r[0], "filename": r[1] or "receipt"}


# ----- Quotations -----
def list_quotations(conn):
    # Note: signed_file (the base64 blob) is deliberately excluded so the
    # dashboard list stays small/fast. signed_name/at indicate "is it signed".
    rows = conn.execute(
        """SELECT q.quotation_id,q.customer_id,q.date,q.term,q.status,q.subtotal,
                  q.total,q.notes,q.signed_name,q.signed_at,q.created_at,
                  c.name AS customer_name, c.company AS customer_company,
                  (SELECT COALESCE(SUM(amount),0) FROM payments
                     WHERE doc_type='quotation' AND doc_id=q.quotation_id) AS paid
           FROM quotations q LEFT JOIN customers c ON c.customer_id=q.customer_id
           ORDER BY q.quotation_id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_quotation(conn, qid):
    r = conn.execute("SELECT * FROM quotations WHERE quotation_id=?", (qid,)).fetchone()
    if not r:
        return None
    doc = dict(r)
    # don't ship the big PDF blob on every open — expose a flag, fetch the file separately
    doc["has_pdf"] = 1 if doc.get("final_pdf") else 0
    doc.pop("final_pdf", None)
    doc.pop("signed_file", None)  # don't ship the signed blob on open — fetched separately
    doc["customer"] = get_customer(conn, doc["customer_id"])
    doc["items"] = get_line_items(conn, "quotation", qid)
    doc["payments"] = list_payments(conn, "quotation", qid)
    doc["paid"] = payments_total(conn, "quotation", qid)
    return doc


def create_quotation(conn, data):
    qid = next_id(conn, "quotations", "quotation_id", "QT-")
    items = data.get("items", [])
    subtotal = round(sum(compute_line_total(it) for it in items), 2)
    total = subtotal
    conn.execute(
        """INSERT INTO quotations(quotation_id,customer_id,date,term,status,subtotal,total,notes)
           VALUES(?,?,?,?,?,?,?,?)""",
        (qid, data.get("customer_id"), data.get("date"), data.get("term", "50% Advance"),
         data.get("status", "pending"), subtotal, total, data.get("notes", "")),
    )
    save_line_items(conn, "quotation", qid, items)
    conn.commit()
    return get_quotation(conn, qid)


class LockedError(Exception):
    """Raised when trying to edit a quotation that is signed/accepted (locked)."""


def update_quotation(conn, qid, data):
    cur = conn.execute("SELECT status FROM quotations WHERE quotation_id=?", (qid,)).fetchone()
    if cur and cur[0] == "accepted":
        raise LockedError("This quotation has been accepted and signed, so it is locked. "
                          "Remove the signed copy first if you really need to change it.")
    items = data.get("items", [])
    subtotal = round(sum(compute_line_total(it) for it in items), 2)
    conn.execute(
        """UPDATE quotations SET customer_id=?,date=?,term=?,status=?,subtotal=?,total=?,notes=?
           WHERE quotation_id=?""",
        (data.get("customer_id"), data.get("date"), data.get("term"), data.get("status"),
         subtotal, subtotal, data.get("notes", ""), qid),
    )
    save_line_items(conn, "quotation", qid, items)
    conn.commit()
    return get_quotation(conn, qid)


def delete_quotation(conn, qid):
    conn.execute("DELETE FROM line_items WHERE doc_type='quotation' AND doc_id=?", (qid,))
    conn.execute("DELETE FROM payments WHERE doc_type='quotation' AND doc_id=?", (qid,))
    conn.execute("DELETE FROM quotations WHERE quotation_id=?", (qid,))
    conn.commit()


def sign_quotation(conn, qid, data):
    """Attach the client-signed copy and lock the quotation (status -> accepted)."""
    file_data = data.get("file")
    if not file_data:
        raise ValueError("No file was uploaded.")
    conn.execute(
        """UPDATE quotations SET signed_file=?, signed_name=?, signed_at=?, status='accepted'
           WHERE quotation_id=?""",
        (file_data, data.get("filename", "signed"),
         __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qid),
    )
    conn.commit()
    return get_quotation(conn, qid)


def unsign_quotation(conn, qid):
    """Remove the signed copy and reopen the quotation for editing (status -> pending)."""
    conn.execute(
        """UPDATE quotations SET signed_file=NULL, signed_name=NULL, signed_at=NULL, status='pending'
           WHERE quotation_id=?""",
        (qid,),
    )
    conn.commit()
    return get_quotation(conn, qid)


def get_signed_file(conn, qid):
    r = conn.execute(
        "SELECT signed_file, signed_name FROM quotations WHERE quotation_id=?", (qid,)
    ).fetchone()
    if not r or not r[0]:
        return None
    return {"file": r[0], "filename": r[1] or "signed"}


# ----- Invoices -----
def list_invoices(conn):
    # final_pdf blob excluded so the list stays small/fast.
    rows = conn.execute(
        """SELECT i.invoice_id,i.customer_id,i.quotation_id,i.date,i.term,i.status,
                  i.subtotal,i.advance_paid,i.balance_due,i.notes,i.final_pdf_name,i.created_at,
                  c.name AS customer_name, c.company AS customer_company,
                  (SELECT COALESCE(SUM(amount),0) FROM payments
                     WHERE doc_type='invoice' AND doc_id=i.invoice_id) AS paid
           FROM invoices i LEFT JOIN customers c ON c.customer_id=i.customer_id
           ORDER BY i.invoice_id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_invoice(conn, iid):
    r = conn.execute("SELECT * FROM invoices WHERE invoice_id=?", (iid,)).fetchone()
    if not r:
        return None
    doc = dict(r)
    doc["has_pdf"] = 1 if doc.get("final_pdf") else 0
    doc.pop("final_pdf", None)
    doc["customer"] = get_customer(conn, doc["customer_id"])
    doc["items"] = get_line_items(conn, "invoice", iid)
    doc["payments"] = list_payments(conn, "invoice", iid)
    doc["paid"] = payments_total(conn, "invoice", iid)
    return doc


def get_final_pdf(conn, table, col, idval):
    r = conn.execute(
        f"SELECT final_pdf, final_pdf_name FROM {table} WHERE {col}=?", (idval,)
    ).fetchone()
    if not r or not r[0]:
        return None
    return {"file": r[0], "filename": r[1] or (idval + ".pdf")}


def create_invoice(conn, data):
    iid = next_id(conn, "invoices", "invoice_id", "INV-")
    items = data.get("items", [])
    subtotal = round(sum(compute_line_total(it) for it in items), 2)
    advance = money(data.get("advance_paid"))
    if data.get("advance_auto", True) and not data.get("advance_paid"):
        advance = round(subtotal * 0.5, 2)
    balance = round(subtotal - advance, 2)
    conn.execute(
        """INSERT INTO invoices(invoice_id,customer_id,quotation_id,date,term,status,subtotal,advance_paid,balance_due,notes)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (iid, data.get("customer_id"), data.get("quotation_id") or None, data.get("date"),
         data.get("term", "Balance Due"), data.get("status", "unpaid"),
         subtotal, advance, balance, data.get("notes", "")),
    )
    save_line_items(conn, "invoice", iid, items)
    conn.commit()
    return get_invoice(conn, iid)


def update_invoice(conn, iid, data):
    items = data.get("items", [])
    subtotal = round(sum(compute_line_total(it) for it in items), 2)
    advance = money(data.get("advance_paid"))
    balance = round(subtotal - advance, 2)
    conn.execute(
        """UPDATE invoices SET customer_id=?,quotation_id=?,date=?,term=?,status=?,
           subtotal=?,advance_paid=?,balance_due=?,notes=? WHERE invoice_id=?""",
        (data.get("customer_id"), data.get("quotation_id") or None, data.get("date"),
         data.get("term"), data.get("status"), subtotal, advance, balance,
         data.get("notes", ""), iid),
    )
    save_line_items(conn, "invoice", iid, items)
    conn.commit()
    return get_invoice(conn, iid)


def delete_invoice(conn, iid):
    conn.execute("DELETE FROM line_items WHERE doc_type='invoice' AND doc_id=?", (iid,))
    conn.execute("DELETE FROM payments WHERE doc_type='invoice' AND doc_id=?", (iid,))
    conn.execute("DELETE FROM invoices WHERE invoice_id=?", (iid,))
    conn.commit()


def peek_next_ids(conn):
    return {
        "customer": next_id(conn, "customers", "customer_id", "CI-"),
        "quotation": next_id(conn, "quotations", "quotation_id", "QT-"),
        "invoice": next_id(conn, "invoices", "invoice_id", "INV-"),
    }


# ------------------------------------------------------------------
#  Users / team
# ------------------------------------------------------------------
def public_user(u):
    """Strip the password hash before sending a user to the client."""
    if not u:
        return None
    u = dict(u)
    u.pop("pw_hash", None)
    return u


def count_users(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(conn, uid):
    r = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None


def get_user_by_email(conn, email):
    r = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", ((email or "").strip(),)).fetchone()
    return dict(r) if r else None


def list_users(conn):
    rows = conn.execute(
        """SELECT user_id,name,email,role,status,created_at,
                  (signature IS NOT NULL) AS has_signature,
                  (avatar IS NOT NULL) AS has_avatar
           FROM users ORDER BY created_at ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


def create_user(conn, name, email, password, role="member", status="pending"):
    uid = next_id(conn, "users", "user_id", "U-")
    conn.execute(
        """INSERT INTO users(user_id,name,email,pw_hash,role,status)
           VALUES(?,?,?,?,?,?)""",
        (uid, (name or "").strip(), (email or "").strip(), hash_pw(password), role, status),
    )
    conn.commit()
    return get_user(conn, uid)


def update_profile(conn, uid, data):
    u = get_user(conn, uid)
    if not u:
        return None
    name = (data.get("name") or "").strip() if "name" in data else u["name"]
    if not name:
        raise ValueError("Name cannot be empty.")
    email = (data.get("email") or "").strip() if "email" in data else u["email"]
    if not email:
        raise ValueError("Email cannot be empty.")
    if email.lower() != (u["email"] or "").lower():
        other = get_user_by_email(conn, email)
        if other and other["user_id"] != uid:
            raise ValueError("An account with this email already exists.")
    sig = data.get("signature", u["signature"]) if "signature" in data else u["signature"]
    avatar = data.get("avatar", u["avatar"]) if "avatar" in data else u["avatar"]
    conn.execute("UPDATE users SET name=?, email=?, signature=?, avatar=? WHERE user_id=?",
                 (name, email, sig, avatar, uid))
    conn.commit()
    return get_user(conn, uid)


def set_password(conn, uid, password):
    conn.execute("UPDATE users SET pw_hash=? WHERE user_id=?", (hash_pw(password), uid))
    conn.commit()


def change_password(conn, uid, current, new):
    u = get_user(conn, uid)
    if not u:
        raise ValueError("User not found.")
    if not verify_pw(current, u["pw_hash"]):
        raise ValueError("Your current password is incorrect.")
    if len(new or "") < 6:
        raise ValueError("New password must be at least 6 characters.")
    set_password(conn, uid, new)
    return public_user(get_user(conn, uid))


def admin_update_user(conn, uid, data):
    u = get_user(conn, uid)
    if not u:
        return None
    role = data.get("role", u["role"])
    status = data.get("status", u["status"])
    conn.execute("UPDATE users SET role=?, status=? WHERE user_id=?", (role, status, uid))
    conn.commit()
    return get_user(conn, uid)


def delete_user(conn, uid):
    conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
    conn.commit()


# ------------------------------------------------------------------
#  HTTP handler
# ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        pass

    # ---- helpers ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        fname = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fname.startswith(STATIC_DIR) or not os.path.isfile(fname):
            self.send_error(404)
            return
        ctypes = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                  ".css": "text/css", ".ico": "image/x-icon",
                  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".svg": "image/svg+xml", ".webp": "image/webp"}
        ext = os.path.splitext(fname)[1]
        with open(fname, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctypes.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- auth ----
    def _current_user(self, conn):
        h = self.headers.get("Authorization") or ""
        token = h[7:] if h.startswith("Bearer ") else ""
        uid = parse_token(token)
        if not uid:
            return None
        u = get_user(conn, uid)
        if not u or u["status"] != "active":
            return None
        return u

    def _handle_auth(self, conn, path, method, data):
        """Routes under /api/auth/*. Returns True if it handled the request."""
        if path == "/api/auth/register" and method == "POST":
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip()
            pw = data.get("password") or ""
            if not name or not email or not pw:
                self._json({"error": "Name, email and password are all required."}, 400)
                return True
            if len(pw) < 6:
                self._json({"error": "Password must be at least 6 characters."}, 400)
                return True
            if get_user_by_email(conn, email):
                self._json({"error": "An account with this email already exists."}, 400)
                return True
            first = count_users(conn) == 0
            u = create_user(conn, name, email, pw,
                            role="owner" if first else "member",
                            status="active" if first else "pending")
            if first:
                self._json({"token": make_token(u["user_id"]), "user": public_user(u)}, 201)
            else:
                self._json({"pending": True, "user": public_user(u)}, 201)
            return True
        if path == "/api/auth/login" and method == "POST":
            u = get_user_by_email(conn, data.get("email"))
            if not u or not verify_pw(data.get("password"), u["pw_hash"]):
                self._json({"error": "Wrong email or password."}, 401)
                return True
            if u["status"] != "active":
                self._json({"error": "Your account is awaiting approval by the owner."}, 403)
                return True
            self._json({"token": make_token(u["user_id"]), "user": public_user(u)})
            return True
        if path == "/api/auth/reset" and method == "POST":
            u = get_user_by_email(conn, data.get("email"))
            pw = data.get("password") or ""
            if not u:
                self._json({"error": "No account found with that email."}, 404)
                return True
            if len(pw) < 6:
                self._json({"error": "Password must be at least 6 characters."}, 400)
                return True
            set_password(conn, u["user_id"], pw)
            self._json({"ok": True})
            return True
        return False

    # ---- routing ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._static(path)
        conn = get_db()
        try:
            user = self._current_user(conn)
            if not user:
                return self._json({"error": "Not authenticated"}, 401)
            if path == "/api/auth/me":
                return self._json(public_user(user))
            if path == "/api/users":
                if user["role"] != "owner":
                    return self._json({"error": "Owner only"}, 403)
                return self._json(list_users(conn))
            if path == "/api/next-ids":
                return self._json(peek_next_ids(conn))
            m = re.match(r"^/api/payments/([^/]+)/receipt$", path)
            if m:
                f = get_payment_receipt(conn, m.group(1))
                return self._json(f) if f else self._json({"error": "no receipt"}, 404)
            if path == "/api/customers":
                return self._json(list_customers(conn))
            m = re.match(r"^/api/customers/(.+)$", path)
            if m:
                c = get_customer(conn, m.group(1))
                return self._json(c) if c else self._json({"error": "not found"}, 404)
            if path == "/api/quotations":
                return self._json(list_quotations(conn))
            m = re.match(r"^/api/quotations/([^/]+)/signed$", path)
            if m:
                f = get_signed_file(conn, m.group(1))
                return self._json(f) if f else self._json({"error": "no signed copy"}, 404)
            m = re.match(r"^/api/quotations/([^/]+)/pdf$", path)
            if m:
                f = get_final_pdf(conn, "quotations", "quotation_id", m.group(1))
                return self._json(f) if f else self._json({"error": "no pdf"}, 404)
            m = re.match(r"^/api/quotations/(.+)$", path)
            if m:
                d = get_quotation(conn, m.group(1))
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            if path == "/api/invoices":
                return self._json(list_invoices(conn))
            m = re.match(r"^/api/invoices/([^/]+)/pdf$", path)
            if m:
                f = get_final_pdf(conn, "invoices", "invoice_id", m.group(1))
                return self._json(f) if f else self._json({"error": "no pdf"}, 404)
            m = re.match(r"^/api/invoices/(.+)$", path)
            if m:
                d = get_invoice(conn, m.group(1))
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            return self._json({"error": "unknown endpoint"}, 404)
        finally:
            conn.close()

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._body()
        conn = get_db()
        try:
            if path.startswith("/api/auth/"):
                if self._handle_auth(conn, path, "POST", data):
                    return
                return self._json({"error": "unknown endpoint"}, 404)
            user = self._current_user(conn)
            if not user:
                return self._json({"error": "Not authenticated"}, 401)
            if path == "/api/users":  # owner adds an already-approved member
                if user["role"] != "owner":
                    return self._json({"error": "Owner only"}, 403)
                if get_user_by_email(conn, data.get("email")):
                    return self._json({"error": "An account with this email already exists."}, 400)
                u = create_user(conn, data.get("name"), data.get("email"),
                                data.get("password") or secrets.token_hex(8),
                                role=data.get("role", "member"), status="active")
                return self._json(public_user(u), 201)
            if path == "/api/customers":
                return self._json(create_customer(conn, data), 201)
            if path == "/api/quotations":
                return self._json(create_quotation(conn, data), 201)
            m = re.match(r"^/api/quotations/([^/]+)/sign$", path)
            if m:
                return self._json(sign_quotation(conn, m.group(1), data))
            m = re.match(r"^/api/quotations/([^/]+)/payments$", path)
            if m:
                return self._json(add_payment(conn, "quotation", m.group(1), data), 201)
            m = re.match(r"^/api/invoices/([^/]+)/payments$", path)
            if m:
                return self._json(add_payment(conn, "invoice", m.group(1), data), 201)
            if path == "/api/invoices":
                return self._json(create_invoice(conn, data), 201)
            return self._json({"error": "unknown endpoint"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except sqlite3.Error as e:
            return self._json({"error": str(e)}, 400)
        finally:
            conn.close()

    def do_PUT(self):
        path = urlparse(self.path).path
        data = self._body()
        conn = get_db()
        try:
            user = self._current_user(conn)
            if not user:
                return self._json({"error": "Not authenticated"}, 401)
            if path == "/api/auth/me":  # update own profile
                return self._json(public_user(update_profile(conn, user["user_id"], data)))
            if path == "/api/auth/password":  # change own password
                return self._json(change_password(conn, user["user_id"],
                                                  data.get("current_password"), data.get("new_password")))
            m = re.match(r"^/api/users/(.+)$", path)
            if m:
                if user["role"] != "owner":
                    return self._json({"error": "Owner only"}, 403)
                return self._json(public_user(admin_update_user(conn, m.group(1), data)))
            m = re.match(r"^/api/customers/(.+)$", path)
            if m:
                return self._json(update_customer(conn, m.group(1), data))
            m = re.match(r"^/api/quotations/(.+)$", path)
            if m:
                return self._json(update_quotation(conn, m.group(1), data))
            m = re.match(r"^/api/invoices/(.+)$", path)
            if m:
                return self._json(update_invoice(conn, m.group(1), data))
            return self._json({"error": "unknown endpoint"}, 404)
        except LockedError as e:
            return self._json({"error": str(e)}, 409)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except sqlite3.Error as e:
            return self._json({"error": str(e)}, 400)
        finally:
            conn.close()

    def do_DELETE(self):
        path = urlparse(self.path).path
        conn = get_db()
        try:
            user = self._current_user(conn)
            if not user:
                return self._json({"error": "Not authenticated"}, 401)
            m = re.match(r"^/api/users/(.+)$", path)
            if m:
                if user["role"] != "owner":
                    return self._json({"error": "Owner only"}, 403)
                if m.group(1) == user["user_id"]:
                    return self._json({"error": "You can't remove your own owner account."}, 400)
                delete_user(conn, m.group(1))
                return self._json({"ok": True})
            m = re.match(r"^/api/quotations/([^/]+)/sign$", path)
            if m:
                return self._json(unsign_quotation(conn, m.group(1)))
            m = re.match(r"^/api/payments/(.+)$", path)
            if m:
                return self._json(delete_payment(conn, m.group(1)))
            m = re.match(r"^/api/quotations/(.+)$", path)
            if m:
                delete_quotation(conn, m.group(1))
                return self._json({"ok": True})
            m = re.match(r"^/api/invoices/(.+)$", path)
            if m:
                delete_invoice(conn, m.group(1))
                return self._json({"ok": True})
            m = re.match(r"^/api/customers/(.+)$", path)
            if m:
                delete_customer(conn, m.group(1))
                return self._json({"ok": True})
            return self._json({"error": "unknown endpoint"}, 404)
        except LockedError as e:
            return self._json({"error": str(e)}, 409)
        finally:
            conn.close()


def main():
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print("=" * 52)
    print("  UBOLD Quotation & Invoice system is running")
    print(f"  Open:  {url}")
    print(f"  Data:  {DB_PATH}")
    print("  Press Ctrl+C to stop.")
    print("=" * 52)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
