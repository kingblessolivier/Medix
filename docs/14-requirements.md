# 14 — Requirements

Product requirements for Medix v1. Written to be testable: every requirement carries acceptance criteria, and anything that cannot be verified is not a requirement.

---

## Problem statement

Rwandan pharmacies operate on disconnected paper records. The same transaction is written into five separate notebooks — purchases, sales, expiry, insurance, prescriptions — and reconciled by hand at the end of each day. The consequence is that owners cannot state their real margin (batch purchase cost is never linked to the sale), stock-outs and expiry losses are discovered late, insurance receivables go unchased, and compliance evidence for Rwanda FDA and RRA has to be reconstructed rather than produced.

Separately, retail pharmacies routinely cannot obtain products that no local wholesale pharmacy stocks, because a single pharmacy's demand never reaches the minimum order quantity that makes an import economic.

## Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Eliminate the nightly reconciliation notebook | Day-end close completed from system data in under 5 minutes, with zero manual re-entry |
| G2 | Make gross margin real, not estimated | 100% of completed sales resolve to a batch with a landed cost |
| G3 | Make every sale fiscally compliant | ≥99% of sales produce an accepted fiscal invoice; the remainder appear in a visible exception queue |
| G4 | Reduce expiry loss | Value of stock written off at expiry falls measurably against the pharmacy's own baseline in the first two quarters |
| G5 | Make previously impossible imports possible | At least one consolidated import fulfilled that no single participant could have ordered alone |
| G6 | Keep selling when the internet does not | POS completes a sale and issues a receipt with the cloud unreachable |

## Non-goals for v1

| Non-goal | Why |
|---|---|
| Clinical decision support, interaction or allergy checking | Regulated clinical territory; out of scope permanently, not just for v1 |
| Full double-entry accounting | Export to the pharmacy's accountant instead; building a ledger-of-record is a separate product |
| Patient-facing application | No demand established; the counter is the interaction point |
| E-prescription network integration | No confirmed national interface to integrate with |
| Manufacturing or production planning | Different domain; Medix ends at the wholesale pharmacy |
| Serialization to unit level | Mandate timing unconfirmed; the model must not preclude it |

---

## Users

| Persona | Context | Primary need |
|---|---|---|
| **Retail pharmacist** | Behind a counter, 10-hour shifts, often the responsible pharmacist | Sell fast, dispense lawfully, know what needs attention |
| **Cashier** | Retail, may not be a pharmacist | Complete a sale; escalate anything requiring a pharmacist |
| **Warehouse manager** | Wholesale pharmacy or multi-branch retail | Receive, put away, pick, transfer, control expiry |
| **Procurement officer** | Either type | Compare suppliers, raise and track orders |
| **Owner / director** | Often multi-branch, rarely on the counter | Is the business healthy, and what needs a decision |
| **Wholesale operations** | Wholesale pharmacy | Fulfil orders, dispatch, manage pharmacy customers |
| **Importer** | Often also a wholesale pharmacy | See aggregate demand, source, consolidate, clear |
| **Regulatory user** | Rwanda FDA / platform oversight | Registration, licences, recalls, audit |

---

## User stories

### Retail pharmacist

- As a retail pharmacist, I want the system to pick the batch nearest to expiry automatically so that I stop losing stock I could have sold.
- As a retail pharmacist, I want a prescription-only product to be blocked until a prescription is attached so that I cannot dispense unlawfully under time pressure.
- As a retail pharmacist, I want to sell six tablets from a pack of a hundred so that the system matches how customers actually buy.
- As a retail pharmacist, I want to scan a pack and have batch and expiry filled in so that I am not typing them from a box at the counter.
- As a retail pharmacist, I want to keep selling when the internet drops so that a connectivity failure is not a trading failure.
- As a retail pharmacist, I want the day to close from what the system already recorded so that I stop writing a notebook at ten at night.

### Warehouse manager

- As a warehouse manager, I want a receipt discrepancy recorded rather than silently corrected so that supplier shortfalls are visible.
- As a warehouse manager, I want an out-of-range temperature event to quarantine affected batches automatically so that a fridge failure cannot reach a patient.
- As a warehouse manager, I want to see every movement of a batch so that I can execute a recall in minutes.

