# 30 — Delivery plan

Everything specified for the depot-to-retail distribution system, staged.

Each stage states what gets built, the exact model and service surface,
how it is proved, and what it unblocks. Stages are ordered so nothing is
built on top of something still undecided.

Cross-references: `docs/28-distribution-spec.md` (schema reconciliation
and the finance model), `docs/29-alerts.md` (warnings),
`docs/18-document-design.md` (document anatomy and rendering),
`docs/21-data-visualization.md` (charts).

---

> **Status: stages 1–12 are built, and so is everything that was
> previously blocked on a decision** — PDF rendering, and the clinical
> checks `docs/29` §3.1 classes as data matches. Drug–drug interaction
> checking remains without data, deliberately: the machinery is built and
> a licensed dataset plugs into it, but no table is authored here. See
> §*Clinical* below.

---

## Where this actually stands

Honest state, not a summary of intentions.

| Stage | State | Notes |
|---|---|---|
| 1 Product range and images | **built** | |
| 2 Cart with unit selection | **built** | |
| 3 Depot inbound recording | **built** | |
| 4 Payment terms and credit | **built** | |
| 5A Audit spine | **built** | `core/audit.py`; every transition writes |
| 5B Order timeline | **built** | `OrderEvent`, both sides read it |
| 5C Document pipeline | **built** | HTML hashed and stored; PDF rendered by Playwright |
| 5D Dispatch logistics | **built** | carrier, vehicle, driver, signature |
| 5E Controlled transfer gate | **built** | hard stop before the ledger moves |
| 6 Transfer payload | **built** | seeds the buyer's draft receipt |
| 7 Alerts | **built** | including the four clinical data-match checks |
| 8 Finance | **built** | computed per range, never stored as periods |
| 9 Dashboards | **built** | four tiles, four charts, table view on each |
| 10 Volume tiers and SRP | **built** | |
| 11 Controlled quotas and extract | **built** | |
| 12 Import documents | **built** | CoA releases a batch, a breach holds one |

**Clinical.** The four §3.1 checks are built — allergy, duplicate
therapy, demographic restriction, maximum daily dose — each reading
effective-dated, sourced `catalog.ClinicalAttribute` rows rather than a
constant. All four are warnings addressed to the pharmacist: a hard stop
would be worked around, while an acknowledgement is written to the audit
stream against their name.

**Interaction checking has machinery and no data, on purpose.**
`sales/interactions.py` defines the provider a licensed dataset
implements, and ships `NoDatasetProvider`, which reports
`NOT_AVAILABLE` rather than an empty result. That distinction is the
whole point: a pharmacist shown nothing concludes the pair was checked
and found safe, so the counter prints "Interactions not checked. No
clinical dataset is licensed on this installation." Licensing one is a
setting, not a rewrite.

**PDF rendering is on.** Playwright is installed and
`DOCUMENT_PDF_BACKEND` defaults to `playwright`; a host that cannot carry
Chromium sets it to `none` and still gets issued, numbered, immutable
documents as stored, hashed HTML.

**585 tests passing**, from a baseline of 343 when this plan was written.

| Area | What works today |
|---|---|
| Ledger | Append-only `StockMovement`; balances derived and rebuildable; FEFO allocation |
| Units | Carton → pack → blister → unit per dosage form; integer conversion; mixed-unit entry |
| Pricing | Per-level derivation with explicit rounding; batch-level cost |
| Catalogue | Full pharmacy range — medicines, sexual health, baby care, first aid, oral care, cosmetics, devices; manufacturers with country and GMP; dosage form, strength, route on `Product`; storage range, light and moisture sensitivity; weight, dimensions, reorder point; product images |
| Distribution | Depot allocation (`offered_base` / `committed_base`), two-stage approval, dispatch with FEFO picking, delivery note |
| Ordering | Cart in the buyer's chosen unit, mixed-unit entry, MOQ and allocation enforced at add time |
| Inbound | Import receipt with FX, batch capture, landed-cost apportionment into `Batch.unit_cost_base` |
| Commerce | Invoices (proforma and tax), payment recording, credit limit hard block, receivables ageing |
| Retail | POS with prescription gating, controlled register, shifts, X/Z, clinical review at the counter |
| Audit | Every transition recorded; alert acknowledgements attributable |
| Documents | Nine kinds, frozen at issue, rendered to PDF |
| Finance | Period reports, expenses, credit notes, write-offs, ageing, dashboards |
| Compliance | Controlled quotas, chain-of-custody forms, regulator extract, import document gates |
| Design | Tokens with a colour validator, DataTable with selection and saved views, centred modals |

---

## Constraints the plan obeys

Already-settled rules; every stage inherits them.

1. **Stock is never mutated.** Only `post_movement()` writes. Balances are a projection.
2. **FEFO, never FIFO.** For medicines, arrival order is the wrong order.
3. **Money is integer minor units.** No `DECIMAL`, no float, currency always explicit.
4. **Regulatory, clinical and threshold values are effective-dated configuration.** A decision from eight months ago stays explainable under the rules that applied then. This now explicitly includes alert thresholds.
5. **Aggregates are computed, never stored as periods.** No `financial_ledger` table with pre-summed columns.
6. **No clinical advice.** Data matches yes; authored clinical judgement no.
7. **The system never prints "net profit".** Gross profit and margin are ours; past that it is an estimated operating result.
8. **Tests are mandatory** for anything touching ledger, FEFO, UoM, money or tax.
9. **Documents are immutable once issued.** A correction is a new version with a visible supersession reference, never an overwrite.

