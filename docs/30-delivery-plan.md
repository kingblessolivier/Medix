# 30 — Delivery plan

Everything specified for the depot-to-retail distribution system, staged.

Each stage states what gets built, which files move, how it is proved,
and what it unblocks. Stages are ordered so that nothing is built on top
of something still undecided.

Cross-references: `docs/28-distribution-spec.md` (schema reconciliation
and the finance model), `docs/29-alerts.md` (warnings).

---

## Where this actually stands

Honest state, not a summary of intentions.

**Built and tested — 322 tests passing**

| Area | What works today |
|---|---|
| Ledger | Append-only `StockMovement`; balances derived and rebuildable; FEFO allocation |
| Units | Carton → pack → blister → unit per dosage form; integer conversion; mixed-unit entry |
| Pricing | Per-level derivation with explicit rounding; batch-level cost |
| Catalogue | 37 products, 13 therapeutic categories, 5 product types, manufacturers, clinical identity, storage rules |
| Distribution | Depot allocation (`offered_base` / `committed_base`), two-stage approval, dispatch with FEFO picking, delivery note |
| Receiving | GRN against PO, discrepancies, catalogue mirroring by registration then GTIN |
| Retail | POS with prescription gating, controlled register, shifts, X/Z |
| Design | Tokens with a colour validator, DataTable with selection and saved views, centred modals |

**Specified, not built** — everything in stages 1–9 below.

**Blocked on a decision from you** — drug interaction checking
(`docs/29-alerts.md` §3.2) and the three Phase 0 verifications (VSDC
on-premise, data residency, CBHI scope).

---

## Constraints the plan obeys

These are already-settled rules; every stage inherits them.

1. **Stock is never mutated.** Only `post_movement()` writes. Balances are a projection.
2. **FEFO, never FIFO.** For medicines, arrival order is the wrong order.
3. **Money is integer minor units.** No `DECIMAL`, no float, currency always explicit.
4. **Regulatory and clinical thresholds are effective-dated configuration.** A decision from eight months ago stays explainable under the rules that applied then.
5. **Aggregates are computed, never stored as periods.** No `financial_ledger` table with pre-summed columns.
6. **No clinical advice.** Data matches yes; authored clinical judgement no.
7. **The system never says "net profit".** Gross profit and margin are ours; past that it is an estimated operating result.
8. **Tests are mandatory** for anything touching ledger, FEFO, UoM, money or tax.

---

## Stage 1 — Product range and images

**Goal.** A depot catalogue a Rwandan pharmacy recognises, and a product
page a buyer can trust before ordering.

**Build**

- Extend `catalog/reference.py` beyond medicines: sexual health (condoms, lubricants, pregnancy tests, emergency contraception), baby care (formula, nappies, wipes, bottles), first aid, oral care, wound care, mother-and-child.
- `ProductImage` — file, alt text, ordering, primary flag. Storage local in dev, object storage later.
- Marketplace card and product modal show the image; a placeholder when absent is explicit, not a blank.
- Full product detail: brand, generic, strength, form, route, manufacturer with country and GMP, registration number and expiry, storage rules, pack breakdown.

**Why an image matters here.** A buyer ordering from a screen cannot pick
the box up. The image is how they confirm the product is what they meant
and that the presentation matches what they stock. It is verification,
not decoration — which is why alt text is required and the placeholder is
never silently empty.

**Files.** `catalog/models.py`, `catalog/reference.py`, `catalog/serializers.py`, `MarketplaceScreen.tsx`

**Proof.** Seeded catalogue covers every product type; a product with no image renders the explicit placeholder; image upload rejects non-images and oversize files.

---

## Stage 2 — Cart with unit selection

**Goal.** Ordering in the unit the buyer actually wants, down to the
smallest sellable one.

**Build**