### Owner

- As an owner, I want gross margin computed from what the batch actually cost so that I am not making decisions on a guess.
- As an owner, I want to be told a supplier raised a price so that I can act rather than discover it at year end.
- As an owner, I want branches compared on the same measures so that I can see which one is underperforming.
- As an owner, I want a figure labelled honestly as an estimate when the data does not support precision.

### Procurement and imports

- As a procurement officer, I want suppliers compared on price, stock, expiry, MOQ and delivery together so that I can make a real tradeoff.
- As a retail pharmacist, I want to request a product nobody stocks so that a dead end becomes an order.
- As an importer, I want to see demand for the same product across many pharmacies so that I can consolidate it into a viable import.
- As a participating pharmacy, I want to know how a short shipment will be allocated **before** I commit so that I am not surprised.

### Wholesale

- As wholesale operations, I want incoming orders in a fulfilment queue with pick lists so that dispatch is not run from a WhatsApp thread.
- As wholesale operations, I want to verify that a pharmacy customer holds a current licence so that I am not supplying an unlicensed premises.

### Compliance

- As a compliance officer, I want licence and registration expiries surfaced in advance so that we never trade on an expired licence.
- As a compliance officer, I want the controlled substances register produced from dispensing records so that it is complete by construction.

---

## Functional requirements

### P0 — cannot ship without

| ID | Requirement | Acceptance criteria |
|---|---|---|
| **F1** | Append-only stock ledger | Given any stock change, when it is applied, then a `StockMovement` row exists referencing its cause; no code path mutates a quantity column; replaying the ledger from zero reproduces current balances exactly |
| **F2** | Batch tracking | Given goods are received, when the GRN is posted, then batches are created with number, expiry and supplier, and every subsequent movement of those units references the batch |
| **F3** | FEFO allocation | Given multiple available batches, when stock is issued, then the nearest-expiry batch is selected; an override is permitted only with a recorded reason |
| **F4** | UoM hierarchy | Given a product with pack and unit levels, when purchasing in packs and dispensing in units, then both reconcile against one batch with no fractional base units |
| **F5** | Multi-tenant isolation | Given a user of organization A, when any resource is requested, then no data belonging to organization B is returned; verified by an automated cross-tenant test suite |
| **F6** | Product catalog with typed attributes | Given a product type, when a product is created, then only that type's attributes are offered and required ones are enforced |
| **F7** | Purchase order to receipt | A PO can be raised, sent, confirmed, received against, and short-received with a discrepancy report |
| **F8** | POS sale | A sale can be created, items added by search or scan, priced, taxed per line, paid, and completed, producing ledger movements and a receipt |
| **F9** | POM gating | Given a POM line, when completion is attempted without a verified prescription and a registered pharmacist, then the sale is blocked with an explanatory message |
| **F10** | Controlled substances register | Given a controlled line is dispensed, when the sale completes, then exactly one register entry exists with patient name and address and a running balance |
| **F11** | Fiscal invoicing | Given a completed sale, when fiscal submission is attempted, then either an accepted fiscal invoice is stored or the sale appears in the exception queue; it is never silently unfiscalized |
| **F12** | Offline POS | Given no cloud connectivity, when a sale is completed, then it is journalled locally, a receipt is issued, and it syncs exactly once on reconnection (idempotent) |
| **F13** | Per-line tax | Given a basket of exempt and standard-rated items, when totals are computed, then tax is calculated per line against rules effective on the sale date |
| **F14** | Asynchronous payment | Given mobile money is selected, when payment is requested, then the sale enters a pending state and resolves on callback, timeout, or manual reconciliation |
| **F15** | Day-end close | Given a shift, when it is closed, then expected versus counted is presented per payment method with variance, and a Z report is produced |
| **F16** | Expiry management | Products are banded by expiry risk, alerts are raised at configurable thresholds, and expired stock cannot be sold |
| **F17** | Licence-derived capability | Given a branch without a valid licence of the required kind, when a restricted action is attempted, then it is refused |
| **F18** | Audit trail | Every mutation records actor, time, before and after; reads of patient data are also recorded |
| **F19** | Documents | Every transaction type renders to a numbered PDF whose web preview matches it |
| **F20** | Opening balances | Existing stock can be imported at go-live with batch and expiry, producing `OPENING` movements |

