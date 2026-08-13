# 31 — Operations

The blueprint: every operation from purchase request to day end, and for
each one what a person does, what the system writes, what document comes
out, and what happens to stock and money.

This is the document to read before building a screen. `docs/19-screens.md`
says what a screen looks like; this says what it is *for*.

---

## The five layers

Every field in Medix belongs to exactly one of these, and confusing two of
them is how ERPs rot.

| Layer | What it is | Changes when |
|---|---|---|
| **Master** | Product, manufacturer, supplier, pharmacy, branch, location, user, unit, tax rule | Someone decides it should |
| **Transaction** | Order, receipt, sale, transfer, return, payment, claim | An event happens |
| **Inventory** | Batch, movement, balance, status | A movement is posted — never directly |
| **Financial** | Payable, receivable, revenue, COGS, expense | Derived from transactions, never entered |
| **Audit** | Who, when, from where, before, after, why | Every one of the above |

Two rules follow, and they are not style:

**No period totals are stored.** There is no `monthly_revenue` column. A
figure is computed from the transactions that produced it, so it can
always be taken apart and explained. See `finance/reports.py`.

**No balance is written.** `StockBalance` is a projection of
`StockMovement`, maintained by `inventory.services.post_movement()`.
Nothing else may touch it.

---

## The trip, end to end

```
Purchase request → Purchase order → Supplier invoice → Shipment
      → Landed cost → Receiving → Inspection → Depot stock
      → Retail order → Credit check → Reservation → Picking → Packing
      → Dispatch → Transfer payload → Retail receipt → Retail stock
      → POS sale → Payment / claim → Day end → Reports
```

Each step below states: **who**, **screen**, **what they press**, **what
is written**, **document**, **stock**, **money**, **next action**.

---

## 1. Purchase request

*Not built. `docs/09-roadmap.md` Phase 7.*

| | |
|---|---|
| Who | Procurement, or a pharmacist noticing a gap |
| Screen | Inventory → below reorder point |
| Action | **Suggest reorder** |
| Writes | `PurchaseRequest` + lines |
| Document | Purchase request |
| Stock | Nothing |
| Money | Nothing |
| Next | Approve, then convert to an order |

Today the Assistant does the useful half of this: ask *"what is running
low"* and it offers a draft order for everything below its reorder point,
as a proposal that a person confirms. See `assistant/services.py`.

---

## 2. Purchase order

**Two approvals, and they are different people.** The buyer's own release
comes first — a pharmacist raises the order, someone who can commit money
releases it. Only then does the depot see it, and the depot approves
again before anything is picked.

| | |
|---|---|
| Who | Pharmacist raises; owner or manager releases |
| Screen | Marketplace → **Add to order**; Orders |
| Actions | **Send for approval** → **Approve and send** / **Send back** |
| Writes | `PurchaseOrder`, `PurchaseOrderLine`, `OrderEvent` per transition |
| Document | Purchase order, on release |
| Stock | Nothing. An order is a promise |
| Money | Nothing yet — the credit check runs at release |
| Next | The depot confirms |