- Cart holds lines against a listing **and a chosen UoM**, not a bare number.
- Unit selector offers only levels the depot will sell at that level (`is_sellable` on the wholesale row) — a depot that will not break a pack does not offer tablets.
- Live line total from `core.pricing.derive`, showing the per-unit price and the rounding when it is not exact.
- Quantity entry accepts a mixed count — "10 cartons, 8 packs" — via `core.quantity.compose`.
- MOQ and the depot's remaining allocation both enforced at add time, with the depot's own wording.

**Files.** `commerce/models.py` (cart or reuse the draft order), `commerce/services.py`, `MarketplaceScreen.tsx`, new `CartPanel.tsx`

**Proof.** Adding below MOQ refused; adding beyond the allocation refused; mixed entry produces the right base quantity; derived unit price never below the pack-implied cost.

---

## Stage 3 — Depot inbound recording

**Goal.** The depot records what it imported, with everything a medicine
must carry, in the units it arrived in.

**Build**

- Import receipt: supplier or manufacturer, invoice reference, currency and FX rate with rate date.
- Per line: product, mixed-unit quantity, batch number, manufacture date, expiry date, unit cost.
- Landed cost apportionment — freight, customs duty, clearing — spread across lines by value using `Money.allocate` so the split is exact to the franc, landing in `Batch.unit_cost_base`.
- Batch created on posting; stock enters through `post_movement`.
- Cold-chain lines require a transport temperature confirmation.

**Why landed cost here.** A depot's "total invested" is not the invoice.
Freight and duty are real capital and must sit inside batch cost, or
every downstream margin figure is overstated.

**Files.** `commerce/models.py`, `commerce/services.py`, new `ImportReceiptScreen.tsx`

**Proof.** Apportioned costs sum exactly to the total; a short landed
cost cannot vanish into rounding; posting is idempotent.

---

## Stage 4 — Payment terms and credit

**Goal.** Credit and immediate payment both work, and the depot's cash
position is protected.

**Build**

- Payment terms on the order, defaulting from `TradingRelationship.payment_terms_days`: immediate, Net-15, Net-30, Net-60.
- Proforma invoice when advance payment is required — new pharmacies and controlled drugs.
- Commercial tax invoice on dispatch, with per-line tax from the effective-dated `TaxRule`.
- Payment recording against the invoice; partial payments allowed.
- Credit checks: **hard block** at the limit, **warning** at 80% (`docs/29-alerts.md` §6).
- Receivables ageing: 0–30, 31–60, 61–90, 90+.

**Files.** `commerce/models.py`, new `commerce/invoicing.py`, `commerce/services.py`

**Proof.** An order taking a pharmacy past its limit is refused at
approval, naming the outstanding balance; ageing buckets reconcile to the
sum of unpaid invoices.

---

## Stage 5 — Order tracking and documents

**Goal.** Both sides see where an order is, and every stage leaves a
document.

**Build**

- Timeline on the order: raised → approved internally → sent → depot approved → picking → dispatched → delivered → received, each with actor and timestamp.
- **Picking ticket** — internal depot document, FEFO-ordered by shelf location.
- **Delivery note** — built; add carrier, vehicle, driver and a signature block.
- **Controlled substance transfer form** — required when any line is scheduled, signed both ends.
- Document rendering to PDF, following `docs/18-document-design.md`.

**Files.** new `commerce/documents.py`, `OrdersScreen.tsx`, new `OrderTimeline.tsx`

**Proof.** Every state transition appears on the timeline with an actor;
an order containing a controlled line cannot dispatch without the form.

---

## Stage 6 — Transfer payload

**Goal.** The retail pharmacy re-keys nothing.

**Build**

- Payload emitted on dispatch: products with registration number and GTIN, batch, expiry, quantities in base units, costs, tax treatment, legal status, cold-chain flag.
- Consumed on the buyer's side to pre-fill the goods receipt — the receiver confirms and corrects rather than types.
- Product resolution reuses `mirror_product`: registration number, then GTIN, **never name**.

