# 12 — Glossary

Domain, regulatory and system terms as Medix uses them. Where a term has a looser everyday meaning, the Medix meaning is the one that governs.

---

## Organizations and people

**Retail pharmacy** — A Rwanda FDA–licensed premises that dispenses to patients. Runs a POS, handles prescriptions and insurance, sells in units as well as packs.

**Wholesale pharmacy** — A Rwanda FDA–licensed premises that supplies retail pharmacies and institutions. No patient dispensing. Sells in packs and cartons against purchase orders. **Also a pharmacy** — same core system, different permissions and channel.

**Importer** — An organization licensed to source internationally. Very often also a wholesale pharmacy.

**Licence set** — An organization's held premises licences. Capability derives from these, never from a single type label. An organization may hold retail, wholesale and importer licences simultaneously. See [ADR-006](10-decisions.md).

**Responsible pharmacist** — The registered pharmacist accountable for a premises. Distinct from any user with a login.

**Locum** — A temporary pharmacist. Access is time-boxed at the grant, not by convention.

**Organization / tenant** — The isolation boundary. All data is scoped to one, with cross-organization visibility only through explicit sharing relations.

**Branch** — A licensed premises belonging to an organization.

**Store** — A stock location within a branch: main store, dispensary, cold room, quarantine.

---

## Product

**Product** — A sellable item. Not necessarily a medicine — cosmetics, devices and consumables are products too, with different attributes.

**Product type** — `MEDICINE` · `COSMETIC` · `DEVICE` · `CONSUMABLE` · `SUPPLEMENT`. Determines which dynamic attributes apply.

**Generic name** — The active substance (amoxicillin). **Brand** is the trade name.

**Dosage form** — Capsule, tablet, syrup, injection, cream.

**Legal status** — `OTC` (over the counter) · `POM` (prescription-only medicine) · `CONTROLLED` (narcotic or psychotropic, additional statutory register).

**Registration** — Rwanda FDA product registration: number, holder, local agent, expiry. An unregistered or suspended product cannot be sold.

**MAH** — Marketing Authorization Holder. The entity holding the product registration.

**Therapeutic category** — Antibiotic, antihypertensive, analgesic, antihistamine. Used for browsing and margin analysis.

**Vendor listing** — What a specific wholesale pharmacy or importer offers: price, availability state, MOQ, lead time. A product is shared; a listing belongs to a seller.

---

## Quantity and measure

**Unit of measure (UoM)** — A level in a product's packaging hierarchy: carton, pack, blister, unit.

**Base unit** — The smallest dispensable level. All ledger quantities are stored in base units. Integer only.

**Conversion factor** — How many base units one UoM contains. `Pack = 1000` base units when the base is a capsule and a pack holds 100, scaled so integer arithmetic never needs division.

**Partial pack dispensing** — Selling fewer units than a full pack while decrementing the parent batch. Standard practice in this market, and the reason the UoM hierarchy exists.

**Quantity** — Never a bare integer. Always a value plus a UoM.

---

## Stock

**Stock movement** — One immutable row in the ledger. The only way stock changes.

**Stock ledger** — The append-only sequence of movements. Authoritative.

**Stock balance** — A materialized projection of the ledger. Disposable and rebuildable. If it disagrees with the ledger, **the ledger is right**.

**Batch (lot)** — A manufactured production run with its own number, expiry and landed cost. Traceability and costing both hang off it.

**FEFO** — First Expired, First Out. The allocation policy: always issue from the nearest-expiry available batch. Not FIFO.

**Stock status** — `AVAILABLE` · `RESERVED` · `QUARANTINED` · `DAMAGED` · `EXPIRED` · `RECALLED` · `IN_TRANSIT` · `RETURNED`. Distinct from quantity; only `AVAILABLE` is sellable.

**Quarantine** — Stock held pending assessment. Automatic on a temperature excursion or a recall.

**Stock take** — A physical count reconciled against system balance, producing an approved variance adjustment.

**Cold chain** — Products requiring controlled temperature, typically 2–8 °C. Insulin, vaccines, biologics.

**Temperature excursion** — A recorded period outside a location's temperature class. Automatically quarantines affected batches.

---

## Commerce

**Requisition** — An internal request to purchase, before it becomes an order.

**RFQ** — Request for Quotation. Broadcast to multiple suppliers.

**Quotation** — A supplier's itemized response: price, shipping, other costs, lead time, MOQ, terms.

**Purchase order (PO)** — A commitment to buy. Converted from a quotation or raised directly.

**MOQ** — Minimum Order Quantity. The threshold that makes a single pharmacy unable to import alone.

**GRN** — Goods Received Note. The receiving document. **Where batches enter the system.**

**Discrepancy report** — Ordered vs received vs accepted vs rejected. Produced whenever they differ; never a silently edited number.

**ASN** — Advance Shipping Notice. What is coming, before it arrives.

---

## Imports

**Import request** — A pharmacy asking for a product nobody stocks locally.

**Availability state** — `AVAILABLE_NOW` · `INCOMING` (reservable against a shipment in motion) · `PRE_ORDER` · `IMPORT_ON_DEMAND` · `NOT_IN_COUNTRY`.

**Demand consolidation** — Combining import requests from many pharmacies into one order that clears MOQ. **The reason Medix is a platform rather than pharmacy software** — no other actor can see aggregate demand.

**Consolidated import** — The resulting joint order, with participants, allocation policy and shared landed costs.

**Allocation policy** — How a short shipment is divided: `PRO_RATA` · `FIRST_COMMITTED` · `PRIORITY`. Stated before commitment, not after arrival.

