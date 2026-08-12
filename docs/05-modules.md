# 05 — Modules

## Organization types — read this first

Medix serves **two licensed pharmacy types**, not a pharmacy and a non-pharmacy. Both are Rwanda FDA–licensed premises, both require a responsible pharmacist, both hold stock in batches against the same ledger. They differ in who they sell to and what they are permitted to do.

```python
class OrganizationType(TextChoices):
    RETAIL_PHARMACY    = "RETAIL"      # dispenses to patients
    WHOLESALE_PHARMACY = "WHOLESALE"   # supplies retail pharmacies
    IMPORTER           = "IMPORTER"    # sources internationally
    PLATFORM           = "PLATFORM"    # Medix / regulatory oversight
```

An organization may hold **more than one licence** — a wholesale pharmacy that also runs a retail counter is common, and an importer is very often also a wholesale pharmacy. So licences are a set, not a single value:

```python
class Organization(AuditedModel):
    name, tin, primary_type
    licences        # reverse FK to PremisesLicence, one per branch per kind
    def has(self, kind) -> bool
```

Capability is derived from held licences, never hardcoded from a single type field.

### What differs between the two

| | Retail pharmacy | Wholesale pharmacy |
|---|---|---|
| Sells to | Patients, walk-in customers | Retail pharmacies, institutions |
| Sales channel | POS, per-unit, immediate | Purchase orders, bulk, on terms |
| Dispensing | Yes — prescriptions, POM gating, narcotics register | No patient dispensing |
| Insurance | Central — claims, co-pay, eligibility | Not applicable |
| Fiscal invoice | Every sale, at the counter | Per order, on invoice |
| Typical UoM sold | Unit, blister, pack | Pack, carton |
| Partial pack | Yes — six tablets is normal | No |
| Facility standard | Sales/admin area minimum | Larger storage minimum, separate storage and admin areas |
| Cold chain | Small fridge | Cold room, mapped, logged |
| Imports | Requests them | Fulfils and consolidates them |

### What is identical

Catalog, batches, the stock ledger, FEFO, UoM, locations, transfers, expiry, cold chain, documents, audit, compliance, and analytics. **The core is shared.** This is why the two types are one product and not two.

---

## Module map

| Module | Retail | Wholesale | Importer | Platform |
|---|:--:|:--:|:--:|:--:|
| Catalog | read | write own listings | write | curate |
| Marketplace | buy | sell | sell | monitor |
| Procurement | ✓ | ✓ | ✓ | — |
| Imports | request | fulfil | fulfil + consolidate | monitor |
| Receiving | ✓ | ✓ | ✓ | — |
| Inventory | ✓ | ✓ | ✓ | — |
| Distribution | — | ✓ | ✓ | — |
| Point of sale | ✓ | — | — | — |
| Prescriptions | ✓ | — | — | audit |
| Insurance | ✓ | — | — | audit |
| Fiscal | ✓ | ✓ | ✓ | audit |
| Compliance | ✓ | ✓ | ✓ | ✓ |
| Finance | ✓ | ✓ | ✓ | — |
| Analytics | ✓ | ✓ | ✓ | ✓ |

---

## 1. Catalog

Product master, dynamic attributes per product type, UoM chains, regulatory registration, therapeutic categories, packaging images.

**Screens.** Product list (DataTable) · product detail (split layout, tabs: Overview · Uses · Suppliers · Batches · Documents) · product create (grouped form: Basic → Pharmaceutical → Regulatory → Commercial) · category management · attribute definitions.

**Product presentation — three modes.** Compact list is the **default** for procurement. Card grid where visual identification matters (cosmetics, OTC, devices, consumables). Split detail for one product in full context. Cards stay small: image strip, name, form and pack, price, stock, one compact Add.

**Product intelligence panel.** What it is, therapeutic class, legal status, available suppliers, price range, total available stock, earliest expiry, link to official leaflet. Reference information only — never symptom-to-drug.

---

## 2. Marketplace

Where a retail pharmacy buys from wholesale pharmacies and importers.

**Three availability states**, each a different transaction:

```
AVAILABLE_NOW      order → receive in days
INCOMING           reserve against a shipment already in motion → receive on arrival
IMPORT_ON_DEMAND   request → source internationally → quote → approve → import
NOT_IN_COUNTRY     the product exists but nobody stocks it
```

**Screens.** Browse (list default, grid toggle, filters: category, supplier, availability, price) · vendor comparison · listing management for the selling side · request product.

**Vendor comparison** shows price, stock, expiry, MOQ, delivery, verification side by side. The system makes the tradeoff visible; it never chooses.

---

## 3. Procurement

