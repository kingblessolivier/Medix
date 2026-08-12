# 28 — Distribution specification

The depot-to-retail model, specified. This is the working backlog for the
distribution core: what exists, what is missing, and the places where the
supplied schema conflicts with a rule Medix already holds.

Nothing here is aspiration. Each row is either **built**, **partial**, or
**open**, and the open ones are the queue.

---

## 1 — What the system is

A **closed distribution network**, not an open marketplace.

A depot admits specific retail pharmacies (`TradingRelationship`). Those
pharmacies see that depot's published catalogue, order from it, and
receive stock from it. There is no shopping around between depots, which
is why vendor price comparison was removed rather than left unused.

```
[Manufacturer / importer]
      ↓  import documents, customs, Rwanda FDA
[Depot pharmacy]          ← publishes an allocation, not its stock
      ↓  purchase order → two approvals → picking → dispatch
[Retail pharmacy]         ← receives, stock lands without re-keying
      ↓
[Patient]
```

---

## 2 — Three conflicts with the supplied schema

These are not preferences. Each one is a rule already in `CLAUDE.md`, and
each exists because breaking it costs something specific.

### 2.1 `quantity_on_hand` must not be a column

The supplied `product_inventory` table holds `quantity_on_hand INT` and
mutates it. Medix does not have that column and must not gain one.

Stock is an **append-only ledger** of `StockMovement` rows; balances are a
derived projection that can be rebuilt from the movements at any time. A
mutable counter cannot answer "who changed this, when, and why", which is
the question a recall and a regulator both ask. `quantity_allocated` and
`quantity_available` map cleanly onto `committed_base` and
`available_base`, which are allocation figures on the *listing* — not
stock — so those are fine.

### 2.2 FEFO, not FIFO

The supplied schema specifies FIFO twice. For medicines that is wrong.

FIFO ships what arrived first. A carton received in January expiring in
2030 would go out before one received in February expiring next month —
and the short-dated stock dies on the shelf. **First Expired First Out**
is the rule, and `inventory.services.allocate_fefo` implements it.

### 2.3 Money is integer minor units, and tax is dated

`DECIMAL(10,2)` for money and `DECIMAL(10,4)` for unit cost are both
refused. Money is `BigIntegerField` in minor units with an explicit
currency; binary floating point and silent precision changes have no
place near a price. Sub-minor-unit precision in `unit_cost` is a symptom
of the division problem solved in `core/pricing.py`.

`tax_rate_percentage` as a plain column is also refused. Tax is
**effective-dated configuration** (`sales.TaxRule`): a transaction from
six months ago must stay explainable under the rules that applied then,
which a single mutable percentage cannot do.

One smaller note: **NDC is US-specific.** Rwanda identifies a product by
its Rwanda FDA registration number, with GS1 GTIN as the barcode. Both
exist on `Product` / `ProductRegistration`.

---

## 3 — Product master

| Supplied field | Medix | State |
|---|---|---|
| `ndc_or_barcode` | `Product.gtin` + `ProductRegistration.registration_number` | built |
| `brand_name` | `Product.brand` | built |
| `generic_name` | `Product.generic_name` | built |
| `drug_class` | `Product.category` (therapeutic) | built |
| `dosage_form` | `ProductRegistration.dosage_form` | partial — belongs on `Product` |
| `strength` | `ProductRegistration.strength` | partial — belongs on `Product` |
| `route_of_administration` | `ProductRegistration.route` | partial |
| `manufacturer_id` | free-text `ProductRegistration.manufacturer` | **open** — needs a `Manufacturer` table |
| `regulatory_license_number` | `ProductRegistration.registration_number` | built |
| `is_active` | `Product.is_active` | built |

**Open:** a `Manufacturer` model with `country_of_origin` and
`gmp_certified`, replacing the free-text field. GMP status is a purchasing
decision, not a label.

---

## 4 — Commercial

| Supplied field | Medix | State |
|---|---|---|
| `wholesale_cost` | `VendorListing.price` | built |
| `suggested_retail_price` | — | **open** |
| `tax_rate_percentage` | `sales.TaxRule`, effective-dated | built, deliberately different (§2.3) |
| `minimum_order_quantity` | `VendorListing.moq` | built |
| `is_tax_exempt` | `Product.tax_treatment` | built |
| volume discount tiers | — | **open** |

**Open:** `PriceTier` (listing, min quantity, price) for volume breaks,
and SRP on the listing so the retail side inherits a starting price.

---

## 5 — Inventory and logistics

