// ============================================================
//  UBOLD — serverless API (Vercel)
//  Handles every /api/* request. Mirrors the local Python server.
// ============================================================
import {
  ensureSchema, peekNextIds,
  listCustomers, getCustomer, createCustomer, updateCustomer, deleteCustomer,
  listQuotations, getQuotation, createQuotation, updateQuotation, deleteQuotation,
  signQuotation, unsignQuotation, getSignedFile, getFinalPdf,
  listInvoices, getInvoice, createInvoice, updateInvoice, deleteInvoice,
  addPayment, deletePayment, getPaymentReceipt,
  makeToken, parseToken, verifyPw,
  publicUser, countUsers, getUser, getUserByEmail, listUsers,
  createUser, updateProfile, setPassword, changePassword, adminUpdateUser, deleteUser,
} from '../lib/db.js';
import crypto from 'node:crypto';

function parseBody(req) {
  if (!req.body) return {};
  if (typeof req.body === 'object') return req.body;
  try { return JSON.parse(req.body); } catch { return {}; }
}

async function currentUser(req) {
  const h = req.headers['authorization'] || req.headers['Authorization'] || '';
  const token = h.startsWith('Bearer ') ? h.slice(7) : '';
  const uid = parseToken(token);
  if (!uid) return null;
  const u = await getUser(uid);
  if (!u || u.status !== 'active') return null;
  return u;
}

// Returns a response object if handled, else null.
async function handleAuth(req, res, path, method, body) {
  if (path === 'auth/register' && method === 'POST') {
    const name = String(body.name || '').trim();
    const email = String(body.email || '').trim();
    const pw = body.password || '';
    if (!name || !email || !pw) return res.status(400).json({ error: 'Name, email and password are all required.' });
    if (pw.length < 6) return res.status(400).json({ error: 'Password must be at least 6 characters.' });
    if (await getUserByEmail(email)) return res.status(400).json({ error: 'An account with this email already exists.' });
    const first = (await countUsers()) === 0;
    const u = await createUser(name, email, pw, first ? 'owner' : 'member', first ? 'active' : 'pending');
    return first
      ? res.status(201).json({ token: makeToken(u.user_id), user: publicUser(u) })
      : res.status(201).json({ pending: true, user: publicUser(u) });
  }
  if (path === 'auth/login' && method === 'POST') {
    const u = await getUserByEmail(body.email);
    if (!u || !verifyPw(body.password, u.pw_hash)) return res.status(401).json({ error: 'Wrong email or password.' });
    if (u.status !== 'active') return res.status(403).json({ error: 'Your account is awaiting approval by the owner.' });
    return res.status(200).json({ token: makeToken(u.user_id), user: publicUser(u) });
  }
  if (path === 'auth/reset' && method === 'POST') {
    const u = await getUserByEmail(body.email);
    const pw = body.password || '';
    if (!u) return res.status(404).json({ error: 'No account found with that email.' });
    if (pw.length < 6) return res.status(400).json({ error: 'Password must be at least 6 characters.' });
    await setPassword(u.user_id, pw);
    return res.status(200).json({ ok: true });
  }
  return null;
}