---

## Stage 5 — Audit spine, order tracking, documents

The largest stage, and it splits into five pieces that ship in order.
Everything after this depends on at least one of them.

### 5A — The audit spine

**Build.** `core/audit.py`:

```python
def record(*, action: str, subject, actor: User | None,
           before: dict | None = None, after: dict | None = None,
           organization: Organization | None = None) -> AuditEvent
```

Called from every state-transition service in `commerce`, `inventory`,
`sales` and `finance`. `subject` is any model instance; `subject_type`
and `subject_id` derive from it.

**Why first.** The table is already append-only with the update and
delete grants revoked in production. It has been sitting empty. An
acknowledgement that is not recorded is not an acknowledgement, and a
document with an attestation block that nothing logged is decorative.

**Proof.** Every service in `commerce/services.py` that changes a status
writes exactly one audit row; a test enumerates the transition functions
and asserts the count, so a new transition added without a record fails
the suite.

### 5B — Order timeline

**Model.** `commerce.OrderEvent(BaseModel)`

| Field | Type | Note |
|---|---|---|
| `order` | FK → `PurchaseOrder`, `related_name="events"` | |
| `from_status` / `to_status` | `CharField(20)` | `PurchaseOrderStatus` values |
| `actor` | FK → `User`, null | |
| `actor_organization` | FK → `Organization` | which side moved it |
| `occurred_at` | `DateTimeField` | UTC, rendered Africa/Kigali |
| `note` | `TextField(blank=True)` | rejection reason, partial dispatch note |
| `document_number` | `CharField(30, blank=True)` | the document this stage produced |

**Why a separate table from `AuditEvent`.** The timeline is
cross-organization — the buyer must see "depot approved, 14:20". Audit is
tenant-scoped, internal, and also logs patient-data reads, which a
counterparty must never see. `OrderEvent` is the shared, sanitised view;
`AuditEvent` is the internal record. Both get written.

**Enforcement.** A single private `_transition(order, to_status, actor, note)`
helper in `commerce/services.py`; every status assignment routes through
it. A status can then never change without an event.

**Migration.** Backfill from `submitted_at` and `confirmed_at` on
existing orders so history is not blank on day one.

**API.** Events embedded in the order detail serializer; the buyer and
the supplier each see the same list.

**Frontend.** `OrderTimeline.tsx` — vertical rail, Lucide icons at 16px,
status label 13/400, timestamp and actor 11/400 helper. Lives inside the
order modal; a timeline is inspection, not a workflow, so it does not get
a page.

### 5C — The document pipeline

**New app: `documents/`.**

**Model.** `documents.Document(TenantModel)`

| Field | Type | Note |
|---|---|---|
| `kind` | `CharField(20)`, choices | `PICKING_TICKET`, `DELIVERY_NOTE`, `TAX_INVOICE`, `PROFORMA`, `GRN`, `CONTROLLED_TRANSFER`, `CREDIT_NOTE`, `WRITE_OFF_CERTIFICATE`, … |
| `number` | `CharField(30)` | from `core.sequences`, gap-free |
| `subject_type` / `subject_id` | | the order, shipment, invoice or batch |
| `context` | `JSONField` | **the frozen render context** |
| `pdf` | `FileField` | rendered bytes |
| `sha256` | `CharField(64)` | content hash |
| `version` | `IntegerField(default=1)` | |
| `supersedes` | FK → self, null | |
| `issued_at` / `issued_by` | | |

**Why the context is frozen.** A reissued invoice must show what it
showed then. Re-rendering from live data would silently restate history —
a product renamed, a tax rule superseded, an address corrected — and the
reprint would no longer be the document that was signed.

**Rendering.** `documents/render.py`: Django template → HTML → headless
Chromium via Playwright → PDF, exactly as `docs/18` §Implementation
specifies. Deterministic: the same context produces the same bytes.
Adds `playwright` to `requirements/` and a browser-install step to CI.

**Templates.** `documents/templates/docs/base_document.html` owning the
five regions from `docs/18` §Anatomy — masthead, parties, body,
attestation, footer — plus `print.css` built from the same tokens as the
application. **No document-local colour values.**

Templates shipped in this stage:

| Document | Prefix | Notes |
|---|---|---|
| Picking ticket | `PT-` | internal; FEFO order, grouped by location |
| Delivery note | `DN-` | exists; gains carrier and signature block |
| Commercial tax invoice | `INV-` | per-line tax from the effective-dated `TaxRule` |
| Proforma invoice | `PRO-` | advance payment; new customers, controlled lines |
| Goods receipt note | `GRN-` | four columns — ordered, received, accepted, rejected |
| Controlled substance transfer | `CST-` | signed both ends |

### 5D — Dispatch logistics

**`Shipment` gains:** `carrier`, `vehicle_registration`, `driver_name`,
`driver_licence`, `received_by_name`, `received_by_registration`,
`received_at`, `signature` (image field).

The picking ticket is FEFO-ordered by location then expiry, so the picker
walks the warehouse once and cannot pick the wrong batch by reaching for
the nearest one.

