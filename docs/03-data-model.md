# 03 — Data model

This is the most consequential document in the repository. Three decisions here — the ledger, unit of measure, and batch costing — cannot be retrofitted without rewriting the product.

---

## 1. The stock ledger

### The rule

**There is no mutable quantity column.** Stock is an append-only sequence of movements. Balances are derived.

```python
stock.quantity -= n          # ❌ never
post_movement(...)           # ✅ always
```

### Why

| Requirement | A counter | A ledger |
|---|---|---|
| "Who received units from batch AMX-2601?" | Cannot answer | Query the movements |
| Gross margin at real cost | Guesses | Knows the batch and its landed cost |
| Cash short at day end | Argument | Replay |
| Audit trail | A separate feature to build | Already there |
| Offline merge from two sources | Conflicting writes | Appends merge cleanly |

The last row is why the ledger and the offline agent are the same decision.

### Model

```python
class StockMovement(TenantModel):
    """Append-only. Never updated, never deleted."""
    id
    organization        FK Organization
    location            FK Location          # branch + store
    batch               FK Batch
    product             FK Product           # denormalized for query speed

    kind                MovementKind         # see below
    quantity_base       BigIntegerField      # signed, in the product's base unit
    balance_after_base  BigIntegerField      # running balance, materialized

    # provenance — at least one must be set
    goods_receipt       FK GoodsReceipt      null
    sale                FK Sale              null
    transfer            FK StockTransfer     null
    adjustment          FK StockAdjustment   null
    disposal            FK DisposalRecord    null

    reason              TextField            # required for adjustments and overrides
    performed_by        FK User
    occurred_at         DateTimeField        # business time
    recorded_at         DateTimeField        # system time (differs when offline)
    idempotency_key     CharField unique     # for offline sync deduplication

    class Meta:
        indexes = [(organization, location, batch, occurred_at),
                   (organization, product, occurred_at)]
```

`MovementKind`: `OPENING` `PURCHASE_RECEIPT` `SALE` `SALE_RETURN` `TRANSFER_OUT` `TRANSFER_IN` `ADJUSTMENT` `DISPOSAL` `QUARANTINE` `RELEASE` `RECALL` `EXPIRY_WRITE_OFF` `SUPPLIER_RETURN`

### Balances

`StockBalance` is a **materialized projection**, rebuildable from movements at any time:

```python
class StockBalance(TenantModel):
    location, batch, product
    status              StockStatus
    quantity_base       BigIntegerField
    updated_at
    unique_together = (location, batch, status)
```

If the projection and the ledger ever disagree, **the ledger is right**. A management command rebuilds projections.

### Status is not quantity

```
AVAILABLE · RESERVED · QUARANTINED · DAMAGED · EXPIRED · RECALLED · IN_TRANSIT · RETURNED
```

A batch may hold quantity in several statuses at once. Only `AVAILABLE` is sellable.

---

## 2. Unit of measure

### The problem

This market sells six tablets, not a box of a hundred. Purchasing happens in packs; dispensing happens in units; both must reconcile against one batch.

### The model

Every product declares a **UoM chain** down to a base unit. All ledger quantities are stored in base units.

```python
class UnitOfMeasure(TenantModel):
    product         FK Product
    code            CharField        # CARTON | PACK | BLISTER | UNIT
    name            CharField        # "Pack of 100"
    factor_to_base  BigIntegerField  # how many base units in one of these
    is_base         BooleanField
    is_purchase_default   BooleanField
    is_dispense_default   BooleanField
    is_sellable     BooleanField     # can a customer buy this level?
```

Example — Amoxicillin 500mg:

| Code | Name | factor_to_base | base | purchase | dispense |
|---|---|---|---|---|---|
| `CARTON` | Carton of 12 packs | 12000 | | | |
| `PACK` | Pack of 100 capsules | 1000 | | ✓ | |
| `BLISTER` | Blister of 10 | 100 | | | |
| `UNIT` | Capsule | 1 | ✓ | | ✓ |

Note the base unit here is a **capsule with factor 1**, and a blister is 10 capsules — the table shows factors scaled so integer arithmetic never needs division. Choose the base as the smallest dispensable unit and set factors accordingly.

### The rules

