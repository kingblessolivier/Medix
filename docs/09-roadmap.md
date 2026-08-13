# 09 — Roadmap

Sequencing matters more than any individual feature here. Medix is genuinely five products, and the order below is chosen so that each phase is independently useful and nothing later forces a rewrite of anything earlier.

**Governing rule: design system → module templates → screens.** Never screen by screen.

---

## Phase 0 — Verify and found

*Nothing user-facing ships. Two of these tasks can invalidate the architecture, so they come first.*

### Verification — blocking

| # | Question | Authority | Blocks |
|---|---|---|---|
| V1 | Is a cloud-hosted per-tenant VSDC permissible, or must it be on-premise? | RRA | Whole architecture |
| V2 | Data residency and cross-border transfer under Law 058/2021 | NCSA / legal | Infrastructure region |
| V3 | Does CBHI capitation apply to contracted private pharmacies? | RSSB | Insurance module shape |

### Non-blocking, resolve during the phase

VAT classification for the full product mix · GS1 mandate dates for Rwanda · current retail and wholesale facility standards · current controlled substance categories · monetization decision · hardware baseline per site.

### Foundation

- Repository, CI, environments, Docker Compose
- `core`: organizations with **licence sets**, users, roles, capability-derived permissions, tenancy middleware, audit models, `Money`, `Quantity`, gap-free sequences
- **Design system**: tokens (light and dark), `ui/` primitives, `AppShell`, `DataTable`, `Drawer`, form primitives, `EmptyState`, skeletons
- `base_document.html` and the print stylesheet
- Cross-tenant test harness that runs against every endpoint

**Exit criteria.** V1–V3 answered. A developer can scaffold a screen using only existing tokens and components. The cross-tenant suite is green and runs in CI.

---

## Phase 1 — The ledger

*The foundation everything else stands on. Still nothing a pharmacist would recognize as a product.*

- `catalog`: product types, dynamic attributes, **UoM hierarchy**, products, categories, registration data
- `inventory`: `StockMovement`, `StockBalance` projection, locations, statuses, **FEFO allocation**, adjustments, transfers, opening balances
- Rebuild-from-ledger management command
- Product list, product detail, stock list, movement history, batch view

**Exit criteria.** All ten data-model invariants hold under test. Replaying the ledger from zero reproduces balances exactly. Six tablets can be dispensed from a pack of a hundred and reconcile.

---

## Phase 2 — Retail, single site

*First shippable product. A single retail pharmacy can run its counter on Medix.*

- `commerce`: suppliers, purchase orders, goods receipts with discrepancy, batch creation on receipt
- `sales`: POS, cart, FEFO at point of sale, partial pack dispensing, tills, shifts, day-end with X and Z reports, returns
- `sales`: prescriptions, POM gating, pharmacist verification, **controlled substances register**
- `fiscal`: invoice engine, per-line tax, `FiscalIntegrationService`, exception queue
- **Local agent v1**: offline journal, sync with idempotency, VSDC bridge, receipt printing, barcode scanning
- Documents: receipt, fiscal invoice, purchase order, GRN
- Pharmacist home screen with attention items

**Exit criteria.** A pharmacy trades for a full day offline and reconciles with zero duplicates. Day-end close completes from system data in under five minutes. ≥99% of sales fiscally accepted in the RRA test environment.

> This is the pilot. Everything after it is expansion.

---

## Phase 3 — Wholesale and multi-branch

*The second pharmacy type, and the supply side of the marketplace.*

- Wholesale organization type, licence-derived capability
- `commerce`: marketplace listings, browse (list default, card grid), vendor comparison, RFQ, quotations
- `commerce`: order fulfilment queue, picking, packing, dispatch, delivery notes
- Multi-branch: transfers with in-transit state, branch comparison
- Customer accounts, credit limits, receivables aging
- Stock take with variance approval
- Documents: quotation, delivery note, invoice, credit note

**Exit criteria.** A retail pharmacy orders from a wholesale pharmacy inside Medix and receives against the order, with stock and money correct on both sides.

---

## Phase 4 — Imports and consolidation

*The differentiator. Requires Phase 3's supply side to exist.*

- Import requests, broadcast RFQ to approved importers
- Itemized quotations with cost breakdown
- **Demand consolidation** with MOQ tracking and commitment
- **Landed cost apportionment** into batch cost
- FX capture — currency, rate, rate date, fixed or indicative
- Shipment tracking, regulatory checkpoints, temperature log
- **Partial-arrival allocation** with per-participant goods receipts
- Import console with status timeline
- Documents: import request, consolidated order, manifest, packing list