```
Requisition → Approval → Purchase Order → Supplier Confirmation
  → Delivery → GRN → Stock → Invoice → Payment
```

RFQ broadcast to multiple suppliers, quotation comparison, quote-to-PO conversion, supplier verification and KYC, payment terms, credit limits.

**Price intelligence.** "You paid 28,000 last month and 31,000 this month — ↑10.7%" with a compare-vendors action. One of the highest-value features for an owner.

---

## 4. Imports

The differentiator. Used by retail pharmacies to request, and by importers and wholesale pharmacies to fulfil.

```
Search → not available → Request import
  → broadcast to approved importers
  → international sourcing → quotations (itemized)
  → pharmacy approves → import begins
  → shipment + temperature tracking → regulatory checks
  → arrival → per-pharmacy goods receipt
```

**Demand consolidation.** The importer combines requests from many pharmacies into one order that clears MOQ. No other actor in this market can see aggregate demand, because today it is scattered across dozens of separate phone calls.

**Four problems the module must solve** — see [03-data-model.md](03-data-model.md#import-consolidation):

1. **Partial arrival allocation** — policy per order; one consignment produces separate GRNs per pharmacy with correct batch traceability.
2. **Landed cost apportionment** — freight, duty, clearance apportioned across participants to produce batch cost. Every margin number depends on this.
3. **FX** — quote currency, rate, rate date, fixed or indicative.
4. **Commitment** — binding point and deposit, because one withdrawal can break MOQ for everyone.

**Screens.** Import request console (request · quotations · consolidation · status timeline · documents · activity) · importer demand board · consolidation builder · shipment tracking.

---

## 4b. Dispatch

The supplier's half of a trade, and the reason stock stays honest across two organizations.

Receiving on its own credits the buyer with goods that never left the seller — the same cartons then exist twice on the platform and every marketplace stock figure overstates. `dispatch_order()` closes that loop: it issues the goods out of the supplier's ledger as `WHOLESALE_DISPATCH` and raises a **delivery note** (`Shipment`, `DN-YYYY-NNNNN`).

**Picking is FEFO**, so one order line routinely spans several batches — the buyer ordered a round number, the shelf does not hold one. Each batch becomes its own delivery-note line carrying batch number and expiry. Those are what the receiving pharmacy checks the physical cartons against, and what pre-fills their GRN.

**A short pick is not an error.** The supplier ships what they hold; the order goes `PARTIALLY_DISPATCHED` and the shortfall is still owed. Only the supplier may dispatch, and only against a confirmed order.

`WHOLESALE_DISPATCH` is deliberately not `SALE`. An organization holding both licences sells over a counter *and* ships to other pharmacies; collapsing the two would make counter revenue and trade revenue inseparable in analytics.

> Still to come here: transport temperature log and carrier tracking.

---

## 5. Receiving

GRN against PO. Ordered 500, received 480 produces a **discrepancy report**, never a silently edited number.

This is where batches enter the system — batch number, manufacture date, expiry, quantity, bound permanently to supplier, PO and receiving user.

**GS1 scanning.** A DataMatrix encodes GTIN `(01)`, batch `(10)`, expiry `(17)`, serial `(21)`. Parsing it **auto-fills batch and expiry**, which removes most manual data entry in the entire product. Treat this as a core feature, not a convenience.

**Cold chain check on receipt.** For a cold-chain product the receiver confirms transport temperature and records any excursion, which quarantines rather than accepts.

---

## 6. Inventory

Shared identically by both pharmacy types.

Ledger, batches, FEFO, locations, transfers, adjustments, stock take with variance approval, expiry management, cold chain, quarantine, recall execution, supplier returns, witnessed disposal.

**Expiry dashboard.**
```
🔴 Critical            12
🟠 Within 90 days      28
🟡 Within 180 days     47
🟢 Healthy            820
```

**Batch view per product** — available stock, batch count, expiring under 30 and 90 days, expired, then the batch table with quantity, manufacture, expiry, status.

**Screens.** Stock list · batch detail · movement history (the ledger, readable) · transfers · adjustments · stock take · expiry · disposal · temperature log.

---

## 7. Distribution — wholesale only

Outbound fulfilment: order picking, packing, dispatch, delivery notes, vehicle and route, proof of delivery, returns from customers.

Cold-chain shipments carry a temperature log that becomes part of the delivery record.

**Screens.** Order fulfilment queue · pick list · dispatch · delivery tracking · customer (pharmacy) accounts and verification.

---

## 8. Point of sale — retail only

```
Search / scan → Cart → Prescription check → Totals → Payment → Fiscal invoice → Ledger
```

Faster and more tactile than the rest of the system, larger touch targets, same visual language. Must work offline.

**Three things it must get right:**

- **Prescription gating blocks, it does not warn.** A POM line stops the sale until a prescription is attached and verified by a registered pharmacist. OCR may extract; OCR never authorizes.
- **Payment is asynchronous.** Mobile money is request-to-pay plus callback. `PENDING` is a real state that may resolve later or time out.
- **Tax is per line.** Medicines exempt, cosmetics and devices generally standard-rated. Every invoice is mixed-treatment.

**Also:** partial pack dispensing, barcode scan, held sales, returns, discounts with permission, till and shift management, X and Z reports, day-end reconciliation of expected versus counted cash per method.

---

## 9. Prescriptions — retail only

Capture (upload, scan, or manual), OCR extraction as advisory, pharmacist verification, dispensing record, partial dispensing, repeat handling, patient history.

**Narcotics register.** Every delivery of a narcotic or psychotropic writes a `ControlledDeliveryEntry` with the patient's name and address, the substance denomination, quantity, dispensing pharmacist and a running register balance. Append-only and reportable. This is a statutory artifact, not a flag on a sale.

---

## 10. Insurance — retail only

Its own workflow, not a payment method.

```
patient → scheme → eligibility → prescription → covered items
  → coverage calculation → co-pay → dispense → claim → tracking
```

Coverage rules are per scheme and per contract, held as versioned configuration.

> **Open risk.** CBHI is reportedly moving to capitation rather than fee-for-service. `SchemeContract.model` supports both, but the claim workflow assumes fee-for-service. Confirm with RSSB before building. See [11-risks.md](11-risks.md).

**Screens.** Claim list · claim detail · eligibility check at POS · rejections queue · receivables aging by scheme.

---

## 11. Fiscal

Invoice engine, per-line tax resolution, EBM submission through the local agent to VSDC, fiscal response storage, exception queue, reconciliation.

Behind `FiscalIntegrationService` so no screen ever knows the shape of the RRA interface.

**Per-tenant provisioning** is a workflow, not a settings field: apply to RRA → approval → deploy → initialize → activate, with test before production.

---

## 12. Compliance

Product registration status, premises licences per branch and kind, **pharmacist registration** (council number, expiry — dispensing must be attributable to a registered professional), inspections, recalls with execution tracking, adverse event reports, document expiry alerts.

**Screens.** Compliance dashboard (what expires when) · licence register · pharmacist register · recall console · inspection history.

---

## 13. Finance

Revenue, cost of goods at batch cost, gross margin, operating expenses, receivables and payables with aging, supplier statements, customer accounts, credit limits, payment recording, bank and mobile money reconciliation, accounting export.

---

## 14. Analytics and executive intelligence

Attention first, performance second. The owner is never asked to interpret twenty charts.

```
NEEDS ATTENTION
● 18 products expiring within 30 days
● 6 supplier invoices overdue
● 3 insurance claims rejected
● Stock shortage on 7 fast-moving products
```

Then: revenue trend, gross margin by category, best sellers and slow movers, vendor profitability and price changes, branch comparison, stock value and expiry exposure, insurance turnaround.

**Never overstate profit.** Revenue − COGS = gross profit. If there is insufficient data for a reliable net figure, the label reads *estimated operating result*.

Charts must answer a question. No decorative charts because a dashboard looks empty.

---

## 15. Documents

~35 types across seven categories, generated from templates with gap-free numbering, rendered to PDF, previewed on the web so the preview matches the PDF.

Every document carries: header, Medix identity, organization details, number, date, status, parties, line items, totals, terms, approvals, QR reference where appropriate, footer and page numbers.

---

## 16. Assistant

Search and action layer across products, inventory, orders, suppliers, invoices, GRNs and reports. Answers "which products expire in 60 days", "which branch has lowest stock", "show unpaid supplier invoices".

**It assists; it never silently performs.** Anything that moves stock, money, or a regulated record requires explicit human confirmation.

---

## Role-specific home screens

Same portal, different landing.

| Role | Home shows |
|---|---|
| Pharmacist (retail) | Attention items, quick actions: Sell · Receive stock · Order · Stock |
| Cashier | POS only |
| Warehouse manager | Stock, receiving, picking, expiry, transfers |
| Procurement officer | Suppliers, purchase orders, approvals, deliveries |
| Finance officer | Receivables, payables, reconciliation, claims |
| Owner | Revenue, gross profit, stock value, attention, branch comparison |
| Wholesale operations | Order queue, fulfilment, dispatch, customer accounts |
| Importer | Demand board, sourcing, consolidation, shipments |
| Regulator | Registrations, licences, recalls, inspections, audit |
