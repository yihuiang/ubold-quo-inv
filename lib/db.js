// ============================================================
//  UBOLD — data layer (Turso / libSQL)
//  Same 4-table schema as the local SQLite app, but cloud-hosted.
//  Requires env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
// ============================================================
import { createClient } from '@libsql/client';
import crypto from 'node:crypto';

export const db = createClient({
  url: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});

/* ---------- auth: password hashing + signed session tokens ---------- */
const APP_SECRET = process.env.APP_SECRET || 'ubold-dev-secret-change-me';
const TOKEN_TTL = 60 * 60 * 24 * 30; // 30 days

export function hashPw(pw) {
  const salt = crypto.randomBytes(16).toString('hex');
  const dk = crypto.pbkdf2Sync(pw || '', salt, 200000, 32, 'sha256').toString('hex');
  return salt + '$' + dk;
}
export function verifyPw(pw, stored) {
  const [salt, h] = String(stored || '').split('$');
  if (!salt || !h) return false;
  const dk = crypto.pbkdf2Sync(pw || '', salt, 200000, 32, 'sha256').toString('hex');
  try { return crypto.timingSafeEqual(Buffer.from(dk), Buffer.from(h)); } catch { return false; }
}
const b64url = (buf) => Buffer.from(buf).toString('base64url');
export function makeToken(uid) {
  const payload = b64url(JSON.stringify({ uid, iat: Math.floor(Date.now() / 1000) }));
  const sig = b64url(crypto.createHmac('sha256', APP_SECRET).update(payload).digest());
  return payload + '.' + sig;
}
export function parseToken(token) {
  try {
    const [payload, sig] = String(token || '').split('.');
    const expected = b64url(crypto.createHmac('sha256', APP_SECRET).update(payload).digest());
    if (!sig || sig.length !== expected.length
        || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString());
    if (Math.floor(Date.now() / 1000) - (data.iat || 0) > TOKEN_TTL) return null;
    return data.uid;
  } catch { return null; }
}

/* ---------- helpers ---------- */
const round2 = (v) => Math.round((Number(v) || 0) * 100) / 100;
export function money(v) {
  const n = parseFloat(v);
  return isNaN(n) ? 0 : round2(n);
}
export function discountAmount(it) {
  const gross = money(it.qty) * money(it.unit_price);
  if (it && it.disc_type === 'pct') return round2(gross * money(it.disc) / 100);
  return money(it.disc);
}
export function lineTotal(it) {
  if (it && it.foc) return 0;
  const t = money(it.qty) * money(it.unit_price) - discountAmount(it);
  return round2(Math.max(t, 0));
}
const objs = (r) => r.rows.map((row) => ({ ...row }));
const one = (r) => (r.rows.length ? { ...r.rows[0] } : null);

