# 01 — Overview

## The problem

A Rwandan pharmacy runs on disconnected paper.

Stock is bought from a wholesaler by phone, WhatsApp, or a visit. It arrives and is written in a notebook. It is sold and written in a different notebook. Expiry dates live in a third. Insurance claims in a fourth. Prescriptions in a fifth. At ten at night the pharmacist sits down and reconciles by hand, and gets it roughly right.

The owner asks whether the business made money this month, and the honest answer is that nobody knows. They know revenue. They are guessing at margin, because nobody tracks which batch at which purchase price was actually sold. Stock value comes from a count taken three weeks ago.

Meanwhile two authorities want data from that same counter:

- **Rwanda FDA** cares that the product is registered, stored under the right conditions, and dispensed under the right prescription rules.
- **RRA** cares that every sale produced a valid fiscal invoice.

So the pharmacy does the same work five times in five places, and still cannot answer the two questions that matter: *am I profitable* and *am I compliant*.

## The thesis

Medix's job is not to digitize the notebooks. It is to make them unnecessary.

Digitizing the notebooks produces five disconnected screens, and the pharmacist still does double entry. One transaction spine produces all five views as a byproduct of work already recorded.

**Medix is a transaction ledger for the pharmaceutical supply chain, with different windows cut into it for different people.**

The spine never breaks:

```
Product → Vendor Listing → Batch → Inventory → Purchase → Order
  → Shipment → Goods Receipt → Stock → Prescription → POS Sale
  → Insurance Claim → EBM Invoice → Financial Record → Executive Report
```

Every screen in Medix is a lens on some segment of that chain. The pharmacist looks at the middle. The owner looks at the end. The wholesaler looks at the beginning. The regulator looks across all of it.

Same data, different windows. That is the architectural commitment, and it is what makes this worth building rather than buying four separate tools.

The corollary matters as much as the thesis: **if any link is optional or bypassable, the chain rots and you are back to notebooks.**

## Following one product through

The clearest way to understand the system is to follow a single box of Amoxicillin from a wholesaler's warehouse to a patient's hand and into the owner's profit figure.

**Discovery.** A pharmacist in Kigali needs Amoxicillin 500mg. Three wholesalers have it. The comparison view shows not just price but stock, expiry, MOQ, delivery time, and verification status. The cheapest option expires a year earlier and takes three days. That is a real procurement decision, and the system's job is to make the tradeoff visible, not to choose.

**When nobody has it.** Insulin XYZ is not in anyone's warehouse. Instead of a dead end, the pharmacist raises a product request. It broadcasts as an RFQ to approved importers. They respond with price, lead time, MOQ and terms. The pharmacy compares and converts a quotation into a purchase order. See *demand consolidation* below — this is the differentiator.

**Import.** For an on-demand import the order becomes an import request with its own console: requested → quoted → approved → sourcing → shipped → arrived → received. Documents accumulate against it.

**Receiving.** Goods arrive and are checked in against a Goods Received Note. Ordered 500, received 480 produces a discrepancy report — never a silently edited number. **This is where batches enter the system**: batch AMX-2601, expiry April 2028, quantity 480, bound permanently to the supplier, the purchase order, and the receiving pharmacist. The stock ledger gets an IN row.

**Stock.** Those units are `AVAILABLE` at Kigali branch, Main Store. Fifty are transferred to Remera, producing ledger rows on both sides.

**Sale.** Nine at night. A customer presents a prescription. Amoxicillin is prescription-only, so the POS blocks — not warns, blocks. A prescription must be attached: patient, prescriber, number, date, quantity, directions. If a photo is uploaded, OCR may extract fields, but OCR never authorizes. A registered pharmacist confirms and their identity attaches to the dispensing event.

**Payment.** If the patient is insured, this is not a payment method — it is a workflow. Eligibility, covered items, coverage calculation, co-pay, dispense, claim, tracking. The patient pays 7,000 and leaves; the pharmacy is now owed 28,000 by an insurer, and that receivable must be submitted, chased, and reconciled.

**Fiscal.** The sale produces an invoice, which must pass through the RRA EBM layer to become a legally valid fiscal document.

**Stock closes the loop.** The ledger gets an OUT row. If the balance crosses a reorder threshold it appears in tomorrow's attention feed.

**Day end.** No notebook. The system already knows sales, cash, mobile money, insurance, transaction count, items sold, returns, discounts, expected cash versus counted cash. The pharmacist reviews an exception instead of reconstructing a day.

**Upstairs.** That single sale moved the owner's numbers. Because the ledger knows the batch, it knows the actual purchase cost, so it knows real gross margin — not an estimate.