export default async function handler(req, res) {
  try {
    await ensureSchema();
    // Resolve the path segments robustly. Prefer Vercel's catch-all param, but
    // fall back to parsing the URL directly so multi-segment routes (e.g.
    // /api/auth/login, /api/quotations/QT-003) work regardless of how the
    // platform routes the request to this function.
    let seg;
    if (Array.isArray(req.query.path)) {
      seg = req.query.path.filter(Boolean);
    } else if (typeof req.query.path === 'string' && req.query.path) {
      seg = req.query.path.split('/').filter(Boolean);
    } else {
      const rawPath = (req.url || '').split('?')[0].replace(/^\/+/, '');
      seg = rawPath.split('/').filter(Boolean);
      if (seg[0] === 'api') seg = seg.slice(1);
    }
    const method = req.method;
    const body = parseBody(req);
    const fullPath = seg.join('/');

    // ---- /api/auth/* (no token required for register/login/reset) ----
    if (seg[0] === 'auth' && ['register', 'login', 'reset'].includes(seg[1])) {
      const r = await handleAuth(req, res, fullPath, method, body);
      if (r) return r;
      return res.status(404).json({ error: 'unknown endpoint' });
    }

    // ---- everything else requires a valid, active user ----
    const user = await currentUser(req);
    if (!user) return res.status(401).json({ error: 'Not authenticated' });

    const [resource, id, sub] = seg;

    // ---- current user / profile ----
    if (resource === 'auth' && id === 'me') {
      if (method === 'GET') return res.status(200).json(publicUser(user));
      if (method === 'PUT') return res.status(200).json(publicUser(await updateProfile(user.user_id, body)));
    }
    if (resource === 'auth' && id === 'password' && method === 'PUT') {
      return res.status(200).json(await changePassword(user.user_id, body.current_password, body.new_password));
    }

    // ---- /api/users (owner only) ----
    if (resource === 'users') {
      if (user.role !== 'owner') return res.status(403).json({ error: 'Owner only' });
      if (!id) {
        if (method === 'GET') return res.status(200).json(await listUsers());
        if (method === 'POST') {
          if (await getUserByEmail(body.email)) return res.status(400).json({ error: 'An account with this email already exists.' });
          const u = await createUser(body.name, body.email, body.password || crypto.randomBytes(8).toString('hex'),
            body.role || 'member', 'active');
          return res.status(201).json(publicUser(u));
        }
      } else {
        if (method === 'PUT') return res.status(200).json(publicUser(await adminUpdateUser(id, body)));
        if (method === 'DELETE') {
          if (id === user.user_id) return res.status(400).json({ error: "You can't remove your own owner account." });
          await deleteUser(id);
          return res.status(200).json({ ok: true });
        }
      }
    }

    // ---- /api/next-ids ----
    if (resource === 'next-ids' && method === 'GET') {
      return res.status(200).json(await peekNextIds());
    }

    // ---- /api/customers ----
    if (resource === 'customers') {
      if (!id) {
        if (method === 'GET') return res.status(200).json(await listCustomers());
        if (method === 'POST') return res.status(201).json(await createCustomer(body));
      } else {
        if (method === 'GET') {
          const c = await getCustomer(id);
          return c ? res.status(200).json(c) : res.status(404).json({ error: 'not found' });
        }
        if (method === 'PUT') return res.status(200).json(await updateCustomer(id, body));
        if (method === 'DELETE') { await deleteCustomer(id); return res.status(200).json({ ok: true }); }
      }
    }

    // ---- /api/quotations ----
    if (resource === 'quotations') {
      if (!id) {
        if (method === 'GET') return res.status(200).json(await listQuotations());
        if (method === 'POST') return res.status(201).json(await createQuotation(body));
      } else if (sub === 'signed') {
        if (method === 'GET') {
          const f = await getSignedFile(id);
          return f ? res.status(200).json(f) : res.status(404).json({ error: 'no signed copy' });
        }
      } else if (sub === 'pdf') {
        if (method === 'GET') {
          const f = await getFinalPdf('quotations', 'quotation_id', id);
          return f ? res.status(200).json(f) : res.status(404).json({ error: 'no pdf' });
        }
      } else if (sub === 'sign') {
        if (method === 'POST') return res.status(200).json(await signQuotation(id, body));
        if (method === 'DELETE') return res.status(200).json(await unsignQuotation(id));
      } else if (sub === 'payments') {
        if (method === 'POST') return res.status(201).json(await addPayment('quotation', id, body));
      } else {
        if (method === 'GET') {
          const d = await getQuotation(id);
          return d ? res.status(200).json(d) : res.status(404).json({ error: 'not found' });
        }
        if (method === 'PUT') return res.status(200).json(await updateQuotation(id, body));
        if (method === 'DELETE') { await deleteQuotation(id); return res.status(200).json({ ok: true }); }
      }
    }

    // ---- /api/invoices ----
    if (resource === 'invoices') {
      if (!id) {
        if (method === 'GET') return res.status(200).json(await listInvoices());
        if (method === 'POST') return res.status(201).json(await createInvoice(body));
      } else if (sub === 'pdf') {
        if (method === 'GET') {
          const f = await getFinalPdf('invoices', 'invoice_id', id);
          return f ? res.status(200).json(f) : res.status(404).json({ error: 'no pdf' });
        }
      } else if (sub === 'payments') {
        if (method === 'POST') return res.status(201).json(await addPayment('invoice', id, body));
      } else {
        if (method === 'GET') {
          const d = await getInvoice(id);
          return d ? res.status(200).json(d) : res.status(404).json({ error: 'not found' });
        }
        if (method === 'PUT') return res.status(200).json(await updateInvoice(id, body));
        if (method === 'DELETE') { await deleteInvoice(id); return res.status(200).json({ ok: true }); }
      }
    }

    // ---- /api/payments/:id/receipt (GET) and /api/payments/:id (DELETE) ----
    if (resource === 'payments' && id) {
      if (sub === 'receipt' && method === 'GET') {
        const f = await getPaymentReceipt(id);
        return f ? res.status(200).json(f) : res.status(404).json({ error: 'no receipt' });
      }
      if (!sub && method === 'DELETE') return res.status(200).json(await deletePayment(id));
    }

    return res.status(404).json({ error: 'unknown endpoint' });
  } catch (e) {
    return res.status((e && e.status) || 400).json({ error: String((e && e.message) || e) });
  }
}