**Exit criteria.** One consolidated import completes end to end, with landed cost apportioned correctly and each participant's batch carrying the right unit cost. A deliberate short shipment allocates per policy.

---

## Phase 5 — Insurance

**Built, with both shapes modelled.** V3 remains open, and the mitigation
`docs/11` R3 already named is what unblocked it: `SchemeContract.model`
carries the reimbursement shape, so answering V3 later selects a contract
row rather than forcing a rewrite.

They are genuinely different workflows, not one with a different rate.
Fee-for-service raises a claim per covered sale. **Capitation raises
none** — the scheme has already paid for the period, so claiming as well
would be asking twice, and the question becomes utilisation against the
money received.

- Schemes, contracts, selective contracting status
- Coverage rules as versioned configuration
- Eligibility check at POS, coverage calculation, co-pay
- Claim creation, submission, response handling, rejections queue
- Receivables by scheme with aging
- Documents: claim, submission bundle, rejection notice

**Exit criteria.** A covered sale splits into co-pay and claim correctly, and the claim reconciles on payment.

---

## Phase 6 — Compliance and cold chain

**Built**, except the sensor half. Licence and registration expiry
alerting, the compliance dashboard, automatic quarantine on a recorded
cold-chain breach, the recall console with per-location execution and
full trace, witnessed disposal with a certificate, and GS1 DataMatrix
parsing all exist. What remains is live temperature capture from a
device, which needs the local agent.

- Premises licences and pharmacist registrations with expiry alerting
- Compliance dashboard
- Cold chain: location temperature classes, excursion capture, **automatic quarantine**
- Recall console with per-location execution tracking
- Witnessed disposal with certificate
- Adverse event reports
- **GS1 DataMatrix parsing** at receiving and POS
- Documents: disposal certificate, recall notice, inspection record

**Exit criteria.** A simulated recall traces every unit of a batch to its current location or its sale, in under a minute.

---

## Phase 7 — Intelligence

**Built.** Margin by category and product from batch cost, best sellers
by units, slow movers sorted never-sold first, stock-outs inferred from
demand against holding, and the performance dashboard. Vendor price-change
alerting and the accounting export remain.

- Margin by category, product, branch — computed from batch cost
- Vendor profitability and price-change alerting
- Best sellers, slow movers, stock-out analysis, expiry exposure
- Branch comparison
- Executive attention feed
- Reporting exports, accounting export

**Exit criteria.** Gross margin reconciles to a hand calculation on a sample of transactions. No figure is labelled "net profit" without the data to support it.

---

## Phase 8 — Assistant and polish

**Partly built:** the command palette and global search across products,
batches, orders, invoices, documents, pharmacies and patients. The
Assistant, the copy pass and the accessibility audit remain.

- Command palette with actions
- Global search across products, orders, invoices, patients, prescriptions, suppliers, batches, documents
- Medix Assistant — search and action layer, always with human confirmation for anything that moves stock, money, or a regulated record
- Keyboard shortcuts
- Full UI copy pass against the length limits in docs/23-ui-copy.md
- Accessibility audit against WCAG AA in both themes

---

## Dependency graph

```
Phase 0  Verify + foundation
   │
Phase 1  Catalog + ledger + FEFO + UoM
   │
Phase 2  Retail POS + fiscal + agent ──────────────► PILOT
   │
   ├── Phase 3  Wholesale + marketplace
   │      │
   │   Phase 4  Imports + consolidation
   │
   ├── Phase 5  Insurance          (gated on V3)
   ├── Phase 6  Compliance + cold chain
   │
Phase 7  Intelligence   (needs data from 1–4)
   │
Phase 8  Assistant + polish
```

Phases 3, 5 and 6 can proceed in parallel after Phase 2 if staffing allows. Phase 4 cannot start before Phase 3. Phase 7 is not worth starting before Phase 4 has produced landed costs.

---

## What is deliberately not on this roadmap

| Not building | Why |
|---|---|
| Clinical decision support, interaction checking | Regulated clinical territory. Permanent exclusion, not a deferral |
| Full double-entry accounting | Export to the accountant instead |
| Patient-facing app | No established demand; the counter is the interaction point |
| E-prescription network | No confirmed national interface |
| Manufacturing | Different domain |
| Unit-level serialization | Mandate timing unconfirmed. The model does not preclude it (`Batch.serial` exists) |

---

## Release policy

- `main` is always deployable.
- Semantic versioning. Version bumps on release, not per merge.
- Every user-visible change lands in `CHANGELOG.md` under *Unreleased* in the same PR.
- Site agents version independently of the cloud and must tolerate a cloud one minor version ahead — sites are updated on their own schedule and a pharmacy cannot be forced to upgrade mid-trading-day.