**Landed cost** — The true per-unit cost: product price plus apportioned freight, insurance, duty, clearance and handling. Becomes `Batch.unit_cost_base` and therefore drives every margin figure in the system.

**Apportionment basis** — How a shipment-level cost is divided among participants: by quantity, value, weight, or equally.

---

## Sales

**POS** — Point of sale. The retail counter. Must work offline.

**Till** — A physical sales point. **Shift** — one till's trading session, opened and closed with cash reconciliation.

**X report** — Reads the shift without closing it. **Z report** — closes it.

**Day end** — Reconciliation of expected against counted per payment method, producing the daily report that replaces the notebook.

**Prescription** — A prescriber's authorization to dispense. Required for POM and CONTROLLED.

**Prescriber** — The clinician who wrote it. Distinct from the pharmacist who dispenses.

**Dispensing** — Supplying a medicine against a prescription. Attaches to a **registered** pharmacist.

**Controlled delivery entry** — The statutory register row required for every narcotic or psychotropic delivery, carrying the patient's name and address. See [06-compliance.md](06-compliance.md).

**Pending payment** — A real sale state. Mobile money resolves asynchronously via callback and may time out.

---

## Insurance

**Scheme** — An insurer. `CBHI` · `RSSB` · private.

**CBHI / Mutuelle de Santé** — Community-Based Health Insurance, operated by RSSB. The dominant payer.

**RSSB** — Rwanda Social Security Board.

**Selective contracting** — RSSB contracts with specific private pharmacies against explicit criteria. An uncontracted pharmacy cannot bill the scheme.

**Fee-for-service** — Reimbursement per item dispensed. **Capitation** — payment in advance for planned services. Which applies is [V3](11-risks.md), open.

**Coverage rule** — Versioned configuration: what a contract covers, at what percentage, with what cap.

**Co-pay** — The patient's share. **Claim** — the insurer's share, and a receivable until paid.

---

## Fiscal and tax

**RRA** — Rwanda Revenue Authority.

**EBM** — Electronic Billing Machine. The fiscal invoicing regime.

**EIS** — Electronic Invoicing System.

**VSDC** — Virtual Sales Data Controller. The RRA-provided module, deployed as a WAR on the taxpayer's own local webserver, that bridges a private system to RRA. **The reason Medix needs a local agent.**

**Fiscal invoice** — An invoice that has been accepted by the fiscal system. A sale is not compliant without one.

**Fiscal exception** — A completed sale whose submission failed. Must be visible and actionable, never silent.

**Exempt** — No output VAT charged and **input VAT not reclaimable**. Most medicines. **Zero-rated** — no output VAT but input VAT *is* reclaimable. The distinction changes true cost of goods and therefore margin.

**Mixed-treatment invoice** — One document carrying lines with different tax treatments. In a pharmacy this is the normal case, not the exception.

---

## Compliance

**Rwanda FDA** — The regulator for medicines, devices, cosmetics and related products.

**National Pharmacy Council** — Registers pharmacists. Separate from premises licensing.

**GSDP** — Good Storage and Distribution Practice. **GPP** — Good Pharmacy Practice.

**Premises licence** — Per branch, per kind. Expiry revokes capability.

**Pharmacist registration** — Council number and expiry. Required to verify a prescription or complete a POM sale.

**Recall** — Withdrawal of a product or batch, with per-location execution tracking. The clearest justification for the append-only ledger.

**Disposal record** — Witnessed destruction of expired or recalled stock, with a certificate. A regulated act, never a status change alone.

**ADR (adverse drug reaction)** — A safety report raised against a product and batch, for submission to Rwanda FDA. *Not to be confused with Architecture Decision Record — in this repository, ADR-NNN in [10-decisions.md](10-decisions.md) means the latter.*

**Law 058/2021** — Rwanda's data protection and privacy law. Health data is sensitive personal data.

**NCSA** — National Cyber Security Authority. Supervisory authority for data protection.

**DPO** — Data Protection Officer. Required designation.

---

## Traceability

**GS1** — The standards body behind GTIN and DataMatrix.

**GTIN** — Global Trade Item Number. The product identifier in a GS1 barcode.

**GS1 DataMatrix** — The 2D barcode on pharmaceutical packs.

**Application Identifier (AI)** — The prefix identifying each field: `(01)` GTIN · `(10)` batch · `(17)` expiry · `(21)` serial. Parsing these **auto-fills batch and expiry**, which removes most manual entry in the system.

**Serialization** — Unit-level unique identifiers. Phasing in regionally; the model supports it but nothing writes it yet.

---

## System

**Local agent** — The Python service at each pharmacy site: VSDC bridge, offline POS journal, hardware, sync.

**Idempotency key** — A client-supplied identifier making a repeated request safe. What prevents duplicate sales on offline sync.

**Projection** — A derived, rebuildable read model. `StockBalance` is one.

**Effective dating** — Every regulatory rule carries `effective_from` and `effective_to`, so a historical transaction remains explainable under the rules that applied then.

**Progressive disclosure** — Showing advanced functionality only when a user needs it. How a complex system stays usable by a single-branch pharmacy.

**Module template** — The reusable arrangement for a class of screen — list, transaction, console. Screens configure a template rather than laying out from scratch.

**Design token** — A named design value. The only permitted source of colour, size, spacing and radius in frontend code.

---

## Currency and money

**RWF** — Rwandan franc. The operating currency.

**Minor units** — How all money is stored: integers, never floats.

**Indicative vs fixed quote** — Whether a foreign-currency import quote holds. Quote currency, rate, rate date and this flag are all recorded, because quote and arrival are 21–45 days apart.