Statuses, in the buyer's words: `Draft` → `With the owner` → `Awaiting
confirmation` → `Confirmed` → `Being prepared` → `Shipped` → `Received`.
`Sent back` returns it to the raiser with a reason they can answer.

**A one-person pharmacy may release its own order.** Refusing outright
does not produce a second approver; it produces a second login sharing
one keyboard. The order records `Self-approved: sole user of this
pharmacy`, and the control returns the moment a colleague exists.

---

## 3. Credit check

Runs at release, not at checkout, because the figure that matters is
exposure at the moment the depot commits stock.

```
credit limit − (invoiced outstanding + orders in flight) ≥ this order
```

`commerce/invoicing.credit_position()` folds pending into outstanding
already — adding it again would refuse at half the real limit, which is a
bug this codebase has actually had.

Over the limit is a **blocked action**, not a warning: it states what is
missing and offers the override, which is somebody else's decision.

---

## 4. Shipment and landed cost

| | |
|---|---|
| Who | Depot logistics |
| Screen | Imports |
| Actions | **Record shipment**, **Attach document**, **Add cost** |
| Writes | `Shipment`, `ImportDocument`, `LandedCost` |
| Documents | Packing list, invoice, certificate of analysis, permit, AWB or B/L |
| Stock | Nothing until received |
| Money | Cost allocated across the receipt, by value or by quantity |
| Next | Receive |

Freight, insurance, duty and clearing are allocated into the unit cost of
what arrives. A batch costed at the invoice price alone reports a margin
the pharmacy never made.

---

## 5. Receiving

| | |
|---|---|
| Who | Warehouse |
| Screen | Deliveries → **Receive** |
| Actions | **Scan**, **Add batch**, **Post receipt** |
| Writes | `GoodsReceipt` + lines, `Batch` per batch, `StockMovement` |
| Document | Goods receipt note |
| Stock | **Up**, in the batch's own row |
| Money | Inventory asset up; supplier payable up |
| Next | Inspect, or sell |

Every line carries batch number, manufacturing date and expiry. A receipt
without them is refused — not warned about — because the traceability the
whole system rests on cannot be reconstructed later.

Where the depot sent an advance notice, the receipt is already drafted
with what was shipped, and the pharmacy checks rather than types.

---

## 6. Quarantine and release

Received stock is not automatically sellable. A batch can be held on
arrival, on a cold-chain excursion, on a recall, or on a pharmacist's
judgement.

Held stock is **still in the ledger** — it moved status, not location.
`inventory/movements.quarantine()` writes two rows, out of available and
into quarantined, so both balances stay derivable.

Release requires a reason. `commerce.services.release_batch()` refuses an
empty one.

---

## 7. Marketplace and the retail order

| | |
|---|---|
| Who | Retail pharmacist |
| Screen | Marketplace |
| Actions | filter, **Compare**, **Add to order** |
| Writes | draft `PurchaseOrder` |
| Stock | Nothing |
| Money | Nothing |
| Next | Send for approval |

What a buyer must be able to see before committing, because they cannot
pick the box up:

- the **pack picture**, and the pack size in words — "Box of 30"
- generic name, brand, strength, dosage form
- manufacturer, barcode, Rwanda FDA registration number
- prescription status, cold chain, earliest expiry
- price **in the unit they chose**, down to the smallest the depot will break to
- what is actually left

The picture is verification, never identification. A listing showing
"Available" with nothing allocated behind it reads **None left**, and the
order button is refused with the reason — not discovered at the server
after a quantity has been typed.

---

## 8. Picking, packing, dispatch

*Picking and packing as separate records: not built. Dispatch is.*

| | |
|---|---|
| Who | Depot warehouse |
| Screen | Orders to fill |
| Actions | **Confirm**, **Start preparing**, **Dispatch** |
| Writes | `Shipment` + lines, `StockMovement` per line, `OrderEvent` |
| Documents | Picking ticket, delivery note |
| Stock | **Down** at the depot, FEFO, at dispatch — not at confirmation |
| Money | Revenue and receivable on the invoice |
| Next | The buyer receives |

Allocation is FEFO, always. A request spanning two batches becomes two
lines, each with its own cost and traceability.

---

## 9. The transfer

Dispatch builds a payload — `medix.transfer/1`, see
`commerce/payloads.py` — carrying product identity, batch, expiry,
quantity in base units, pack size and cost. The receiving pharmacy's
draft receipt is seeded from it, so nobody retypes a batch number.

**Products are matched on registration number and GTIN, never on id.**
The two organizations hold different product rows for the same medicine;
matching on id silently links nothing, which is a bug this codebase has
also actually had.

---

## 10. Point of sale

| | |
|---|---|
| Who | Cashier or pharmacist |
| Screen | Point of sale |
| Actions | scan, **Add**, **Pay**, **Void** |
| Writes | `Sale`, `SaleLine`, `Payment`, `StockMovement`, controlled register entry |
| Document | Receipt, fiscal signature where the VSDC is reachable |
| Stock | **Down**, FEFO, by batch |
| Money | Revenue, COGS at the batch's own cost, cash or claim |
| Next | Day end |

Gates, in order: prescription for a POM, a registered pharmacist for the
dispensing, the controlled register, clinical checks where a patient is
known. **A sale is not `COMPLETED` until it is paid** — goods left the
counter, but a sale with nothing tendered is not revenue and day end must
not count it as such.

Offline, the sale is journalled locally and replayed through the same
service path — never inserted — so the offline route is not the way
around any of the above.

---

## 11. Returns

Nothing returns to sellable stock automatically. The disposition is a
pharmacist's judgement and the system asks for it: **Restock**,
**Quarantine**, **Destroy**. `inventory/movements.sale_return()` takes it
as an argument rather than assuming.

---

## 12. Money

Nothing here is entered. Every figure is computed from the transactions
above:

| Figure | From |
|---|---|
| Revenue | Completed sales, and depot invoices |
| COGS | Each line's own batch cost |
| Gross profit | The two above |
| Expenses | Recorded expenses in the period |
| **Estimated operating result** | Gross profit less recorded expenses |
| Receivables | Invoices outstanding, aged |
| Payables | Supplier invoices outstanding |
| Stock at risk | Value expiring inside the alert horizon |

**Never "net profit".** The system sees trade — not tax, not rent, not
salaries, not financing. Calling the figure profit is a claim it cannot
support, and somebody would make a decision on it. Every screen and every
document says *estimated operating result*, and states what is excluded.

---

## Documents

**There is no document centre.** A document is an output of the operation
that produced it and lives on that operation's row: chips in the table,
opening the document itself.

| Document | Raised by | Attached to | Readable by |
|---|---|---|---|
| Purchase order | Release | Order | Both parties |
| Proforma | Order confirmation | Invoice | Both |
| Picking ticket | Dispatch | Shipment | **Depot only** |
| Delivery note | Dispatch | Shipment | Both |
| Tax invoice | Invoicing | Invoice | Both |
| Credit note | Return or correction | Invoice | Both |
| Goods receipt note | Posting a receipt | Receipt | Receiver |
| Controlled transfer | Dispatching a controlled line | Transfer | Both, and the regulator |
| Write-off | Disposal | Write-off | Issuer, and the regulator |
| Insurance claim | A covered sale | Claim | Issuer and scheme |

The picking ticket is the one document that does not cross: it names the
depot's own shelves, and the buyer has no business seeing them.

Documents are **immutable**. A correction issues a new version that
supersedes the old, and the old stays readable. The render context is
frozen at issue, so a product renamed next year does not rewrite last
year's invoice.

---

## Usability

Medix is bought by pharmacies, not by software teams. The person using it
did not choose it and was not trained on it.

**Name the work, not the record.** "Deliveries", not "Receiving".
"Orders to fill", not "Distribution". "Send for approval", not "Submit".
"Correct stock", not "Inventory adjustment". The record type is what the
database calls it; the user never has to learn that word.

**Say what happens next.** A status is not an instruction. `Approved`
tells a pharmacist where the order is, not what they should now do. Every
transaction screen carries one next action and one button that does it.

**Show the whole line, not the current state.** A chip showing
`Confirmed` hides that four steps happened before it and three have not
happened yet.

**Explain the term, not the system.** One sentence, on demand, beside the
word that needs it — *"Available: what you can order now. Excludes stock
already reserved for other pharmacies."* Never a paragraph, never a tour.

**State the consequence, never ask "are you sure".** *"Returns the order
to the pharmacist who raised it. The depot never sees it."* Then a button
that repeats the verb.

Copy limits and the banned list are in `docs/23-ui-copy.md` and are
enforced by `scripts/validate-copy.mjs`. Accessibility is enforced by
`scripts/validate-a11y.mjs` and `scripts/validate-palette.mjs`. All three
run under `npm run check`.

---

## What is not built

Named here so the gaps are the plan rather than a surprise.

| Missing | Consequence today |
|---|---|
| Purchase requests | Ordering starts at the draft order; the Assistant covers the reorder case |
| Picking and packing records | Dispatch posts the movement directly; no pick list state |
| Explicit reservation | `VendorListing.committed_base` holds the depot's side; retail has no reserved status |
| Accounts payable | Receivables are modelled; payables are not |
| Role model | Any second colleague may release an order; there is no `APPROVE_PURCHASE` permission |
| Stock count sessions | The agent can replay a count; there is no count screen |

See `docs/30-delivery-plan.md` for sequencing.
