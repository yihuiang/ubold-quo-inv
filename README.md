# UBOLD Design Lab — Quotation & Invoice System

A small local web app for creating quotations and invoices that match the UBOLD
template. Runs on your own PC, stores everything in a single SQLite file
(`ubold.db`), and works fully offline.

## How to run

**Easiest:** double-click **`Start UBOLD.bat`**. A black window opens and your
browser launches at <http://localhost:5000>. Keep that window open while you work;
close it to stop the app.

**Or** from a terminal in this folder:

```
python app.py
```

Requires Python 3 (already installed). No other installs needed.

## What you can do

- **Customers** — add clients (name, company, NRIC/TIN, phone, address). Each gets
  an auto ID like `CI-004`.
- **New Quotation / New Invoice** — pick a customer (or add one on the spot), enter
  line items (description, qty, UOM, unit price, discount, or mark a line **F.O.C.**).
  Totals, 50% advance, balance due and the amount-in-words are calculated for you.
  IDs (`QT-004`, `INV-004`) are generated automatically and are unique.
- **Save to database** — writes the document and its line items to `ubold.db`.
- **Open / Print** — any saved document renders in the exact UBOLD 2-page layout;
  use **Print / Save PDF** to export.
- **Edit / Delete** — update or remove saved documents.

An example (VJ Food Enterprise — QT-003 / INV-003) is pre-loaded so you can see the
format. Numbering continues from there (next is 004).

## Your data

Everything lives in **`ubold.db`** in this folder — one file. Back it up by copying
it. Delete it to start completely fresh (the example data is re-created on next run).

## Files

| File | What it is |
|------|------------|
| `app.py` | The local server + API (Python standard library only) |
| `schema.sql` | Database structure — 4 tables (customers, quotations, invoices, line_items) |
| `static/index.html` | The app you use in the browser |
| `ubold.db` | Your data (created on first run) |
| `Start UBOLD.bat` | Double-click launcher |
| `ubold-quote-invoice.html` | Standalone single-file version (no server, no database) |