## Two pharmacy types, one core

Medix serves **two licensed pharmacy types**, not a pharmacy and some other kind of business. Both are Rwanda FDA–licensed premises, both require a responsible pharmacist, both hold stock in batches against the same ledger.

| | Retail pharmacy | Wholesale pharmacy |
|---|---|---|
| Sells to | Patients | Retail pharmacies, institutions |
| Channel | POS, immediate | Purchase orders, on terms |
| Dispensing | Prescriptions, POM gating, narcotics register | None |
| Insurance | Central | Not applicable |
| Typical UoM | Unit, blister, pack | Pack, carton |
| Partial pack | Yes — six tablets is normal | No |

**What is identical:** catalog, batches, the stock ledger, FEFO, unit of measure, locations, transfers, expiry, cold chain, documents, audit, compliance and analytics. This is why they are one product and not two.

An organization may hold **more than one licence** — a wholesale pharmacy with a retail counter is common, and an importer is very often also a wholesale pharmacy. So capability derives from **held licences**, never from a single type label. See [ADR-006](10-decisions.md).

## The four actors

Four genuinely different products over one ledger. Not role-based hiding of a single dashboard.

### Retail pharmacist
Operational, time-pressured, often working late. Asks *what do I need to do right now?* Needs to sell fast, check stock, verify a prescription, receive a delivery. Should never see gross margin by branch. Home screen is a to-do list, not KPIs.

### Owner / executive
May own several branches — of either type — and never touch the POS. Asks *is my business healthy?* Revenue, gross profit, stock value, receivables, which branch is winning, which supplier is quietly raising prices. Should never see a batch-picking screen.

### Wholesale pharmacy / importer
The supply side. Asks *what are pharmacies asking for and can I fulfil it?* Catalog, listings, pricing, incoming orders, RFQs to quote, consolidated imports, distribution, shipments, regulatory documentation, customer verification.

### Platform / regulatory administrator
Oversight. Product registration status, licences, inspections, recalls, cross-organization transaction visibility, audit trail.

### Operational roles inside an organization
Distinct from the four portals: warehouse manager, procurement officer, cashier, finance officer, wholesale operations, locum pharmacist. Each gets a different home screen within the same portal.

## Progressive disclosure

Medix is complex. Most of its users are not.

A single-branch pharmacy owner needs Sell, Order, Stock, Reports. They should never be forced to learn RFQ, GRN, ASN, batch reconciliation, insurance reconciliation, margin analysis, or import documentation. Those surface when the business grows into needing them.

**Complexity lives underneath the interface, not in front of the user.**

## Demand consolidation — why this is a platform

A single pharmacy needing 75 packs of a specialty product cannot import. It fails minimum order quantity, and per-unit shipping and clearance make it uneconomic.

Twenty pharmacies with the same need clear the threshold easily.

```
Pharmacy A    100
Pharmacy B     50
Pharmacy C    200
Pharmacy D     75
──────────────────
Total         425  →  Consolidated Import Order
```

No individual actor in the current market can see that aggregate demand exists, because it is scattered across twenty separate phone calls to twenty separate wholesalers. Medix is the only thing positioned to see it.

That means **Medix makes previously impossible imports possible**. That is the adoption argument — not "nicer software", but access to products a pharmacy currently cannot get at all.

## The wedge

**Pharmacy-first.**

Consolidation requires pharmacy density before it works. One importer with no pharmacies is useless. A hundred pharmacies with one importer already creates real value. Importers are pulled in by visible aggregate demand, which is a far easier sell than asking them to join an empty marketplace.

## Business model

**Open decision.** The candidates:

| Model | Implication |
|---|---|
| Subscription per pharmacy | Predictable revenue; wholesaler portal is a feature |
| Transaction fee on marketplace and import | Aligns with consolidation value; wholesaler portal is the product |
| Both | Subscription for operations, fee for brokered transactions |

This determines who is onboarded first and whether the wholesaler portal is a supporting feature or the core business. See [11-risks.md](11-risks.md).

## Guardrails

These are product requirements, not preferences.

**No clinical advice.** Medix can state that Amoxicillin is an antibiotic used for certain bacterial infections and link the official leaflet. It must never map symptom to drug. Diagnosis and prescribing are regulated professional acts and the system's job is to enforce that boundary.

**OCR never authorizes.** It may extract prescription fields. A registered pharmacist confirms.

**Never overstate profit.** If there is not enough accounting data to compute a reliable net figure, label it *estimated operating result*. A system that lies about profit is abandoned the first time an accountant checks it.

**The Assistant assists.** It answers questions and drafts actions. It never silently performs an action that moves stock, money, or a regulated record.