### 5E — Controlled substance gate

**Model.** `commerce.ControlledTransfer(TenantModel)` — shipment,
depot pharmacist (FK → `PharmacistRegistration`), receiving pharmacist
name and council registration number, signed timestamps both ends,
document FK.

`dispatch_order` **refuses** when any line's product is `CONTROLLED` and
no signed transfer exists. Not a warning — a hard stop, because the chain
of custody is the legal artifact.

**Files.** `core/audit.py`, `commerce/models.py`, `commerce/services.py`,
new `documents/` app, `OrdersScreen.tsx`, new `OrderTimeline.tsx`

**Proof.** Every transition writes one `OrderEvent` and one `AuditEvent`;
the same render context twice produces an identical `sha256`; document
numbers are gap-free under concurrent issue; an order with a controlled
line cannot dispatch without a signed `CST-`; a reissued invoice carries
a supersession reference and the original remains readable.

**Unblocks.** Stage 6 (the payload is emitted alongside the delivery
note), stage 7 (acknowledgement needs the audit spine), stage 8 (credit
notes and write-off certificates are documents).

---

## Stage 6 — Transfer payload (ASN)

**Goal.** The retail pharmacy re-keys nothing.

**Build.** `commerce/payloads.py`:

```python
def build_transfer_payload(*, shipment: Shipment) -> dict
def apply_transfer_payload(*, payload: dict, organization: Organization,
                           performed_by: User) -> GoodsReceipt
```

**Envelope.** `{"schema": "medix.transfer/1", "transfer_id": "<DN number>",
"source_organization", "destination_organization", "dispatched_at", "lines": [...]}`

**Per line.** Registration number · GTIN · name · generic name · dosage
form · strength · route · legal status · controlled schedule · cold chain
· storage range · **packaging chain** as an ordered list of
`{code, name, factor_to_base}` · batch number · manufacture date · expiry
date · `quantity_base` · `unit_cost_base` with currency · SRP (stage 10)
· tax treatment · manufacturer `{name, country, gmp_certified}`.

**Three deviations from the supplied payload**, all argued in `docs/28`:

- **Base units only.** `total_packs_on_hand` plus `loose_units_on_hand`
  as two counters will drift and then need reconciling. One number,
  split for display by `core.quantity.split_to_units`.
- **No four-decimal unit cost.** `core.pricing.derive` returns the
  rounding alongside the price so a screen can show it rather than
  absorb it.
- **Tax treatment, not a rate.** A rate frozen in a payload is wrong the
  moment the rule changes; the treatment resolves against the receiver's
  effective-dated `TaxRule`.

**Resolution.** Registration number, then GTIN, **never name** — reusing
`catalog.services.mirror_product`. A line with neither identifier is
rejected rather than name-matched, because a name match creates a
duplicate product with a real batch under it.

**Idempotency.** The payload hash is recorded on the receipt; applying
the same payload twice returns the existing receipt, following the
`_dispatch_key` pattern already in `commerce/services.py`.

**Transport.** Both organizations on the same instance — direct service
call at dispatch, creating a `DRAFT` `GoodsReceipt` pre-filled for the
buyer. Off-instance — signed JSON download and
`POST /api/commerce/receipts/import/`.

**Frontend.** `ReceivingScreen` shows "Pre-filled from DN-2026-00412";
the receiver confirms and corrects rather than types. Discrepancies stay
the point of the GRN — a pre-filled receipt that cannot be corrected
would just hide short deliveries.

**Files.** `commerce/payloads.py`, `commerce/services.py`,
`catalog/services.py`, `ReceivingScreen.tsx`

**Proof.** Round trip: a dispatched order produces a receipt whose lines
match the delivery note exactly. A product the buyer has never held is
created with the packaging chain factor-for-factor. The same payload
applied twice is idempotent. A payload with no registration number and no
GTIN is refused.

---

## Stage 7 — Alerts

**Goal.** The warnings in `docs/29-alerts.md`, without alert fatigue.

### The framework

**Value object.** `core/alerts.py` — frozen dataclass `Alert(code,
severity, title, detail, subject_type, subject_id, requires_ack)`.
Three severities: `CRITICAL`, `WARNING`, `INFO`.

**Severity is behaviour, enforced in the service layer, not the UI:**

| Severity | Service behaviour | UI |
|---|---|---|
| `CRITICAL` | raises `DomainError`; proceeding needs an explicit override with a recorded reason | red banner, blocks the control |
| `WARNING` | refuses unless the caller passes `acknowledged=[codes]` | amber banner above the control |
| `INFO` | no effect on the call | passive; a pill in a row |

**Why acknowledgement is a service argument.** If it were a UI
convention, the API would remain the real boundary and any client could
skip it. The service refusing until the code is passed makes the
acknowledgement a fact rather than a click.

**Thresholds are effective-dated configuration.** `core.AlertRule(TenantModel)`
— code, `threshold` JSON, severity, `effective_from`, `effective_to`.
Ninety days and eighty percent are regulatory-shaped policy, and
`CLAUDE.md` rule 4 covers them: an alert that fired in March must stay
explainable under March's threshold.

**Acknowledgement.** `AlertAcknowledgement(TenantModel)` — code, subject,
actor, reason, timestamp — and a matching `AuditEvent` through 5A.