**Two deviations from the supplied payload**, both already argued in
`docs/28` §10: base units only rather than separate pack and loose
counters, which cannot drift; and unit cost carrying its rounding rather
than four decimal places.

**Files.** `commerce/payloads.py`, `commerce/services.py`, `ReceivingScreen.tsx`

**Proof.** A dispatched order produces a receipt whose lines match the
delivery note exactly; a product the buyer has never held is created with
the right packaging chain; the same payload applied twice is idempotent.

---

## Stage 7 — Alerts

**Goal.** The warnings in `docs/29-alerts.md`, without alert fatigue.

**Build**

- `Alert` value object, three severities, acknowledgement written to `AuditEvent`.
- Operational: short-dated batch at 90 days, stock below reorder point, allocation exhausted.
- Financial: credit limit, receivable ageing, sale below batch cost.
- Compliance: registration expiry blocking publish and dispatch, controlled substance quota.
- The fatigue rules enforced: three per screen, then a summary.

**Not in this stage.** Clinical alerts — §3.1 needs licensed reference
data; interaction checking is blocked on the §3.2 decision.

**Files.** new `core/alerts.py`, per-app check modules, `Banner` usage

**Proof.** Each threshold has a test at the boundary; an acknowledgement
writes an audit row naming user, alert and record.

---

## Stage 8 — Finance

**Goal.** Both tiers answer "what did I put in, what did I get back"
for any date range.

**Build**

- Expense recording — rent, salaries, transport, utilities, licence fees — categorised.
- Period report service computing, for an arbitrary range: capital invested, revenue, COGS from batch cost, gross profit, gross margin, expiry write-offs, estimated operating result.
- Depot and retail variants of the same service, differing in revenue source.
- Receivables and payables ageing.

**Computed, never stored as periods** (`docs/28` §12.1) — so an arbitrary
range is answerable and a backdated credit note corrects history rather
than leaving a stale total.

**Files.** new `finance/` app, `finance/reports.py`

**Proof.** Gross profit reconciles to the sum of line-level margins;
COGS reconciles to batch costs of dispatched or dispensed goods; a
backdated adjustment changes the report for the period it belongs to.

---

## Stage 9 — Dashboards

**Goal.** The performance view, correct by construction.

**Build**

- Four tiles: total invested, revenue, gross profit, ROI. **No "net profit" tile** — `docs/28` §12.3.
- Investment against revenue over time, **single axis** — both are RWF, and a second axis would invent a scale difference.
- Inventory health stacked bar: stable, slow-moving, expiring within 90 days.
- Revenue by therapeutic class — top three plus "Other", because the validated palette has four categorical slots in light and three in dark.
- Sales against collections, grouped, so credit terms drying up cash is visible.
- Every chart has a table view.

**Files.** new `modules/analytics/`, chart primitives

**Proof.** `node scripts/validate-palette.mjs` passes; every chart has a
table equivalent; no chart uses green, amber or red for a non-status
series.

---

## Decisions needed from you

These block work rather than slow it.

1. **Drug interaction data.** License a clinical dataset, or ship no interaction alert and say so in the interface. There is no safe middle (`docs/29` §3.2).
2. **VSDC deployment.** On-premise WAR or a hosted endpoint? Fiscalisation and the local agent both wait on it.
3. **Data residency** under Law 058/2021 — does patient data have to stay in Rwanda? Decides hosting.
4. **Product images** — who supplies them? Manufacturer artwork, depot photography, or a shared national catalogue.
5. **Is the depot the only supplier a retail pharmacy sees**, or may a pharmacy hold relationships with several depots? The model supports several; the removal of price comparison assumed one at a time.

---

## Sequencing

Stages 1–3 are independent and could run in any order. Stage 4 needs 3
for invoice values. Stage 5 needs 4 for the tax invoice. Stage 6 needs 5
for the delivery note. Stages 7–9 need the transaction history that 3–6
produce, which is why finance is late rather than first — a dashboard
over incomplete data is worse than no dashboard.

Recommended order: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9**.