### P1 — high-priority follow

| ID | Requirement |
|---|---|
| F21 | GS1 DataMatrix parsing — scan auto-fills GTIN, batch, expiry |
| F22 | Import request, RFQ broadcast, quotation comparison |
| F23 | Demand consolidation with allocation policy and landed-cost apportionment |
| F24 | Insurance eligibility, coverage calculation, co-pay, claim submission and tracking |
| F25 | Cold chain — location classes, excursion capture, automatic quarantine |
| F26 | Executive analytics — margin by category, vendor performance, branch comparison |
| F27 | Stock take with variance approval |
| F28 | Recall execution tracking |
| F29 | Transfers between branches |
| F30 | Command palette with actions, global search |
| F31 | Account customers, receivables aging |
| F32 | Witnessed disposal with certificate |

### P2 — designed for, not built

Serialization to unit level · Medix Assistant · demand forecasting · anomaly detection · route and delivery optimization · accounting system integration · adverse event submission workflow · public API for third parties.

These influence the data model now — for example `Batch.serial` exists though nothing writes it yet — so that adding them later is additive rather than structural.

---

## Non-functional requirements

| Area | Requirement |
|---|---|
| **Performance** | Product search returns in <300 ms p95 at 50k products; a POS line is added in <100 ms locally; a table page renders in <500 ms p95 |
| **Offline** | POS operates fully offline for at least 72 hours of trading and reconciles without duplication |
| **Availability** | 99.5% cloud availability; POS availability is independent of it |
| **Scale** | 500 organizations, 2,000 branches, 100k products, 50M ledger rows in year one without redesign |
| **Security** | JWT with rotation; capability-based permissions enforced in services; rate limiting on auth; secrets from environment |
| **Privacy** | Health data handled per Law 058/2021 — consent, purpose limitation, retention, erasure, read auditing, residency |
| **Auditability** | Any transaction older than a year remains explainable under the rules effective at the time |
| **Language** | English only. No localization layer in v1 |
| **Currency** | RWF in integer minor units; foreign-currency quotes record rate, date and whether fixed |
| **Time** | Store UTC, render `Africa/Kigali`; offline records carry business and system time separately |
| **Accessibility** | WCAG AA contrast in both themes; full keyboard operation of the POS flow; visible focus states |
| **Browser** | Current Chrome, Edge, Firefox, Safari. No IE. Tablet supported for POS |
| **Data portability** | An organization can export its complete data on request or on exit |

---

## Success metrics

**Leading — first 30 days per site**

- Day-end close completed from system data on ≥90% of trading days
- ≥99% of sales fiscally accepted
- ≥95% of sales resolved to a batch (the remainder indicate migration gaps)
- Zero cross-tenant data incidents
- POS median transaction time at or below the paper baseline by day 14

**Lagging — first two quarters**

- Expiry write-off value down against the site's own baseline
- Stock-out days on fast movers down
- Insurance receivable ageing improved
- At least one consolidated import fulfilled that no participant could have ordered alone
- Site retention after the first full quarter

---

## Open questions

| Question | Owner | Blocking? |
|---|---|---|
| Cloud-hosted per-tenant VSDC permissible? | Engineering + RRA | **Yes — architecture** |
| Data residency under Law 058/2021 | Legal + NCSA | **Yes — infrastructure** |
| CBHI capitation scope for private pharmacies | Product + RSSB | **Yes — insurance module** |
| Monetization: subscription, transaction fee, or both | Business | Yes — shapes onboarding and the wholesale portal's role |
| Current VAT classification for the full product mix | Finance + tax adviser | No — configurable |
| GS1 mandate dates for Rwanda | Compliance | No — building the capability regardless |
| Which mobile money provider first | Engineering | No |
| Hardware baseline per site | Operations | No |

---

## Phasing

Full plan in [09-roadmap.md](09-roadmap.md). Sequence is **foundation → retail single site → wholesale → imports → insurance → intelligence**, with verification tasks completed in Phase 0 because two of them can invalidate architecture.