### The checks

**Operational** — `SHORT_DATED_BATCH` (90 days, WARNING) ·
`BELOW_REORDER_POINT` (the field exists; nothing reads it) ·
`ALLOCATION_EXHAUSTED` · `STORAGE_CLASS_MISMATCH` (product range against
location temperature class, CRITICAL on putaway).

**Compliance** — `REGISTRATION_EXPIRED` (CRITICAL; blocks publish and
dispatch) · `REGISTRATION_EXPIRING` (60 days, WARNING) ·
`BUYER_LICENCE_EXPIRED` (blocks depot confirmation) ·
`CONTROLLED_QUOTA_NEAR` / `_EXCEEDED`.

**Financial** — `CREDIT_LIMIT_EXCEEDED` (built in stage 4; moves under
the framework) · `CREDIT_LIMIT_NEAR` at 80% · `RECEIVABLE_OVERDUE` per
ageing bucket · `SALE_BELOW_COST` when a price change would put a line
under batch cost.

**Clinical** — only `docs/29` §3.1, and only once reference data is
sourced: allergy match, duplicate therapy by category, demographic
restriction. **Interaction checking is blocked on the §3.2 decision** and
ships as nothing rather than as something hand-built.

### Delivery

Inline and attached to the thing it is about. **No toast for anything
requiring action** — it disappears, cannot be re-read, and does not
survive a page change. Three banners per screen maximum, then a summary
row. Nothing actionable announced by colour alone.

Out-of-band — a temperature excursion at 02:00, a receivable crossing 60
days — goes to a `Notification` queue with a delivery adapter stubbed for
email and SMS. That is the only case for leaving the interface.

**Files.** new `core/alerts.py`, `<app>/checks.py` per app, new
`Banner.tsx` and `AlertStack.tsx`, `useAlerts` hook

**Proof.** A boundary test per threshold — 89, 90 and 91 days; 79, 80 and
81 percent. An acknowledgement writes an audit row naming user, alert and
record. A `CRITICAL` cannot be acknowledged past, only overridden with a
reason, and the override is itself audited.

---

## Stage 8 — Finance

**Goal.** Both tiers answer "what did I put in, what did I get back" for
any date range.

**New app: `finance/`.**

### Models

**`ExpenseCategory(TenantModel)`** — name, code, `is_operating`.
Seeded: rent, salaries, transport, utilities, licence fees, cold-chain
power, bank charges.

**`Expense(TenantModel)`** — category, `incurred_on`, `amount_minor`,
currency, description, branch, payee, document FK.

**`CreditNote(TenantModel)` / `DebitNote`** — one model with a `kind`,
raised against an `Invoice`, with lines, reason and amount. Adjusts
receivables and revenue **in the period the note belongs to**, not the
period it was entered.

**`WriteOff(TenantModel)`** — batch, `quantity_base`, reason
(`EXPIRY` / `DAMAGE` / `RECALL`), value in minor units, witness name and
role. Posts a `StockMovement` of kind `EXPIRY_WRITE_OFF` and issues an
**Inventory Write-Off Certificate** through the stage 5 pipeline — a
regulated artifact with a genuine signature block, per `docs/18`.

### Reports

`finance/reports.py`:

```python
def period_report(*, organization: Organization, start: date, end: date,
                  tier: Literal["DEPOT", "RETAIL"]) -> PeriodReport
```

Returning, for an arbitrary range:

| Figure | Source |
|---|---|
| Capital invested | `GoodsReceiptLine` × `Batch.unit_cost_base`, landed cost included |
| Revenue | dispatched order value net of credit notes (depot) · `SaleLine.line_total` split cash and insurance (retail) |
| COGS | `SaleLine` → `Batch.unit_cost_base` — **exact, not averaged** |
| Gross profit | revenue − COGS |
| Gross margin | basis points, integer |
| Expenses | by category |
| Estimated operating result | gross profit − recorded expenses, with a `basis` string naming what it excludes |
| Expiry write-offs | `StockMovement` kind `EXPIRY_WRITE_OFF` × batch cost |
| Stock at risk | batches expiring within 90 days × cost |
| ROI | gross profit ÷ capital invested, basis points |

Plus `receivables_ageing` (extended from `commerce/invoicing.py` with a
per-customer breakdown) and `payables_ageing`.

**COGS is exact here.** Cost lives on the batch, FEFO records which batch
left, and `SaleLine` holds the batch — so the cost of a sale is the cost
of the actual goods, not a moving average over one `wholesale_cost`
column.

**Computed, never stored as periods.** A stored total is only true until
someone backdates a credit note, and then it is quietly wrong with
nothing to say so. It also fixes the periods in advance — "what did I
earn between the 3rd and the 17th" becomes unanswerable. A `ReportCache`
keyed so it can be invalidated is allowed later; the source it is not.

**"Net profit" appears nowhere** — not in a column, a serializer, a
variable name or a label. A pharmacist who reads "net profit: 65,000" and
files a tax return on it has been misled by us.

**API.** `GET /api/finance/period/?from=&to=&tier=` ·
`/api/finance/receivables/` · `/api/finance/expenses/`

**Files.** new `finance/` app — `models.py`, `services.py`, `reports.py`,
`serializers.py`, `views.py`, `tests/`