| Supplied field | Medix | State |
|---|---|---|
| `pack_size_description` | derived from the UoM chain | built |
| `units_per_pack` | `UnitOfMeasure.factor_to_base` | built |
| `quantity_on_hand` | `StockBalance`, derived from the ledger | built, deliberately different (§2.1) |
| `quantity_allocated` | `VendorListing.committed_base` | built |
| `quantity_available` | `VendorListing.available_base` | built |
| `weight_kg` | — | **open** |
| `box_dimensions` | — | **open** |
| `reorder_point` | — | **open** |

---

## 6 — Compliance and storage

| Supplied field | Medix | State |
|---|---|---|
| `prescription_status` | `Product.legal_status` (OTC / POM / CONTROLLED) | built |
| `controlled_schedule` | `Product.controlled_schedule` | built |
| `requires_cold_chain` | `Product.cold_chain` | built |
| `min`/`max_storage_temp_celsius` | `Location.temperature_class` only | **open** — needs a per-product range |
| `light_sensitive` | — | **open** |
| `moisture_sensitive` | — | **open** |

Behind-the-counter (`BTC`) is a US/UK category with no Rwanda FDA
equivalent; OTC / POM / CONTROLLED is the local classification.

---

## 7 — Batches

Built. `inventory.Batch` carries batch number, expiry, manufacture date
and cold-chain flag; quantity comes from the ledger rather than a column
(§2.1), and rotation is FEFO (§2.2).

---

## 8 — Order lifecycle

Built through dispatch:

```
DRAFT
  → PENDING_APPROVAL     pharmacist raises
  → SUBMITTED            owner or manager releases  ── or REJECTED, with a reason
  → CONFIRMED            depot approves; the offer is committed
  → PREPARING            depot picks
  → DISPATCHED           stock leaves the depot ledger, delivery note raised
  → RECEIVED             stock enters the retail ledger
```

**Open:** payment terms on the order (immediate, credit with agreed days
— `TradingRelationship` already carries `credit_limit` and
`payment_terms_days` but nothing reads them), and a visible tracking
timeline for the buyer.

---

## 9 — Documents

### Inbound — importation

| Document | State |
|---|---|
| Import licence / permit | **open** |
| Proforma invoice | **open** |
| Commercial invoice | **open** |
| Packing list | **open** |
| Bill of lading / air waybill | **open** |
| Certificate of Analysis, per batch | **open** |
| Certificate of Origin | **open** |
| Customs import declaration (HS codes) | **open** |
| Cold-chain temperature log | **open** |

These belong to Phase 4 (imports) on the roadmap. The `Shipment` model is
the natural anchor: it already sits between purchase order and goods
receipt, and its docstring notes transport temperature and tracking as
still to come.

### Outbound — depot to retail

| Document | State |
|---|---|
| Purchase order | built (numbered `PO-YYYY-NNNNN`) |
| Delivery note | built (`DN-YYYY-NNNNN`, carries picked batch and expiry) |
| Goods receipt note | built (`GRN-YYYY-NNNNN`, with discrepancies) |
| Picking ticket | **open** |
| Proforma invoice (local) | **open** |
| Commercial tax invoice | **open** |
| Advance Shipping Notice / transfer payload | **open** — §10 |
| Controlled substance transfer form | **open** — required for scheduled drugs, signed both ends |

---

## 10 — The transfer payload

The retail pharmacy must not re-key what the depot already knows.

Half of this exists. `catalog.services.mirror_product` already resolves a
bought product into the buyer's own catalogue — matching on registration
number, then GTIN, **never on name** — and copies the packaging chain
factor for factor. The delivery note already carries the batch number and
expiry that were actually picked.

What is missing is the payload itself: a document emitted on dispatch and
consumed on receipt, so the buyer's goods receipt arrives pre-filled
instead of typed.

Two notes on the supplied payload shape:

- `total_packs_on_hand` and `loose_units_on_hand` as two counters will
  drift. Medix stores base units and derives the split for display via
  `core.quantity.split_to_units`, so there is one number and no
  reconciliation.
- `unit_cost` at four decimal places is the rounding problem in §2.3.
  `core.pricing.derive` returns the rounding alongside the price so a
  screen can show it rather than absorb it.

---

## 11 — Product range

A pharmacy is not only medicines. `catalog/reference.py` covers medicine,
consumable, device, supplement and cosmetic across 13 therapeutic
categories.

**Open:** the range still needs sexual health (condoms, lubricants,
pregnancy tests, emergency contraception), baby care (formula, nappies,
wipes), first aid, oral care, and mother-and-child lines — all of which a
Rwandan pharmacy sells and a depot distributes.

---

## 12 — Finance

Both sides need the same question answered — *what did I put in, what did
I get back, over this period* — but the shape of the answer differs. A
depot lives on volume at a thin margin; a pharmacy lives on a fatter
margin over fewer units.

### 12.1 Do not store the ledger as periods