- **Never store a bare integer quantity.** Use `Quantity(value, uom)`.
- Convert with `catalog.uom.to_base(quantity)` and `from_base(value, uom)`.
- Conversion is integer-only. Fractional base units are a modelling error, not a rounding problem.
- Prices exist **per UoM** — cost per pack, selling price per unit — and are not derived by dividing unless a rule says so.
- A product may forbid partial-pack dispensing (`is_sellable=False` on sub-pack levels) — common for antibiotics dispensed as full courses.

---

## 3. Batches and cost

### Batch

```python
class Batch(TenantModel):
    product             FK Product
    supplier            FK Supplier
    batch_number        CharField
    manufacture_date    DateField    null
    expiry_date         DateField
    goods_receipt       FK GoodsReceipt

    unit_cost_base      BigIntegerField   # landed cost per base unit, minor units
    cost_currency       CharField
    landed_cost_note    TextField         # how apportionment was derived

    gtin                CharField null    # GS1 (01)
    serial              CharField null    # GS1 (21)
    cold_chain          BooleanField
    unique_together = (organization, product, supplier, batch_number)
```

### Batch cost is the foundation of every margin number

`unit_cost_base` is not the invoice price. It is the **landed** cost: product price plus apportioned shipping, clearance, duties and other charges.

For a consolidated import this apportionment is non-trivial and is the highest-leverage calculation in the system — get it wrong and every figure in the executive portal is wrong. See [05-modules.md](05-modules.md#imports).

### FEFO allocation

```python
def allocate_fefo(product, location, quantity, *, as_of) -> list[Allocation]:
    """
    Nearest expiry first, AVAILABLE only, expiry > as_of.
    Returns allocations across one or more batches.
    Raises InsufficientStock rather than partially allocating.
    """
```

- Called by POS, transfers, and any outbound movement.
- Manual batch override is permitted but requires a reason and is recorded on the movement.
- Never select a batch by insertion order or primary key.

---

## 4. Catalog

### Product and dynamic attributes

A medicine, a cosmetic, a device and a consumable cannot share one flat table.

```python
class ProductType(TenantModel):
    code        # MEDICINE | COSMETIC | DEVICE | CONSUMABLE | SUPPLEMENT
    name

class AttributeDefinition(TenantModel):
    product_type    FK ProductType
    code, label
    data_type       # TEXT | NUMBER | DATE | BOOLEAN | ENUM | REFERENCE
    enum_values     JSONField null
    required        BooleanField
    group           CharField        # form section: Basic | Pharmaceutical | Regulatory | Commercial
    display_order   IntegerField

class Product(TenantModel):
    product_type    FK ProductType
    name, generic_name, brand
    therapeutic_category  FK Category null
    attributes      JSONField          # validated against AttributeDefinition
    base_uom        FK UnitOfMeasure
    cold_chain      BooleanField
    tax_treatment   TaxTreatment       # EXEMPT | STANDARD | ZERO_RATED
    legal_status    LegalStatus        # OTC | POM | CONTROLLED
    controlled_schedule  CharField null
```

`attributes` is JSON but **validated on write** against the definitions for the product's type. This gives per-type fields without a table per type, while keeping the data honest.

### Regulatory registration

Mirrors the Rwanda FDA registered-products model:

```python
class ProductRegistration(TenantModel):
    product, registration_number, holder, local_agent
    strength, dosage_form, route, pack_size, shelf_life_months
    manufacturer, manufacturer_country
    registered_on, registration_expiry
    status              # REGISTERED | SUSPENDED | WITHDRAWN | NOT_REGISTERED
    leaflet_document    FK Document null
```

### Vendor listing

A product is global-ish; what a wholesaler offers is a listing.

```python
class VendorListing(TenantModel):
    vendor          FK Organization
    product         FK Product
    availability    # AVAILABLE_NOW | INCOMING | PRE_ORDER | IMPORT_ON_DEMAND | NOT_IN_COUNTRY
    price_per_uom   FK UnitOfMeasure
    price           Money
    moq, lead_time_days
    incoming_shipment  FK Shipment null
```

---

## 5. Locations

```
Organization
  └── Branch          (a licensed premises)
        └── Store     (main store, dispensary, cold room, quarantine)
```

```python
class Location(TenantModel):
    parent          FK self null
    kind            # BRANCH | STORE
    name, code
    temperature_class   # AMBIENT | COOL_15_25 | COLD_2_8 | FROZEN
    licence         FK PremisesLicence null
```

`temperature_class` matters: placing a cold-chain batch in an `AMBIENT` location is a validation error, and an excursion recorded against a location quarantines the batches in it.

---

## 6. Sales

```python
class Sale(TenantModel):
    number              CharField        # SAL-2026-00982
    branch, till, shift
    cashier             FK User
    pharmacist          FK User null     # required when any line is POM/CONTROLLED
    customer            FK Customer null
    prescription        FK Prescription null
    status              # DRAFT | PENDING_PAYMENT | COMPLETED | VOIDED
    subtotal, tax_total, discount_total, total   Money
    occurred_at, idempotency_key

class SaleLine(TenantModel):
    sale, product, batch          # batch chosen by FEFO
    quantity, uom
    unit_price, line_total        Money
    tax_treatment, tax_amount     # per line — invoices are mixed-treatment
```

### Tax is per line

Medicines are VAT-exempt in Rwanda; cosmetics, devices and consumables generally are not. **Every invoice is mixed-treatment.** Exempt is not zero-rated: input VAT on exempt supplies is not reclaimable, which changes true cost and therefore margin. Tax treatment is a product attribute driving line-level calculation, resolved against the tax rules effective on the sale date.

### Payment

```python
class Payment(TenantModel):
    sale, method        # CASH | MOBILE_MONEY | INSURANCE | CARD | ACCOUNT
    provider            # MTN_MOMO | AIRTEL_MONEY | ...
    amount              Money
    status              # PENDING | CONFIRMED | FAILED | TIMED_OUT | REVERSED
    provider_reference, requested_at, resolved_at
```

Mobile money is **asynchronous** — request-to-pay, then a callback. `PENDING` is a real state that may resolve in seconds, time out, or need reconciliation. The POS must handle it as such.

### Till and shift

```python
class Till(TenantModel):        branch, name, agent_id
class Shift(TenantModel):
    till, opened_by, opened_at, closed_by, closed_at
    opening_float, expected_cash, counted_cash, variance   Money
    status              # OPEN | CLOSED | RECONCILED
```

Day end produces expected versus counted per method, with variance. X report reads without closing; Z report closes.

---

## 7. Prescriptions and controlled substances

```python
class Prescription(TenantModel):
    number, patient FK Patient, prescriber FK Prescriber
    issued_on, image FK Document null
    ocr_extract     JSONField null     # extracted, never authoritative
    verified_by     FK User            # a registered pharmacist
    verified_at
    status          # PENDING | VERIFIED | REJECTED | PARTIALLY_DISPENSED | DISPENSED
```

**OCR extracts, a pharmacist authorizes.** `ocr_extract` is advisory data; dispensing requires `verified_by` to hold a current pharmacist registration.

### Narcotics register

Law n° 03/2012 requires that every delivery of a narcotic or psychotropic be registered immediately on the prescription with the patient's name and address. This is a distinct statutory artifact, not a flag:

```python
class ControlledDeliveryEntry(TenantModel):
    sale_line, prescription
    patient_name, patient_address       # captured explicitly, required
    substance_denomination, schedule
    quantity_base, uom
    dispensed_by FK User                # pharmacist council number resolved at write
    balance_after_base                  # running register balance
    entered_at
```

Append-only, reconcilable, and reportable as the register the law expects.

---

## 8. Insurance

```python
class Scheme(TenantModel):          name, code, kind  # CBHI | RSSB | PRIVATE
class SchemeContract(TenantModel):
    scheme, organization
    model           # FEE_FOR_SERVICE | CAPITATION
    effective_from, effective_to
    status          # ACTIVE | SUSPENDED | TERMINATED

class CoverageRule(TenantModel):
    contract, product OR category
    covered_percent, cap Money null, requires_prescription
    effective_from, effective_to

class Claim(TenantModel):
    number, sale, contract, member FK Member
    claimed Money, approved Money null, rejected_reason
    status  # DRAFT | SUBMITTED | APPROVED | PARTIALLY_APPROVED | REJECTED | PAID
    submitted_at, resolved_at, paid_at
```

> **Open risk.** CBHI is reportedly moving from fee-for-service reimbursement to capitation. `SchemeContract.model` exists so both shapes are representable, but the claim workflow above assumes fee-for-service. Confirm with RSSB whether capitation applies to contracted private pharmacies before building this module. See [11-risks.md](11-risks.md).

---

## 9. Procurement, imports, receiving

```python
class Requisition          → internal request, approval
class RFQ                  → broadcast to suppliers
class Quotation            → supplier response; line costs itemized
class PurchaseOrder        → converted from quotation or created directly
class Shipment             → transport, temperature log, tracking
class GoodsReceipt         → GRN; creates Batches and PURCHASE_RECEIPT movements
class DiscrepancyReport    → ordered vs received vs accepted vs rejected
```

### Import consolidation

```python
class ImportRequest(TenantModel):
    requester FK Organization, product, quantity_base, uom
    needed_by, status
    committed BooleanField, deposit Money null

class ConsolidatedImport(TenantModel):
    importer FK Organization, product
    total_quantity_base, moq_base
    allocation_policy   # PRO_RATA | FIRST_COMMITTED | PRIORITY
    quote_currency, quote_rate, rate_date, rate_is_fixed
    status
    participants  M2M ImportRequest

class LandedCostComponent(TenantModel):
    consolidated_import
    kind        # PRODUCT | FREIGHT | INSURANCE | DUTY | CLEARANCE | HANDLING | OTHER
    amount Money
    apportionment   # BY_QUANTITY | BY_VALUE | BY_WEIGHT | EQUAL
```

Four problems this model exists to answer:

1. **Partial arrival** — 425 ordered, 400 arrive. `allocation_policy` decides who is short, and one consignment produces **separate GRNs per participating pharmacy**, each with correct batch traceability.
2. **Landed cost** — components are apportioned across participants to produce `Batch.unit_cost_base`. This drives every margin number downstream.
3. **FX** — quote currency, rate, rate date and whether the rate is fixed are recorded, because quote and arrival are 21–45 days apart.
4. **Commitment** — `committed` and `deposit` exist because one withdrawal can drop the group below MOQ and break it for everyone.

---

## 10. Compliance entities

```python
class PremisesLicence      organization, branch, number, kind, issued, expiry, status
class PharmacistRegistration  user, council_number, issued, expiry, status
class Inspection           branch, date, inspector, findings, outcome
class Recall               product OR batch, reason, severity, issued_at, status
class RecallAction         recall, location, batch, quantity_base, action, completed_at
class DisposalRecord       batch, quantity_base, method, witness, certificate FK Document
class AdverseEventReport   product, batch null, description, reported_by, submitted_at
class TemperatureExcursion location, from_at, to_at, min_c, max_c, batches_quarantined
```

Disposal of expired stock is a regulated act requiring a witnessed certificate — it is never a status change alone.

---

## 11. Documents

```python
class DocumentType         code, name, template, numbering_sequence
class Document(TenantModel)
    type, number, status
    subject_type, subject_id     # generic relation to the transaction
    rendered_pdf, payload JSONField
    issued_at, issued_by
```

Numbers come from `core.sequences`, which is gap-free per organization per type per year and safe under concurrency.

---

## 12. Cross-cutting

### Every audited model

```python
class AuditedModel(models.Model):
    created_by, created_at, modified_by, modified_at
    approved_by, approved_at, rejected_by, rejected_at, reason
    class Meta: abstract = True
```

### Append-only event stream

```python
class AuditEvent:
    organization, actor, action, subject_type, subject_id
    before JSONField, after JSONField, ip, user_agent, occurred_at
```

Reads of patient data are recorded here too, because Law 058/2021 requires demonstrable control over access to sensitive personal data.

### Identifiers

- Primary keys are UUIDv7 — sortable, non-guessable, safe to generate offline.
- Human-facing numbers (`SAL-2026-00982`, `GRN-00412`, `IR-00082`) come from the sequence service and are never the primary key.

### Time

Stored UTC, rendered `Africa/Kigali`. Offline movements carry both `occurred_at` (business time on the agent) and `recorded_at` (server receipt).

---

## Invariants worth writing tests for

1. Sum of `StockMovement.quantity_base` for a batch and location equals `StockBalance.quantity_base`.
2. `balance_after_base` on every movement equals the running sum.
3. FEFO never selects a later-expiring batch when an earlier one has available stock.
4. UoM conversion round-trips exactly; no fractional base units are ever produced.
5. A sale containing a POM line cannot complete without a verified prescription and a registered pharmacist.
6. A controlled sale line always produces exactly one `ControlledDeliveryEntry`.
7. Landed cost components apportioned across participants sum to the total, to the minor unit.
8. Tax is computed per line against rules effective on the sale date.
9. A cold-chain batch cannot be placed in an `AMBIENT` location.
10. Replaying the ledger from zero reproduces current balances exactly.