**Proof.** Gross profit reconciles to the sum of line-level margins. COGS
reconciles to the batch costs of goods that actually left. A backdated
credit note changes the period it belongs to and leaves the current one
alone. Ageing buckets sum to total outstanding. ROI with zero investment
returns null, not a division error.

---

## Stage 9 — Dashboards

**Goal.** The performance view, correct by construction.

**Chart primitives, hand-built as inline SVG** — `LineChart`,
`StackedBar`, `GroupedBar`, `CategoryBar`. No charting library initially:
`docs/21` constrains palette, mark geometry, label placement and
interaction tightly enough that a library would be fought rather than
used. Revisit only if interaction demands it.

**Four tiles.** Total invested · Revenue · Gross profit · ROI.
**No net-profit tile** — `docs/28` §12.3.

**Four charts.**

| Chart | Form | Constraint |
|---|---|---|
| Investment against revenue | line, **single axis** | both are RWF; a second axis invents a scale difference and can be made to show any relationship the author wants |
| Inventory health | stacked bar | stable · slow-moving · expiring within 90 days — the one place the status ramp is correct rather than reserved, because these *are* statuses |
| Revenue by therapeutic class | bar, top 3 + "Other" | 13 categories against four categorical slots in light and three in dark; length carries the comparison, colour carries nothing |
| Sales against collections | grouped bar | credit terms drying up cash has to be visible |

**Every chart carries a table view.** That is the accessibility fallback
and the answer to any contrast complaint.

Depot and retail dashboards are two compositions of the same primitives,
differing in revenue source. Date range control, default last six months,
UTC stored and Africa/Kigali rendered.

**Files.** new `frontend/src/modules/analytics/`, chart primitives under
`components/charts/`

**Proof.** `node scripts/validate-palette.mjs` passes in CI. Every chart
has a table equivalent. No chart uses green, amber or red for a
non-status series.

---

## Stage 10 — Commercial completeness

**Build.**

- **`PriceTier(BaseModel)`** on `VendorListing` — `min_quantity` in
  `price_uom`, `price`. The cart picks the best qualifying tier; the
  payload carries the tier that was applied, so the buyer's cost is
  traceable to a rule rather than to a number someone typed.
- **`VendorListing.srp`** — suggested retail price, flowing through the
  transfer payload to seed the buyer's retail price. The buyer may
  override; it is a starting point, not a controlled price.
