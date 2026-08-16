# 19 — Screens

Screen-by-screen specification. Every screen composes a **module template** from [04-design-system.md](04-design-system.md); none lays out from scratch.

Three templates cover nearly everything:

| Template | Structure | Used by |
|---|---|---|
| **List** | Page header → toolbar → DataTable → detail modal | Products, stock, orders, claims, batches |
| **Transaction** | Document header → tabs → sections → item grid → approval timeline | PO, GRN, invoice, claim, import request |
| **Console** | Header → status → grouped sections → timeline → activity | Import request, recall, compliance |

Plus two bespoke screens that earn their exception: **POS** and **Executive overview**.

---

## Shell (every screen)

```
┌──────────────────────────────────────────────────────────────┐
│ Medix   [ Search products, orders…  ⌘K ]        🔔    MU     │
├──────────────┬───────────────────────────────────────────────┤
│ MAIN         │  Inventory                    [ + Adjust ]    │
│  Overview    │  Monitor stock, batches, expiry and movements │
│ OPERATIONS   ├───────────────────────────────────────────────┤
│ ▎Inventory   │                                               │
│  Point of… │  primary workspace                            │
│  Transfers   │                                               │
│ COMMERCE     ├───────────────────────────────────────────────┤
│  Marketplace │  secondary information                        │
│  Orders      │                                               │
├──────────────┴───────────────────────────────────────────────┤
│ ● All systems operational      last sync 10:42        v1.0   │
└──────────────────────────────────────────────────────────────┘
```

Sidebar 248px, grouped, active item tinted with a 2px left border. Top bar in the same neutral family as the workspace with a 1px divider. Search field quiet at `--hover` until focused. Content max-width 1440px.

Navigation groups differ by portal — a wholesale pharmacy has no Point of sale, Prescriptions or Insurance; a retail pharmacy has no Distribution.

---

## 1 — Pharmacist overview (retail home)

**Purpose:** answer *what do I need to do right now?* Not a dashboard.

```
Good morning, Marie                                    20/600
Kigali Care Pharmacy · Tuesday, 11 August              13/400
──────────────────────────────────────────────────────────────
Sales today    Orders    Low stock    Expiry alerts
1.84M          24        18           32
↑ 12.4%        ↑ 8.2%    Needs review 12 critical
──────────────────────────────────────────────────────────────
Expiring batches                                    View all
Cetirizine 10mg   CTZ-4421  Sep 2026    28   ● Critical
Amoxicillin 500mg AMX-0021  Apr 2027   240   ● Expiring
──────────────────────────────────────────────────────────────
Quick actions   [ Sell ] [ Receive stock ] [ Order ] [ Stock ]
```

**Rules.** KPIs are **borderless** — four numbers on the workspace, one hairline beneath. Maximum six. The table gets the only true surface. Quick actions capped at four to six.

**Empty state:** *Nothing needs your attention. Sales and alerts appear here as the day goes on.*

---

## 2 — Marketplace browse

**Template:** List. **Default view: list, not grid.**

Toolbar carries search, filters (category · supplier · availability · price), a `List | Grid` segmented control, and the result count.

**List columns:** Product · Supplier · Stock · Price · Status. Right-aligned numerics with tabular figures.

**Grid** — cards stay small. Image strip, name, form and pack, price with stock remaining, one compact Add. No large image, no description paragraph, no full-width button. Grid is for cosmetics, OTC, devices, consumables and unfamiliar suppliers; list is for procurement.

**Availability affects the row.** `NOT_IN_COUNTRY` shows a `Request import` action instead of a price.

---

## 3 — Product detail

**Template:** split header + tabs.

```
← Products
┌──────────┐  Amoxicillin 500mg                          20/600
│  IMAGE   │  Capsules · 100 pack · MedSupply             13/400
│          │  [Registered]  [Prescription only]
└──────────┘  RWF 28,000
              240 packs available · earliest expiry Apr 2027
              [ Add to order ]
──────────────────────────────────────────────────────────────
Overview | Uses | Suppliers | Batches | Documents
```

**Tabs.** *Overview* — quick facts as a two-column list, not cards. *Uses* — reference information only, with the official leaflet link. *Suppliers* — vendor comparison. *Batches* — batch table with expiry banding. *Documents* — registration, leaflet, supplier and batch documentation, previewed inline.

**Guardrail on the Uses tab:** approved reference information only. Never symptom to drug.

---

## 4 — Point of sale *(retail only, bespoke)*

