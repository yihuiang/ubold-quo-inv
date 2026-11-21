# Deploying UBOLD to Vercel + Turso

Your app is now a **Vercel** project backed by a **Turso** (cloud SQLite) database.

```
Vercel
├─ public/index.html        ← the app (frontend)
├─ public/ubold-logo.png
└─ api/[...path].js          ← backend (serverless functions)
          │
          └──► Turso database (customers, quotations, invoices, line_items)
```

The tables are created automatically on first use, and a sample (VJ Food) is seeded once.

---

## 1. Create the Turso database

**Easiest — dashboard (no install):**
1. Go to **https://turso.tech** and sign up (free; you can use GitHub).
2. Create a **Database** — name it e.g. `ubold`, pick the nearest region.
3. Open the database page and **copy the Database URL** (looks like
   `libsql://ubold-yourname.turso.io`).
4. Click **Create Token** (or “Connect”) and **copy the auth token** (a long string).

**Or — Turso CLI** (Mac/Linux/WSL):
```
turso auth signup
turso db create ubold
turso db show ubold --url        # Database URL
turso db tokens create ubold     # Auth token
```

Keep the **URL** and **token** — you'll paste them into Vercel.

---

## 2. Put the project on GitHub

From the project folder:
```
git init
git add .
git commit -m "UBOLD quotation & invoice system"
```
Create an empty repo on GitHub, then:
```
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

*(The `.gitignore` already excludes `node_modules`, `.env.local`, and `ubold.db`.)*

---

## 3. Deploy on Vercel

1. Go to **https://vercel.com** → **Add New… → Project** → import your GitHub repo.
2. Framework preset: **Other** (no build step needed). Leave defaults.
3. Open **Settings → Environment Variables** and add these three (for all environments):

   | Name | Value |
   |------|-------|
   | `TURSO_DATABASE_URL` | your `libsql://…` URL from step 1 |
   | `TURSO_AUTH_TOKEN`   | your auth token from step 1 |
   | `APP_SECRET`         | a long random string — signs login tokens. Generate one with `openssl rand -hex 32` and keep it secret. If you ever change it, everyone is signed out. |

4. Click **Deploy**. Vercel installs `@libsql/client` and publishes the site.
5. Open the deployed URL — the app loads, tables are created in Turso, and Save /
   Edit / Delete all write to the cloud database. Done.

> If you add the env vars **after** the first deploy, trigger a redeploy
> (Deployments → ⋯ → Redeploy) so they take effect.

---

## 4. (Optional) Run the Vercel version locally

```
npm install
npm i -g vercel
vercel link
cp .env.local.example .env.local   # then fill in your Turso URL + token
vercel dev                          # http://localhost:3000
```

---

## Notes

- **Two separate data stores:** `python app.py` uses the local `ubold.db` file
  (offline). The deployed site uses **Turso**. Going forward, use the deployed
  version as your real system.
- **Free tier** is far more than enough — your records are tiny text rows.
- To inspect the cloud data, use the Turso dashboard's SQL console, or
  `turso db shell ubold`.