/* ---------- schema (auto-created once per instance) ---------- */
const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS users (
     user_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
     pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
     status TEXT NOT NULL DEFAULT 'pending', signature TEXT, avatar TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))`,
  `CREATE TABLE IF NOT EXISTS customers (
     customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT,
     nric_tin TEXT, phone TEXT, address TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))`,
  `CREATE TABLE IF NOT EXISTS quotations (
     quotation_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
     date TEXT, term TEXT, status TEXT NOT NULL DEFAULT 'draft',
     subtotal REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0, notes TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))`,
  `CREATE TABLE IF NOT EXISTS invoices (
     invoice_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, quotation_id TEXT,
     date TEXT, term TEXT, status TEXT NOT NULL DEFAULT 'unpaid',
     subtotal REAL NOT NULL DEFAULT 0, advance_paid REAL NOT NULL DEFAULT 0,
     balance_due REAL NOT NULL DEFAULT 0, notes TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))`,
  `CREATE TABLE IF NOT EXISTS line_items (
     id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT NOT NULL, doc_id TEXT NOT NULL,
     item_no INTEGER NOT NULL, description TEXT, qty REAL NOT NULL DEFAULT 1, uom TEXT,
     unit_price REAL NOT NULL DEFAULT 0, disc REAL NOT NULL DEFAULT 0,
     disc_type TEXT NOT NULL DEFAULT 'amount',
     foc INTEGER NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0)`,
  `CREATE TABLE IF NOT EXISTS payments (
     id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT NOT NULL, doc_id TEXT NOT NULL,
     amount REAL NOT NULL DEFAULT 0, paid_at TEXT, method TEXT, note TEXT,
     receipt TEXT, receipt_name TEXT,
     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))`,
  `CREATE INDEX IF NOT EXISTS idx_line_items_doc ON line_items(doc_type, doc_id)`,
  `CREATE INDEX IF NOT EXISTS idx_payments_doc ON payments(doc_type, doc_id)`,
];

// Columns added after the first release — applied to existing databases too.
const MIGRATIONS = [
  `ALTER TABLE quotations ADD COLUMN signed_file TEXT`,
  `ALTER TABLE quotations ADD COLUMN signed_name TEXT`,
  `ALTER TABLE quotations ADD COLUMN signed_at TEXT`,
  `ALTER TABLE quotations ADD COLUMN final_pdf TEXT`,
  `ALTER TABLE quotations ADD COLUMN final_pdf_name TEXT`,
  `ALTER TABLE invoices ADD COLUMN final_pdf TEXT`,
  `ALTER TABLE invoices ADD COLUMN final_pdf_name TEXT`,
  `ALTER TABLE line_items ADD COLUMN disc_type TEXT NOT NULL DEFAULT 'amount'`,
];

let _ready;
export function ensureSchema() {
  if (!_ready) {
    _ready = (async () => {
      await db.batch(SCHEMA, 'write');
      for (const sql of MIGRATIONS) {
        try { await db.execute(sql); } catch (e) { /* column already exists */ }
      }
      const c = await db.execute('SELECT COUNT(*) AS n FROM customers');
      if (Number(c.rows[0].n) === 0) await seed();
    })();
  }
  return _ready;
}

async function seed() {
  await db.batch(
    [
      {
        sql: `INSERT INTO customers(customer_id,name,company,nric_tin,phone,address)
              VALUES(?,?,?,?,?,?)`,
        args: ['CI-003', 'Ms. Loh Jin Qi', 'VJ FOOD ENTERPRISE  德食品企業',
          '031122010912', '+60 14-380 4300',
          '18, Jalan Bakawali, Taman Selatan, 83700, Yong Peng, Johor'],
      },
      {
        sql: `INSERT INTO quotations(quotation_id,customer_id,date,term,status,subtotal,total,notes)
              VALUES(?,?,?,?,?,?,?,?)`,
        args: ['QT-003', 'CI-003', '2026-10-02', '50% Advance', 'accepted', 1000, 1000, ''],
      },
      {
        sql: `INSERT INTO invoices(invoice_id,customer_id,quotation_id,date,term,status,subtotal,advance_paid,balance_due,notes)
              VALUES(?,?,?,?,?,?,?,?,?,?)`,
        args: ['INV-003', 'CI-003', 'QT-003', '2026-11-02', 'Balance Due', 'partial', 1000, 500, 500, ''],
      },
    ],
    'write'
  );
  const items = [
    ['PACKAGE A: LOGO DESIGN\nMain logo + 2 concept options\nDelivery date: Week of 19th October 2026', 1, 'SET', 1000, 0, 0, 1000],
    ['PACKAGING STICKER DESIGN\ni. 110 mm x 130 mm\nii. 130 mm x 170 mm\nDelivery date: Week of 19th October 2026', 2, 'DESIGN', 350, 0, 1, 0],
  ];
  const stmts = [];
  for (const [doc_type, doc_id] of [['quotation', 'QT-003'], ['invoice', 'INV-003']]) {
    items.forEach((it, i) => {
      stmts.push({
        sql: `INSERT INTO line_items(doc_type,doc_id,item_no,description,qty,uom,unit_price,disc,foc,total)
              VALUES(?,?,?,?,?,?,?,?,?,?)`,
        args: [doc_type, doc_id, i + 1, it[0], it[1], it[2], it[3], it[4], it[5], it[6]],
      });
    });
  }
  await db.batch(stmts, 'write');
}

/* ---------- id generation ---------- */
export async function nextId(table, col, prefix) {
  const r = await db.execute({
    sql: `SELECT ${col} AS id FROM ${table} WHERE ${col} LIKE ?`,
    args: [prefix + '%'],
  });
  let mx = 0;
  for (const row of r.rows) {
    const m = String(row.id || '').match(/(\d+)\s*$/);
    if (m) mx = Math.max(mx, parseInt(m[1], 10));
  }
  return prefix + String(mx + 1).padStart(3, '0');
}
export async function peekNextIds() {
  return {
    customer: await nextId('customers', 'customer_id', 'CI-'),
    quotation: await nextId('quotations', 'quotation_id', 'QT-'),
    invoice: await nextId('invoices', 'invoice_id', 'INV-'),
  };
}

/* ---------- line items ---------- */
async function saveLineItems(docType, docId, items) {
  const stmts = [
    { sql: 'DELETE FROM line_items WHERE doc_type=? AND doc_id=?', args: [docType, docId] },
  ];
  (items || []).forEach((it, i) => {
    stmts.push({
      sql: `INSERT INTO line_items(doc_type,doc_id,item_no,description,qty,uom,unit_price,disc,disc_type,foc,total)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)`,
      args: [docType, docId, i + 1, it.description || '', money(it.qty), it.uom || '',
        money(it.unit_price), money(it.disc), it.disc_type === 'pct' ? 'pct' : 'amount',
        it.foc ? 1 : 0, lineTotal(it)],
    });
  });
  await db.batch(stmts, 'write');
}
async function getLineItems(docType, docId) {
  const r = await db.execute({
    sql: 'SELECT * FROM line_items WHERE doc_type=? AND doc_id=? ORDER BY item_no',
    args: [docType, docId],
  });
  return objs(r);
}

/* ---------- customers ---------- */
export async function listCustomers() {
  return objs(await db.execute('SELECT * FROM customers ORDER BY customer_id DESC'));
}
export async function getCustomer(cid) {
  return one(await db.execute({ sql: 'SELECT * FROM customers WHERE customer_id=?', args: [cid] }));
}
export async function createCustomer(d) {
  const cid = await nextId('customers', 'customer_id', 'CI-');
  await db.execute({
    sql: `INSERT INTO customers(customer_id,name,company,nric_tin,phone,address) VALUES(?,?,?,?,?,?)`,
    args: [cid, d.name || '', d.company || '', d.nric_tin || '', d.phone || '', d.address || ''],
  });
  return getCustomer(cid);
}
export async function updateCustomer(cid, d) {
  await db.execute({
    sql: `UPDATE customers SET name=?,company=?,nric_tin=?,phone=?,address=? WHERE customer_id=?`,
    args: [d.name || '', d.company || '', d.nric_tin || '', d.phone || '', d.address || '', cid],
  });
  return getCustomer(cid);
}
export async function deleteCustomer(cid) {
  const q = one(await db.execute({ sql: 'SELECT COUNT(*) AS n FROM quotations WHERE customer_id=?', args: [cid] }));
  const i = one(await db.execute({ sql: 'SELECT COUNT(*) AS n FROM invoices WHERE customer_id=?', args: [cid] }));
  const qn = q ? q.n : 0, iN = i ? i.n : 0;
  if (qn || iN) {
    const parts = [];
    if (qn) parts.push(`${qn} quotation(s)`);
    if (iN) parts.push(`${iN} invoice(s)`);
    const e = new Error("Can't delete this customer — " + parts.join(' and ') +
      ' are linked to it. Remove those documents first.');
    e.status = 409;
    throw e;
  }
  await db.execute({ sql: 'DELETE FROM customers WHERE customer_id=?', args: [cid] });
  return { ok: true };
}

/* ---------- quotations ---------- */
export async function listQuotations() {
  // signed_file (base64 blob) excluded so the dashboard list stays lightweight.
  return objs(await db.execute(
    `SELECT q.quotation_id,q.customer_id,q.date,q.term,q.status,q.subtotal,
            q.total,q.notes,q.signed_name,q.signed_at,q.created_at,
            c.name AS customer_name, c.company AS customer_company,
            (SELECT COALESCE(SUM(amount),0) FROM payments
               WHERE doc_type='quotation' AND doc_id=q.quotation_id) AS paid
     FROM quotations q LEFT JOIN customers c ON c.customer_id=q.customer_id
     ORDER BY q.quotation_id DESC`));
}
export async function getQuotation(qid) {
  const doc = one(await db.execute({ sql: 'SELECT * FROM quotations WHERE quotation_id=?', args: [qid] }));
  if (!doc) return null;
  doc.has_pdf = doc.final_pdf ? 1 : 0; delete doc.final_pdf;  // flag only, fetch file separately
  delete doc.signed_file;  // don't ship the signed blob on open — fetched separately
  doc.customer = await getCustomer(doc.customer_id);
  doc.items = await getLineItems('quotation', qid);
  doc.payments = await listPayments('quotation', qid);
  doc.paid = await paymentsTotal('quotation', qid);
  return doc;
}
export async function getFinalPdf(table, col, idval) {
  const r = one(await db.execute({ sql: `SELECT final_pdf, final_pdf_name FROM ${table} WHERE ${col}=?`, args: [idval] }));
  if (!r || !r.final_pdf) return null;
  return { file: r.final_pdf, filename: r.final_pdf_name || (idval + '.pdf') };
}
export async function createQuotation(d) {
  const qid = await nextId('quotations', 'quotation_id', 'QT-');
  const items = d.items || [];
  const subtotal = round2(items.reduce((s, it) => s + lineTotal(it), 0));
  await db.execute({
    sql: `INSERT INTO quotations(quotation_id,customer_id,date,term,status,subtotal,total,notes)
          VALUES(?,?,?,?,?,?,?,?)`,
    args: [qid, d.customer_id, d.date, d.term || '50% Advance', d.status || 'pending', subtotal, subtotal, d.notes || ''],
  });
  await saveLineItems('quotation', qid, items);
  return getQuotation(qid);
}
export async function updateQuotation(qid, d) {
  const cur = one(await db.execute({ sql: 'SELECT status FROM quotations WHERE quotation_id=?', args: [qid] }));
  if (cur && cur.status === 'accepted') {
    const err = new Error('This quotation has been accepted and signed, so it is locked. '
      + 'Remove the signed copy first if you really need to change it.');
    err.status = 409;
    throw err;
  }
  const items = d.items || [];
  const subtotal = round2(items.reduce((s, it) => s + lineTotal(it), 0));
  await db.execute({
    sql: `UPDATE quotations SET customer_id=?,date=?,term=?,status=?,subtotal=?,total=?,notes=? WHERE quotation_id=?`,
    args: [d.customer_id, d.date, d.term, d.status, subtotal, subtotal, d.notes || '', qid],
  });
  await saveLineItems('quotation', qid, items);
  return getQuotation(qid);
}
export async function deleteQuotation(qid) {
  await db.batch([
    { sql: "DELETE FROM line_items WHERE doc_type='quotation' AND doc_id=?", args: [qid] },
    { sql: "DELETE FROM payments WHERE doc_type='quotation' AND doc_id=?", args: [qid] },
    { sql: 'DELETE FROM quotations WHERE quotation_id=?', args: [qid] },
  ], 'write');
}

/* ---------- payments ledger (money received + receipt proof) ---------- */
export async function listPayments(docType, docId) {
  const r = await db.execute({
    sql: `SELECT id,doc_type,doc_id,amount,paid_at,method,note,receipt_name,created_at,
                 (receipt IS NOT NULL) AS has_receipt
          FROM payments WHERE doc_type=? AND doc_id=? ORDER BY paid_at, id`,
    args: [docType, docId],
  });
  return objs(r);
}
export async function paymentsTotal(docType, docId) {
  const r = one(await db.execute({
    sql: 'SELECT COALESCE(SUM(amount),0) AS t FROM payments WHERE doc_type=? AND doc_id=?',
    args: [docType, docId],
  }));
  return round2(r ? r.t : 0);
}
/* Keep an invoice's paid/partial/unpaid tag in step with money received:
   the advance recorded on the invoice plus the balance payments logged against it. */
async function syncInvoiceStatus(iid) {
  const row = one(await db.execute({ sql: 'SELECT subtotal, advance_paid FROM invoices WHERE invoice_id=?', args: [iid] }));
  if (!row) return null;
  const sub = money(row.subtotal), adv = money(row.advance_paid);
  const received = Math.min(adv + await paymentsTotal('invoice', iid), sub);
  const status = (sub > 0 && received >= sub) ? 'paid' : (received > 0 ? 'partial' : 'unpaid');
  await db.execute({ sql: 'UPDATE invoices SET status=? WHERE invoice_id=?', args: [status, iid] });
  return status;
}
export async function addPayment(docType, docId, d) {
  const amount = money(d.amount);
  if (!(amount > 0)) { const e = new Error('Enter a payment amount greater than 0.'); e.status = 400; throw e; }
  const paidAt = (d.paid_at || '').trim() || new Date().toISOString().slice(0, 10);
  await db.execute({
    sql: `INSERT INTO payments(doc_type,doc_id,amount,paid_at,method,note,receipt,receipt_name)
          VALUES(?,?,?,?,?,?,?,?)`,
    args: [docType, docId, amount, paidAt, d.method || '', d.note || '', d.receipt || null, d.receipt_name || null],
  });
  const status = docType === 'invoice' ? await syncInvoiceStatus(docId) : null;
  return { ok: true, payments: await listPayments(docType, docId), paid: await paymentsTotal(docType, docId), status };
}
export async function deletePayment(pid) {
  const row = one(await db.execute({ sql: 'SELECT doc_type,doc_id FROM payments WHERE id=?', args: [Number(pid)] }));
  await db.execute({ sql: 'DELETE FROM payments WHERE id=?', args: [Number(pid)] });
  if (row) {
    const status = row.doc_type === 'invoice' ? await syncInvoiceStatus(row.doc_id) : null;
    return { ok: true, payments: await listPayments(row.doc_type, row.doc_id), paid: await paymentsTotal(row.doc_type, row.doc_id), status };
  }
  return { ok: true };
}
export async function getPaymentReceipt(pid) {
  const r = one(await db.execute({ sql: 'SELECT receipt,receipt_name FROM payments WHERE id=?', args: [Number(pid)] }));
  if (!r || !r.receipt) return null;
  return { file: r.receipt, filename: r.receipt_name || 'receipt' };
}

/* ---------- signed copy (client-signed quotation) ---------- */
export async function signQuotation(qid, d) {
  if (!d || !d.file) { const e = new Error('No file was uploaded.'); e.status = 400; throw e; }
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  await db.execute({
    sql: `UPDATE quotations SET signed_file=?, signed_name=?, signed_at=?, status='accepted' WHERE quotation_id=?`,
    args: [d.file, d.filename || 'signed', now, qid],
  });
  return getQuotation(qid);
}
export async function unsignQuotation(qid) {
  await db.execute({
    sql: `UPDATE quotations SET signed_file=NULL, signed_name=NULL, signed_at=NULL, status='pending' WHERE quotation_id=?`,
    args: [qid],
  });
  return getQuotation(qid);
}
export async function getSignedFile(qid) {
  const r = one(await db.execute({ sql: 'SELECT signed_file, signed_name FROM quotations WHERE quotation_id=?', args: [qid] }));
  if (!r || !r.signed_file) return null;
  return { file: r.signed_file, filename: r.signed_name || 'signed' };
}

/* ---------- invoices ---------- */
export async function listInvoices() {
  // final_pdf blob excluded so the list stays lightweight.
  return objs(await db.execute(
    `SELECT i.invoice_id,i.customer_id,i.quotation_id,i.date,i.term,i.status,
            i.subtotal,i.advance_paid,i.balance_due,i.notes,i.final_pdf_name,i.created_at,
            c.name AS customer_name, c.company AS customer_company,
            (SELECT COALESCE(SUM(amount),0) FROM payments
               WHERE doc_type='invoice' AND doc_id=i.invoice_id) AS paid
     FROM invoices i LEFT JOIN customers c ON c.customer_id=i.customer_id
     ORDER BY i.invoice_id DESC`));
}
export async function getInvoice(iid) {
  const doc = one(await db.execute({ sql: 'SELECT * FROM invoices WHERE invoice_id=?', args: [iid] }));
  if (!doc) return null;
  doc.has_pdf = doc.final_pdf ? 1 : 0; delete doc.final_pdf;
  doc.customer = await getCustomer(doc.customer_id);
  doc.items = await getLineItems('invoice', iid);
  doc.payments = await listPayments('invoice', iid);
  doc.paid = await paymentsTotal('invoice', iid);
  return doc;
}
export async function createInvoice(d) {
  const iid = await nextId('invoices', 'invoice_id', 'INV-');
  const items = d.items || [];
  const subtotal = round2(items.reduce((s, it) => s + lineTotal(it), 0));
  const term = d.term || 'Balance Due';
  const isFull = /full/i.test(term);  // Full Payment term = whole amount due, no advance
  let advance;
  if (d.advance_paid !== undefined && d.advance_paid !== null && d.advance_paid !== '') {
    advance = money(d.advance_paid);
  } else if (isFull) {
    advance = 0;
  } else {
    advance = round2(subtotal * 0.5);
  }
  const balance = round2(subtotal - advance);
  await db.execute({
    sql: `INSERT INTO invoices(invoice_id,customer_id,quotation_id,date,term,status,subtotal,advance_paid,balance_due,notes)
          VALUES(?,?,?,?,?,?,?,?,?,?)`,
    args: [iid, d.customer_id, d.quotation_id || null, d.date, term,
      d.status || 'unpaid', subtotal, advance, balance, d.notes || ''],
  });
  await saveLineItems('invoice', iid, items);
  return getInvoice(iid);
}
export async function updateInvoice(iid, d) {
  const items = d.items || [];
  const subtotal = round2(items.reduce((s, it) => s + lineTotal(it), 0));
  const advance = money(d.advance_paid);
  const balance = round2(subtotal - advance);
  await db.execute({
    sql: `UPDATE invoices SET customer_id=?,quotation_id=?,date=?,term=?,status=?,
          subtotal=?,advance_paid=?,balance_due=?,notes=? WHERE invoice_id=?`,
    args: [d.customer_id, d.quotation_id || null, d.date, d.term, d.status,
      subtotal, advance, balance, d.notes || '', iid],
  });
  await saveLineItems('invoice', iid, items);
  return getInvoice(iid);
}
export async function deleteInvoice(iid) {
  await db.batch([
    { sql: "DELETE FROM line_items WHERE doc_type='invoice' AND doc_id=?", args: [iid] },
    { sql: "DELETE FROM payments WHERE doc_type='invoice' AND doc_id=?", args: [iid] },
    { sql: 'DELETE FROM invoices WHERE invoice_id=?', args: [iid] },
  ], 'write');
}

/* ---------- users / team ---------- */
export function publicUser(u) {
  if (!u) return null;
  const { pw_hash, ...rest } = u;
  return rest;
}
export async function countUsers() {
  const r = await db.execute('SELECT COUNT(*) AS n FROM users');
  return Number(r.rows[0].n);
}
export async function getUser(uid) {
  return one(await db.execute({ sql: 'SELECT * FROM users WHERE user_id=?', args: [uid] }));
}
export async function getUserByEmail(email) {
  return one(await db.execute({ sql: 'SELECT * FROM users WHERE email=? COLLATE NOCASE', args: [String(email || '').trim()] }));
}
export async function listUsers() {
  return objs(await db.execute(
    `SELECT user_id,name,email,role,status,created_at,
            (signature IS NOT NULL) AS has_signature,
            (avatar IS NOT NULL) AS has_avatar
     FROM users ORDER BY created_at ASC`));
}
export async function createUser(name, email, password, role = 'member', status = 'pending') {
  const uid = await nextId('users', 'user_id', 'U-');
  await db.execute({
    sql: `INSERT INTO users(user_id,name,email,pw_hash,role,status) VALUES(?,?,?,?,?,?)`,
    args: [uid, String(name || '').trim(), String(email || '').trim(), hashPw(password), role, status],
  });
  return getUser(uid);
}
export async function updateProfile(uid, d) {
  const u = await getUser(uid);
  if (!u) return null;
  const name = d.name !== undefined ? String(d.name).trim() : u.name;
  if (!name) { const e = new Error('Name cannot be empty.'); e.status = 400; throw e; }
  const email = d.email !== undefined ? String(d.email).trim() : u.email;
  if (!email) { const e = new Error('Email cannot be empty.'); e.status = 400; throw e; }
  if (email.toLowerCase() !== String(u.email || '').toLowerCase()) {
    const other = await getUserByEmail(email);
    if (other && other.user_id !== uid) { const e = new Error('An account with this email already exists.'); e.status = 400; throw e; }
  }
  const sig = d.signature !== undefined ? d.signature : u.signature;
  const avatar = d.avatar !== undefined ? d.avatar : u.avatar;
  await db.execute({
    sql: `UPDATE users SET name=?, email=?, signature=?, avatar=? WHERE user_id=?`,
    args: [name, email, sig, avatar, uid],
  });
  return getUser(uid);
}
export async function setPassword(uid, password) {
  await db.execute({ sql: `UPDATE users SET pw_hash=? WHERE user_id=?`, args: [hashPw(password), uid] });
}
export async function changePassword(uid, current, next) {
  const u = await getUser(uid);
  if (!u) { const e = new Error('User not found.'); e.status = 404; throw e; }
  if (!verifyPw(current, u.pw_hash)) { const e = new Error('Your current password is incorrect.'); e.status = 400; throw e; }
  if ((next || '').length < 6) { const e = new Error('New password must be at least 6 characters.'); e.status = 400; throw e; }
  await setPassword(uid, next);
  return publicUser(await getUser(uid));
}
export async function adminUpdateUser(uid, d) {
  const u = await getUser(uid);
  if (!u) return null;
  await db.execute({
    sql: `UPDATE users SET role=?, status=? WHERE user_id=?`,
    args: [d.role !== undefined ? d.role : u.role, d.status !== undefined ? d.status : u.status, uid],
  });
  return getUser(uid);
}
export async function deleteUser(uid) {
  await db.execute({ sql: `DELETE FROM users WHERE user_id=?`, args: [uid] });
}