The one screen with different ergonomics: larger targets, fewer decisions, keyboard and scanner first. Must work offline.

```
Point of sale                        Till 02 · Marie · ● Offline
──────────────────────────────────────────────────────────────
[ Search product or scan barcode…                          ]
──────────────────────────────────────────────────────────────
Amoxicillin 500mg    × 2 packs    AMX-0021         56,000
Paracetamol 500mg    × 6 units    PCM-1022         720
──────────────────────────────────────────────────────────────
⚠ Prescription required
  Amoxicillin is prescription-only. [ Attach prescription ]
──────────────────────────────────────────────────────────────
Subtotal 56,720   Tax 0   Total  RWF 56,720
[ Cash ]  [ Mobile money ]  [ Insurance ]     [ Complete ]
```

**Behaviour.**
- Batch shown per line — FEFO-selected, overridable with a reason.
- Units and packs on the same receipt.
- POM line **blocks** completion. Not a warning.
- Mobile money enters a **pending** state with a visible spinner and a resolve-manually escape.
- Offline indicator is permanent chrome, not an error toast. Offline is normal.
- Tax computed per line; a mixed basket shows a mixed total.
- Whole flow completable by keyboard alone.

---

## 5 — Prescription capture

Full page. Upload or scan → OCR extraction shown as **advisory, clearly labelled** → pharmacist reviews each field → verify.

```
Extracted (unverified)          Confirmed by pharmacist
Patient      J. Mukamana        [ J. Mukamana        ]
Prescriber   Dr K. Habimana     [ Dr K. Habimana     ]
Amoxicillin 500mg × 20          [ 20 ] capsules
                                 ☐ I have verified this prescription
                                 [ Verify and continue ]
```

For a controlled substance, patient **address** becomes a required field, and the screen states why.

---

## 6 — Inventory list

**Template:** List, density control default **compact**.

Columns: ☐ · Product · Batch · Expiry · Qty · Location · Status. Filters for status, expiry band, location, cold chain. Bulk actions: transfer, adjust, quarantine.

Expiry rendered as a semantic dot plus text — never colour alone.

**Row click → modal:** product, batch, stock, expiry, supplier, last movement, `View full history`.

---

## 7 — Stock movement history (the ledger, readable)

```
Amoxicillin 500mg · Batch AMX-0021 · Kigali Main Store
──────────────────────────────────────────────────────────────
DATE     EVENT        IN    OUT   BALANCE   REFERENCE
11 Aug   Sale               20    680       SAL-00982 · Olivier
11 Aug   Transfer           50    630       TRF-0088 → Remera
11 Aug   Purchase     200         700       GRN-00412
```

Read-only by construction. Every row links to its source document. This screen is what an inspector is shown.

---

## 8 — Goods receipt

**Template:** Transaction.

```
Goods Received Note                              GRN-2026-00412
Against PO-2026-00124 · MedSupply · Main Store      ● Draft
──────────────────────────────────────────────────────────────
Overview | Items | Discrepancy | Documents | Approval | History

Product        Ordered  Received  Accepted  Rejected  Batch  Expiry
Amoxicillin      500      480       480        0    [scan] [scan]
```

**GS1 scan fills batch and expiry.** For cold-chain lines, a transport temperature confirmation is required before acceptance. A difference between ordered and received populates the Discrepancy tab automatically — it cannot be edited away.

---

## 9 — Import console *(bespoke, console template)*

```
Import Request                                     IR-2026-00082
Insulin XYZ 100IU · Kigali Care Pharmacy      ● Awaiting approval
──────────────────────────────────────────────────────────────
CONSOLIDATED DEMAND
Kigali Care          100   ✓ committed
Huye Community       200   ✓ committed
Musanze               75     pending
Total demand         425   MOQ 400 met
──────────────────────────────────────────────────────────────
QUOTATIONS
ABC Importers   2,400,000 + 320,000 + 180,000   28 days  ✓
MedSupply       2,310,000 + 350,000 + 195,000   35 days  ✓
──────────────────────────────────────────────────────────────
Allocation policy: pro-rata on short arrival
──────────────────────────────────────────────────────────────
Requested ──● Quoted ──● Approved ──○ Shipped ──○ Arrived
```

Quotations always show the **cost breakdown**, never one number. **Allocation policy is displayed before commitment**, not after arrival — that is the point of showing it.

---

## 10 — Executive overview *(bespoke)*

**Attention before performance.**