- **INFO alert** when a cart line is one tier short of a discount.
- **Negative margin warning** on price update, using SRP against batch
  cost (feeds stage 7's `SALE_BELOW_COST`).

**Proof.** Tier selection at the exact boundary quantity takes the better
price; a tier priced above the tier below it is refused at save.

---

## Stage 11 — Controlled substances and compliance depth

- Controlled quota as effective-dated configuration per organization, per
  schedule, per period; warn approaching, block at the limit, both sides
  of the transaction.
- Regulator extract: controlled register, movement history and
  transfer forms for a date range, as a signed document bundle.

---

## Stage 12 — Import documents (Phase 4)

Anchored on the import receipt and `Shipment`, which already sit between
purchase order and goods receipt.

**Model.** `commerce.ImportDocument(TenantModel)` — kind, number, issuing
party, file, `issued_on`, `expires_on`, `verified_by`, `verified_at`,
linked to the receipt and, where the document is batch-specific, to the
batch.

| Document | Anchor | Gate |
|---|---|---|
| Import licence / permit | organization + product | publish refused without a current permit |
| Proforma invoice | import receipt | — |
| Commercial invoice | import receipt | the landed-cost basis |
| Packing list | shipment | reconciled against received quantities |
| Bill of lading / air waybill | shipment | — |
| **Certificate of Analysis** | **batch** | **batch cannot become sellable without one for a regulated product** |
| Certificate of Origin | import receipt | — |
| Customs declaration (HS codes) | import receipt | duty enters landed cost from here |
| Cold-chain temperature log | shipment | a breach **quarantines** the batch, it does not warn |

The two gates in bold and the quarantine are the point of this stage. The
rest is filing.

---

## Sequencing

```
5A audit spine ─┬─→ 5B timeline ──→ 5C documents ─┬─→ 6 payload
                │                                  ├─→ 8 finance ──→ 9 dashboards
                └─→ 7 alerts ──────────────────────┘
                                                   └─→ 11 compliance
10 commercial ── independent, any time after 6
12 imports ───── independent, largest and last
```

5A is genuinely first: three later stages write audit rows and none of
them can be proved without it. 6 needs 5C because the payload rides with
the delivery note. 8 needs 5C for credit notes and write-off
certificates. 9 needs 8. 7 needs only 5A and can run in parallel with 6.

Recommended order: **5A → 5B → 5C → 5D → 5E → 6 → 7 → 8 → 9 → 10 → 11 → 12**.

Finance is late rather than first, deliberately: a dashboard over
incomplete transaction history is worse than no dashboard, because it
gets believed.

---

## Decisions needed from you

These block work rather than slow it.

1. **Drug interaction data.** License a clinical dataset, or ship no
   interaction alert and say so plainly in the interface. There is no
   safe middle (`docs/29` §3.2). *Blocks the clinical half of stage 7.*
2. **VSDC deployment.** On-premise WAR or a hosted endpoint?
   *Blocks fiscalisation and the local agent.*
3. **Data residency** under Law 058/2021 — must patient data stay in
   Rwanda? *Decides hosting, and therefore where rendered PDFs live.*
4. **Product images** — manufacturer artwork, depot photography, or a
   shared national catalogue? *Affects stage 5's storage decision too.*
5. **One depot per pharmacy, or several?** The model supports several;
   removing price comparison assumed one at a time. *Affects the
   marketplace and the transfer payload's routing.*
6. **Playwright in the deployment target.** Headless Chromium needs
   ~400MB and specific system libraries. If the target cannot carry it,
   the fallback is WeasyPrint — which changes what `docs/18` can promise
   about web-preview parity.

   *No longer blocking.* Stage 5C shipped with rendering split: HTML is
   always produced, stored on the row and hashed, so preview, parity and
   the determinism test all work today. PDF comes from whichever backend
   `DOCUMENT_PDF_BACKEND` names, and from none if none is configured — a
   document without a PDF is still issued, numbered and immutable, and
   can be back-filled from its stored context once the target is
   settled. The decision now changes one setting rather than one stage.

---

## What was learned along the way

Things the plan did not anticipate, recorded because they are the kind
of thing that gets rediscovered expensively.

**`AuditEvent` had never been written to.** The table, the append-only
grants and the revoked update path had all been in place since Phase 0,
and no service had ever inserted a row. Infrastructure that is never
exercised is indistinguishable from infrastructure that does not work.

**Ties in `occurred_at` are real.** Two transitions in the same
millisecond tie, and the database is then free to return them in either
order — which showed up as a test passing alone and failing in a full
run. Both the audit history and the order timeline now sort by id as
well as time; the id is a uuid7, so it breaks the tie the way a reader
expects.

**Moving credit under the alert framework changed what callers catch.**
The wire `code` was identical either way, but the Python exception class
was not. A small registry maps blocking alert codes to their named
exceptions, so `CreditLimitExceeded` stays catchable.

**Pre-filling and correcting are not the same feature.** The advance
notice seeds the buyer's goods receipt, but the first cut posted the
seeded lines as-is — which would have made a short delivery invisible,
the exact failure the GRN exists to catch. Posting now clears the seeded
lines and rewrites them from what the receiver counted.

**A model outside `models.py` may never be registered.** `AlertRule`,
`AlertAcknowledgement` and `ControlledQuota` were first declared beside
their logic in `core/alerts.py` and `core/quotas.py`. Django registers a
model when the module defining it is imported, and those modules are
imported lazily by whichever service needs them — so the app registry
came to depend on import order. It surfaced as the test-database flush
failing to truncate `core_organization`, because a table Django did not
know about was holding a foreign key into it. The tables are declared in
`core/models.py` now; the behaviour stayed where it was.

**A view that overrides `permission_classes` unbinds itself from its
tenant.** `permission_classes = [IsAuthenticated]` replaces the default
pair, and the default pair is what sets the active organization — Django
middleware runs before DRF authenticates, so `TenantScoped` is the first
hook where the user is known. Twenty-four views had written it, and the
failure was silent: `tenant_objects` scoped to nothing and the endpoint
answered with an empty list or a 404 rather than an error. Found by a
404 on an endpoint that plainly should have worked. There is now a test
that walks the URL conf and asserts every API view is bound.

**The buyer's own approval step had no route.** `request_approval` and
`reject_order` had been in `commerce/services.py` since the approval
chain was built, tested and correct, and neither had ever been exposed.
The Orders screen offered one action on a draft — "Submit" — and the
server answered it with *This order is not awaiting approval*, every
time. A pharmacist could build an order from the marketplace and had no
way to place it. Found by using the system rather than by testing it:
the services were green throughout.

**A control nobody can satisfy is not a control.** Separation of duties
refused self-approval outright, which locks out every one-person
pharmacy — a great many Rwandan retail pharmacies, and the shape of the
demo data. The response to such a rule is a second login shared on one
keyboard, which is the same risk with the audit trail switched off. The
rule now applies whenever a colleague exists and records
*self-approved* when one does not.

**Documents were unreachable from the thing they were about.** The
delivery note hangs off the shipment, the invoice off the invoice, the
GRN off the receipt — each correct, each recording the event that
produced it. But asking "what paperwork does this order have" by subject
returned nothing. Survivable while a document screen existed to browse;
fatal once documents moved onto the transaction row. `?related=<order>`
now resolves the chain.

**A tenant boundary can be too tight to be usable.** Documents were
scoped to their issuer, so a pharmacy could not open the delivery note
for the boxes on its own counter. The rule was protecting the picking
ticket — the depot's own shelf locations — and was applied to everything.
The counterparty to an order now reads the documents addressed to them,
and only those.

**An offer of nothing was displayed as available.** A listing with
`availability=AVAILABLE_NOW` and no allocation behind it showed
"Available" with an enabled order button, and refused at the server with
*0 box available* after the buyer had chosen a quantity. Seven of sixty
seeded listings were in that state. The row says "None left" now.


---

## Stage 13 — Documents on the transaction, and usability

Two changes that came from a direction set after the plan was written,
and one that came from using the system rather than testing it.

**The document centre is gone.** Documents were a destination in the
sidebar with its own filters. They are now chips on the row of the
transaction that produced them, opening the document itself. `?related=`
resolves the chain, so an order finds the delivery note on its shipment
and the invoice on its invoice; the counterparty to an order can read
the documents addressed to them, and never the picking ticket. See
`docs/19-screens.md` and `components/data/DocumentChips.tsx`.

**A buyer can see what they are buying.** The marketplace row, card and
detail carry the pack picture, the pack size in words, the manufacturer,
the barcode and the Rwanda FDA registration number. The picture is
verification and never identification — a photo can be of the wrong box,
and a buyer who ordered on artwork alone has no recourse.

**The interface names the work rather than the record.** "Deliveries"
not "Receiving", "Orders to fill" not "Distribution", "Send for
approval" not "Submit". Three primitives support it, in
`components/ui/Guidance.tsx`: `Help` — one sentence on demand beside a
term like *Available*; `NextAction` — what to do now and the button that
does it, because a status is not an instruction; `Consequence` — what a
confirmation will actually do, in place of "are you sure".

**Verification.** The whole trip was walked as a user against a live
server on seeded data — sign in, browse, order, approve, confirm,
dispatch, receive, ask, report. It found five defects that the test
suite was green through, listed under *What was learned along the way*.
The most serious was that a purchase order could not leave draft at all:
the approval services existed, tested and correct, with no route to
them.

**The till could not see the shelf.** The product list the point of sale
searches carried name, prescription status and nothing about stock. A
cashier typing "paracetamol" got two products that looked identical,
tapped one, and read *Paracetamol 120mg/5ml syrup: 0 available, 1
requested* in a red banner — with a patient at the counter, now guessing
which of two results they can sell. The figure existed; it was simply not
on the row.

**And a stock figure without a location is the same wrong answer in a
politer voice.** The first cut annotated the organization's total, which
would have told a front counter there were twenty-four bottles when all
twenty-four were in the cold room. `?location=` scopes it, and the till
passes the one it is selling from. Verified by standing at both: the cold
room shows every paracetamol as *None on the shelf* and disables the row;
the front counter shows 24 and 4,200 and sells from them.

**A disabled button that does not say why is a dead end.** Two of sixty
listings had a depot minimum higher than what the depot had left — a real
state, and one the buyer has to ring the depot about rather than keep
clicking. The order detail now states it: *Minimum is 20, and 8 are
left.*


---

## Stage 14 — What the backend did and no screen offered

A route-level comparison of the URL conf against `frontend/src/lib/api.ts`,
in both directions: a backend route no screen calls, and a client method
no screen calls. Both are the same failure — work that exists, passes its
tests, and cannot be used.

It found four, and two of them were blockers.

**Nobody could record a pharmacist's registration.** The compliance
dashboard reported *dispensing: blocked* and there was no endpoint at all
to record the council registration that unblocks it — so no prescription
could ever be verified, and no prescription-only medicine could ever be
dispensed. Premises licences were the same: capability derives from held
licences, the screen warned that one expires in thirty days, and there was
no way to record the renewal. Both are writable now, and renewal adds a
record rather than editing one, because a licence is evidence of what was
permitted between two dates.

**Quarantined stock had no way out of the interface.** `api.releaseBatch`
existed and nothing called it. Combined with cold-chain auto-quarantine,
a fridge fault could freeze a batch permanently with nothing on any screen
to unfreeze it.

**The Assistant had no screen.** Built the week before with 43 tests and
no way for anybody to ask it anything — precisely the sin the approval
chain had already demonstrated.

**Cold chain had no screen.** The one alert in the system that acts rather
than warns: it quarantines stock without asking, and there was nothing to
say why or to let a person decide about it.

**And a pharmacy on its first day could not raise a prescription** —
"choose a patient" against an empty list, with no way to add one, though
`api.savePatient` and `api.savePrescriber` had both been written.

Also fixed from the previous pass: the batch trace reported
`on_hand_base` and nothing about *where*. docs/09 sets the criterion as
tracing every unit to its current location or its sale; a total reads
identically whether the stock is on one shelf or in four branches, and
nobody can be sent to fetch a total.


---

## Stage 15 — The lists you could not add to

Prompted by one question — *the catalogue has no button to add a
product?* — which turned out to be a class rather than an oversight. A
`ModelViewSet` serves create, update and destroy; if no screen ever POSTs
to it the record can be read and never made.

**A product could not be created.** `ProductWriteSerializer` and the
create path have existed since Phase 1. The editor could change a
product's packaging, images and registration and could not add one, so a
new pharmacy's catalogue was empty and stayed empty. The base unit is
created with it, because a product without one cannot be received,
priced, sold or counted — creating the product alone produces a row that
looks complete and fails at the first movement.

**A depot could offer nothing.** `ListingViewSet` has full CRUD,
`publish_listing()` is tested in six files, `/listings/{id}/tiers` sets
volume pricing — and no screen called any of it. The marketplace only had
rows because `seed_market` put them there, which is the whole depot half
of the business model with no entry point. Worse, `PublishListingSerializer`
had no `offered` field at all, so every listing created through the API
was published with nothing behind it: exactly the "shows Available,
refuses at the server" state seven of the sixty seeded rows were in.

**A pharmacy could not add a room, and a probe could not be registered** —
so the cold-chain screen listed nothing on every deployment.

**Every sale belonged to no day.** The point of sale started each sale
with a location and no till, so the server never resolved a shift and
`sale.shift` was always null. Day end would have reported zero however
much was sold, and the cash-variance control never ran. Not a missing
screen — a link that was not being captured, which no amount of building
the screen later recovers.

### Two bugs the work itself produced

**Enum drift, twice.** `RETAIL_PHARMACY` where the stored value is
`RETAIL`; `FRIDGE` for a location kind that has only `BRANCH` and
`STORE`. Both were written by reading the Python attribute name instead
of its value, both typecheck perfectly, and both surface as a form that
fills in correctly and is rejected on submit. `core/tests/test_enums.py`
now reads the frontend's literals back and fails when they drift — and
caught two more (`GSL`, `ZERO_RATED`) on its first run.

**A duplicate code returned 500.** Every tenant-scoped record with a code
is unique on `(organization, code)`; the organization is injected in
`perform_create()`, so it is not in the serializer's data, so DRF's
`UniqueTogetherValidator` never fires. The database caught it and the
`IntegrityError` reached the user as *Something went wrong. Try again.*
Fixed once in `core/exceptions.py` rather than by adding a validator to
thirty serializers.

### The order tracker

The order drawer showed a history — what had happened. The question
people open an order to ask is *where is it*, and that needs the steps
**not** yet reached as much as the ones that are: three completed events
look identical whether the next move is tomorrow's delivery or a depot
that has not accepted it. `OrderTracker` shows the whole route
vertically, each done step carrying its time, actor and document.

Vertical because the labels are sentences. Laid out horizontally,
"Awaiting confirmation" either truncates or forces every column to the
width of the longest one.

The usual shipment tracker paints the steps ahead in red. Not here — red
is reserved for status, and a step that has not happened yet is not a
fault. Done is green, the current step is the brand colour, what is ahead
is quiet, and the one genuinely red state is an order sent back or
cancelled.


### Documents could not be opened at all

`window.open("/api/v1/documents/<id>/pdf/")` is a plain navigation. The
JWT lives in localStorage and is attached by `request()`; a navigation
carries no headers, so the API answered 401 and the tab showed a JSON
error where a delivery note should have been. Proved by calling the
endpoint both ways: 401 without the token, 200 with it.

Both the HTML and the PDF are fetched with the token now, and the
document is shown **in the app** rather than thrown at a new tab — a
pharmacist checking a delivery note against what arrived wants it beside
the order, not in a window they have to find their way back from. The
preview renders the stored HTML, which is the same bytes the PDF was
rendered from, so it is the document rather than an approximation of it.

The frame is sandboxed with scripts blocked. A document is the one place
product and patient text reaches a browser as markup — Django escapes
every value, and the sandbox is the second lock rather than the first.


---

## Stage 16 — Closing the open list

The four items the previous pass flagged and left. Each had a complete,
tested service behind it and no way to reach it.

**Expired stock could never leave the shelf.**
`finance.services.write_off()` is the only path that removes it from the
ledger, and `api.recordWriteOff` had no caller. An expired batch sat in
inventory value forever, alerting every day and impossible to dispose of.
It belongs on the batch rather than on a finance screen — the person
deciding is holding the box. Witnessed, because the certificate is what
an inspector asks for and one with nobody's name on it is a note.

**Import paper could not be filed, and two pieces of it are gates.** A
registered medicine imported without a Certificate of Analysis is
quarantined on receipt, and a cold-chain log carrying a breach quarantines
too — `_quarantine_reason` refuses to release either. So a depot's
imports held themselves and nothing said why. Filed on the posted
receipt rather than during entry, deliberately: you find out you need a
certificate *after* posting, when the stock turns up held, and asking for
it earlier asks at the one moment nobody yet knows whether it matters.

**Insurance could be read and not set up.** Without a contract
`check_eligibility` finds no cover, so every insured patient was charged
in full — no error, no warning, no claim. The Claims screen read "No
claims" forever and nothing hinted that a setup step was missing, which
is the worst shape a gap can take: it looks like the pharmacy has no
insurance business. Schemes, contracts and coverage rules are writable
now, with capitation shown for what it is — a contract that raises no
claims because the scheme has already paid for the period.

**A submitted claim never resolved.** `record_response()` records what
the scheme allowed line by line and had no caller, so the receivable
never cleared and nothing recorded which line was refused. Per line
because a partial rejection is the common case: *they paid 28,000 of
45,000* does not tell a pharmacy what to fix.

The enum guard now covers the five new constant lists as well. It has
caught four inventions so far and none since.

Two things the closing pass caught in its own work. Insurance setup is
not finished at a contract: eligibility matches a patient to a scheme
through a `Member` row, so a scheme and a contract with no members charge
every patient in full exactly as before — the wrapper was written and
uncalled, which would have been the same gap one layer down. And the
listings table reported how many volume breaks a listing had against a
table only `seed_market` could write.

Seven client wrappers superseded by other calls were deleted rather than
surfaced — `documentsFor`, `financePeriod`, `receipts`, `shipments`,
`startOrder`, `landedCostPreview`, `eligibility`. Keeping them is what
made the parity audit necessary. Three are left on purpose:
`recordAllergy`, `saveControlledQuota` and `saveTaxRule` mark real
missing features, and deleting them would hide the gap rather than close
it.
