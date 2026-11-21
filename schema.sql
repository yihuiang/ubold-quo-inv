-- ============================================================
--  UBOLD Design Lab — Quotation & Invoice database schema
--  SQLite. One file = the whole database (ubold.db).
-- ============================================================
PRAGMA foreign_keys = ON;

-- ---------- Users (team members) ----------
CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT PRIMARY KEY,             -- U-001
    name       TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE,
    pw_hash    TEXT NOT NULL,                -- pbkdf2: salt$hash
    role       TEXT NOT NULL DEFAULT 'member',  -- owner / member
    status     TEXT NOT NULL DEFAULT 'pending', -- active / pending (owner approves)
    signature  TEXT,                          -- base64 data URL of saved signature
    avatar     TEXT,                          -- base64 data URL of profile picture
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Customers ----------
CREATE TABLE IF NOT EXISTS customers (
    customer_id  TEXT PRIMARY KEY,            -- CI-003
    name         TEXT NOT NULL,               -- "Ms. Loh Jin Qi"
    company      TEXT,                         -- "VJ FOOD ENTERPRISE  德食品企業"
    nric_tin     TEXT,
    phone        TEXT,
    address      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Quotations ----------
CREATE TABLE IF NOT EXISTS quotations (
    quotation_id TEXT PRIMARY KEY,            -- QT-003
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    date         TEXT,                         -- ISO yyyy-mm-dd
    term         TEXT,                         -- "50% Advance"
    status       TEXT NOT NULL DEFAULT 'pending',-- pending / accepted / expired / rejected
    subtotal     REAL NOT NULL DEFAULT 0,
    total        REAL NOT NULL DEFAULT 0,
    notes        TEXT,
    signed_file  TEXT,                         -- base64 data URL of the client-signed copy
    signed_name  TEXT,                         -- original file name of the signed copy
    signed_at    TEXT,                         -- when the signed copy was uploaded
    final_pdf    TEXT,                         -- base64 data URL of the finalized PDF (shown on open)
    final_pdf_name TEXT,                        -- original file name of the finalized PDF
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Invoices ----------
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id   TEXT PRIMARY KEY,            -- INV-003
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    quotation_id TEXT REFERENCES quotations(quotation_id),  -- "Ref. Quotation" (nullable)
    date         TEXT,
    term         TEXT,                         -- "Balance Due"
    status       TEXT NOT NULL DEFAULT 'unpaid',-- unpaid / partial / paid
    subtotal     REAL NOT NULL DEFAULT 0,
    advance_paid REAL NOT NULL DEFAULT 0,
    balance_due  REAL NOT NULL DEFAULT 0,
    notes        TEXT,
    final_pdf    TEXT,                         -- base64 data URL of the finalized PDF (shown on open)
    final_pdf_name TEXT,                        -- original file name of the finalized PDF
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Line items (shared by quotations & invoices) ----------
CREATE TABLE IF NOT EXISTS line_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type    TEXT NOT NULL,                -- "quotation" | "invoice"
    doc_id      TEXT NOT NULL,                -- FK to quotation_id or invoice_id
    item_no     INTEGER NOT NULL,            -- row order
    description TEXT,                          -- multi-line block
    qty         REAL NOT NULL DEFAULT 1,
    uom         TEXT,                          -- SET / DESIGN / ...
    unit_price  REAL NOT NULL DEFAULT 0,
    disc        REAL NOT NULL DEFAULT 0,      -- discount value (percent when disc_type='pct')
    disc_type   TEXT NOT NULL DEFAULT 'amount', -- 'amount' (MYR off) | 'pct' (% off)
    foc         INTEGER NOT NULL DEFAULT 0,   -- 1 = Free of charge
    total       REAL NOT NULL DEFAULT 0
);

-- ---------- Payments (money actually received, with receipt proof) ----------
CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type     TEXT NOT NULL,                -- "quotation" | "invoice"
    doc_id       TEXT NOT NULL,                -- FK to quotation_id or invoice_id
    amount       REAL NOT NULL DEFAULT 0,      -- amount received (MYR)
    paid_at      TEXT,                          -- date money received (yyyy-mm-dd)
    method       TEXT,                          -- Bank transfer / Cash / Cheque / ...
    note         TEXT,
    receipt      TEXT,                          -- base64 data URL of the receipt / proof
    receipt_name TEXT,                          -- original file name of the receipt
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_line_items_doc   ON line_items(doc_type, doc_id);
CREATE INDEX IF NOT EXISTS idx_quotations_cust  ON quotations(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_cust    ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_payments_doc     ON payments(doc_type, doc_id);