```
Business overview · 4 branches
──────────────────────────────────────────────────────────────
Revenue 18.4M ↑12.4%   Gross profit 5.7M ↑8.1%
Stock value 31.2M ↓2.4%  Receivables 4.8M · 2.1M overdue
──────────────────────────────────────────────────────────────
NEEDS ATTENTION
● 18 products expiring within 30 days
● 6 supplier invoices overdue
● Stock shortage on 7 fast-moving products
──────────────────────────────────────────────────────────────
Revenue trend            [ one clean chart ]
──────────────────────────────────────────────────────────────
Branch performance       [ table ]
```

**Never label an unverified figure "net profit"** — *estimated operating result*. Charts must answer a question; no decorative charts.

---

## 11 — Wholesale order fulfilment

**Template:** List → Transaction.

Queue columns: Order · Pharmacy · Value · Required by · Status. Filters by status and date.

Opening an order gives pick list (FEFO-allocated, printable), pack confirmation, dispatch with delivery note, and proof of delivery. Cold-chain orders require a temperature log entry at dispatch.

Customer verification is visible on the order — a pharmacy whose licence has expired shows a block, not a warning.

---

## 12 — Compliance dashboard

Answers *what is about to become a problem.* Grouped list, no cards:

Licences expiring (per branch, per kind) · pharmacist registrations expiring · product registrations expiring or suspended · batches expiring by band · open recalls with incomplete execution · unresolved fiscal exceptions · temperature excursions pending assessment · data protection tasks.

Each row links to the record and the action.

---

## 13 — Day end

```
Shift close · Till 02 · Marie · 11 Aug 2026
──────────────────────────────────────────────────────────────
Sales            1,284,000      Transactions      87
Cash               420,000      Items sold       142
Mobile money       380,000      Returns            2
Insurance          484,000      Discounts     24,000
──────────────────────────────────────────────────────────────
Expected cash      420,000
Counted cash     [ 420,000 ]
Variance                 0
                                    [ X report ] [ Close day ]
```

Variance beyond a configurable threshold requires a reason before closing. This screen is what replaces the notebook.

---

## Cross-cutting

**Loading** — skeletons, never `Loading…`; not on every tiny element.

**Empty** — teach. `No purchase orders yet` / `Your approved purchase orders will appear here.` / `[ Create purchase order ]`

**Error** — explain and offer a next step. `We couldn't submit this order. Your connection may have been interrupted. Your draft has been saved. [ Try again ]`

**Validation** — domain-aware. `Product expiry date cannot be in the past.`

**Autosave** — long workflows show `Draft saved 12 seconds ago`.

**Command palette** — `⌘K`, searching everything and running actions.

**Responsive** — desktop 248px sidebar; tablet collapsed to 68px; mobile bottom navigation. Tables scroll horizontally or become cards. POS supports touch throughout.

---

## Screen review checklist

- [ ] Composes an existing template
- [ ] No new colour, size, spacing or radius introduced
- [ ] Lucide icons only, 16–18px, stroke 1.75–2
- [ ] Uses `DataTable` for any data list
- [ ] Modal for inspection; full page only for a genuine workflow
- [ ] Loading, empty and error states all present
- [ ] Correct in both themes
- [ ] Keyboard operable, visible focus, accessible names
- [ ] Numeric columns use tabular figures
- [ ] Status never conveyed by colour alone
- [ ] UI copy within the length limits in docs/23-ui-copy.md


---

## Documents are not a screen

There is no document centre, and there is no Documents entry in the
navigation. A document is an output of the operation that produced it, so
it lives on that operation's row: a `Documents` column of chips —
`Delivery note · Invoice` — that opens the document itself.

Three reasons this is better than a filing screen:

1. A pharmacist looking for the delivery note for `PO-2026-00001` is
   already looking at `PO-2026-00001`. Sending them elsewhere to filter
   by date and find it again is a detour the system invented for its own
   convenience.
2. The chips read as progress. An order showing `PO · Invoice · Delivery
   note` is further along than one showing `PO`, and that lands from the
   table without opening anything.
3. Nothing has to be filed. Documents are raised by workflows, never by a
   person, so a filing screen would only ever be a second place to look.

The chain is resolved server-side — `?related=<order>` returns the
delivery note on its shipment, the invoice on its invoice and the GRN on
its receipt, because a pharmacist means all of them. See
`components/data/DocumentChips.tsx` and `docs/31-operations.md`.