The supplied `depot_financial_ledger` and `retail_financial_ledger` are
period tables: a row per date range, with the totals already summed into
columns. Medix should not have them, for the same reason it has no
`quantity_on_hand` (§2.1).

A stored total is only true until someone backdates a credit note, and
then it is quietly wrong with nothing to say so. It also fixes the
periods in advance — "what did I earn between the 3rd and the 17th"
becomes unanswerable unless a job happened to bucket it that way.

Every figure below is **computable for any arbitrary date range** from
records that already exist: `Sale`, `SaleLine`, `Payment`, `StockMovement`,
`Batch.unit_cost_base`, `GoodsReceipt`. Materialise later as a cache if a
query gets slow, keyed so it can be invalidated — never as the source.

`DECIMAL(15,2)` is refused throughout, as in §2.3.

### 12.2 COGS is exact here, not estimated

The supplied schema carries one `wholesale_cost` per product, which forces
an averaging assumption at sale time.

Medix does better without extra work: cost lives on the **batch**
(`unit_cost_base`), FEFO records exactly which batch left, and `SaleLine`
holds the batch. So the cost of a specific sale is the actual cost of the
actual goods, not a moving average. Landed cost from imports — freight,
customs, duty — apportions into `unit_cost_base` at receipt, so the
depot's "total invested" is already carried by the batch rather than
sitting in a separate bucket.

### 12.3 "Net profit" is a word this product will not print

`CLAUDE.md` is explicit: **never label an unverified figure "net profit"
— use "estimated operating result".** The supplied `net_profit` column
and the dashboard's `🌟 NET PROFIT` tile both fall foul of it.

This is not pedantry. Net profit is an accounting result that depends on
salaries, rent, depreciation, tax position and accruals — none of which
this system sees. A pharmacist who reads "net profit: 65,000" and files a
tax return on it has been misled by us. Gross profit and gross margin are
ours to state, because we hold every input. Anything past operating
expenses is an estimate and gets labelled as one.

### 12.4 What is computed

| Figure | Source | Both sides? |
|---|---|---|
| Capital invested | `GoodsReceipt` lines × `unit_cost_base`, plus landed cost | yes |
| Revenue | `SaleLine.line_total` (retail) · dispatched order value (depot) | yes |
| COGS | `SaleLine` → `Batch.unit_cost_base` | yes |
| Gross profit | revenue − COGS | yes |
| Gross margin % | gross profit ÷ revenue | yes |
| Estimated operating result | gross profit − recorded expenses | yes, labelled as estimated |
| Expiry write-offs | `StockMovement` kind `EXPIRY_WRITE_OFF` × batch cost | yes |
| Receivables ageing | unpaid dispatched orders by age bucket | depot |
| Insurance receivable | unpaid claim lines | retail |
| Stock at risk | batches expiring within 90 days × cost | yes |

The three leakages named — expired stock, insurance rejections, holding
costs — are the reason the write-off and ageing rows are not optional
extras. Expiry write-off is already a first-class movement kind, so that
one is free.

### 12.5 Charts

`docs/21-data-visualization.md` governs, and it constrains this:

- **Four categorical slots in light, three in dark.** Green, amber and red
  are reserved for status. A revenue donut by therapeutic class has 13
  categories — it must fold to the top 3 plus "Other", or become a bar
  chart where length carries the comparison and colour carries nothing.
- **Investment against revenue is one axis, not two.** Both are RWF. A
  second axis would be inventing a scale difference that isn't there, and
  dual axes can be made to show any relationship the author wants.
- **Expiry risk as a stacked bar** is right, and it is the one place the
  status ramp is correct rather than reserved — safe, slow-moving and
  expiring *are* statuses.
- Every chart carries a table view. That is the accessibility fallback
  and the answer to any contrast complaint.

Run `node scripts/validate-palette.mjs` before changing a series colour.

### 12.6 Open

Nothing in this section is built. In order: expense recording, the
period-report service computing §12.4 for an arbitrary range, receivables
ageing, then the dashboard.

---

## 13 — Queue

In order, largest domain risk first.

1. ~~`Manufacturer` model; dosage form, strength, route on `Product`~~ **built**
2. ~~Storage and handling — temperature range, light, moisture~~ **built**
3. Product range: sexual health, baby care, first aid, oral care
4. Payment terms on the order; credit limit enforced at approval
5. Period financial report — §12.4, for an arbitrary date range
6. Transfer payload emitted on dispatch, consumed on receipt
7. Picking ticket and commercial tax invoice
8. Expense recording, then receivables ageing
9. Financial dashboard — §12.5
10. Volume discount tiers and SRP
11. Reorder point alerting (the field exists; nothing reads it)
12. Controlled substance transfer form
13. Import documents (Phase 4)

Items 1 and 2 landed with this document. The rest is the queue, in the
order the domain risk falls.
