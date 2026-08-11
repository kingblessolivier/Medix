# 13 — Research and findings

Research conducted to validate the system design against the Rwandan regulatory, commercial and operational environment. This document records **what was found, what it changed, and what remains unverified**.

Sources are cited inline. Secondary sources are marked as such — where a finding materially affects architecture it carries a **VERIFY** task in [11-risks.md](11-risks.md).

---

## Method

Four lines of enquiry:

1. **Regulatory** — Rwanda FDA (product registration, premises licensing, good practice), RRA (fiscal invoicing), Law 058/2021 (data protection), Law 03/2012 (narcotics).
2. **Payer** — RSSB, CBHI/Mutuelle de Santé, contracting and reimbursement models.
3. **Domain standards** — pharmacy inventory practice, GS1 traceability, cold chain.
4. **Payments** — mobile money integration patterns in Rwanda.

Design work preceding the research had produced a UI direction and a functional outline. The research was run specifically to find **what the design had missed**. It found eleven gaps, three of which would have forced a rewrite.

---

## Finding 1 — EBM/VSDC is not a cloud API *(architectural)*

**What was assumed.** A `FiscalIntegrationService` in the cloud backend calling an RRA REST endpoint.

**What is actually the case.** VSDC is distributed as a **WAR file deployed on the taxpayer's own local webserver**, and the calling system invokes its web services locally. Each taxpayer applies separately and receives RRA approval before activation, with distinct test and production instances.

**Consequence.** A pure cloud SaaS cannot issue fiscal invoices on a pharmacy's behalf. Medix requires a **local agent at each site**.

**Second-order benefit.** The same agent solves offline POS, which was a separate hard requirement. Two problems, one component — but only if designed in from the start.

**Also introduced.** A per-tenant provisioning workflow: apply → approval → deploy → initialize → activate, test before production. That is a tracked state machine, not a settings field.

Sources: [RRA VSDC specification](https://www.rra.gov.rw/fileadmin/user_upload/vsdc_specification_document_v1.0.4__2022.pdf) · [RRA EBM](https://www.rra.gov.rw/en/ebm-electronic-billing-machine/request-and-install-ebm)

> **VERIFY** — whether a cloud-hosted per-tenant VSDC is permissible. Determines cloud-only vs cloud-plus-agent for the entire product.

---

## Finding 2 — CBHI is moving to capitation *(architectural)*

**What was assumed.** Insurance works as dispense → itemized claim → per-line reimbursement.

**What is reported.** CBHI (Mutuelle de Santé), the dominant payer, is moving from retrospective fee-for-service reimbursement toward a **capitation model** in which providers are paid in advance for planned services. RSSB additionally uses **selective contracting** with private pharmacies against explicit criteria — an uncontracted pharmacy cannot bill the scheme at all.

**Consequence.** If capitation applies to contracted private pharmacies, the claim-and-reimburse workflow is the wrong shape for the majority of covered transactions. The data model now carries `SchemeContract.model = FEE_FOR_SERVICE | CAPITATION` so both are representable, but only one workflow should be built first.

Sources: [The New Times — CBHI reforms](https://www.newtimes.co.rw/article/33617/news/health/minister-habimana-clarifies-what-mutuellede-sante-reforms-mean-for-contributors/amp) · [Health Systems & Reform — strategic purchasing in Rwanda](https://www.tandfonline.com/doi/full/10.1080/23288604.2022.2061891)

> **VERIFY — blocking for the insurance module.** Secondary sources; reforms may apply to public facilities only. Confirm with RSSB.

---

## Finding 3 — Partial pack dispensing breaks a pack-level schema *(architectural)*

**What was assumed.** Quantities are pack-level throughout.

**What is actually the case.** Retail pharmacy in this market sells **individual tablets and partial strips**, decrementing the parent batch. Systems are expected to support it natively.

**Consequence.** A **unit-of-measure hierarchy** is required from day one — carton → pack → blister → unit with conversion factors, purchasing in packs, dispensing in units, both reconciling against one batch. This also changes pricing (cost per pack vs price per unit), margin, and reorder logic.

Retrofitting means rewriting the ledger, POS, pricing and every stock query. This became load-bearing decision #3.

Source: [Rexolia — FEFO in pharmacy inventory](https://rexolia.com/blog/pharmacy-inventory-management-why-fefo-is-non-negotiable/)

---

## Finding 4 — Health data is sensitive personal data under Law 058/2021 *(legal)*

**What was assumed.** Nothing. Data protection appeared nowhere in the preceding design.

**What is actually required.** Law No. 058/2021 treats health data as **sensitive personal data**. Obligations include unambiguous consent, registration as a data controller with the National Cyber Security Authority, designation of a data protection officer, purpose limitation, retention, subject access and erasure, breach notification, and **cross-border transfer rules**.

**Consequence.** Consent capture, retention policy, erasure workflow, and **audit logging of reads as well as writes** of patient data. Most significantly, transfer rules **constrain the hosting region** — a decision that must be made before infrastructure, not after.

Sources: [RwandaLII — Law 058/2021](https://rwandalii.org/akn/rw/act/law/2021/58/eng@2021-10-15) · [RISA](https://www.risa.gov.rw/data-protection-and-privacy-law) · [DLA Piper](https://www.dlapiperdataprotection.com/guide.pdf?c=RW)

> **VERIFY — blocking before infrastructure.** Data residency requirements.

---

## Finding 5 — Controlled substances require a statutory register *(legal)*

**What was assumed.** A `CONTROLLED` classification flag on the product.

**What is actually required.** Under Law n° 03/2012, every delivery of a narcotic or psychotropic must be **registered immediately on the prescription**, recording the **patient's name and address** and the denomination of the substance delivered. A 2019 Ministerial Order defines the categories.

**Consequence.** A distinct append-only `ControlledDeliveryEntry` with mandatory patient name and address, substance denomination, dispensing pharmacist and a running register balance — reconcilable and reportable in the statutory form.

Sources: [Law 03/2012 (Rwanda FDA)](https://rwandafda.gov.rw/wp-content/uploads/2022/11/narcotic_law_2012__2_.pdf) · [Ministerial Order 2019 (RwandaLII)](https://rwandalii.org/akn/rw/act/mo/minister-of-health/2019/1/eng@2019-03-11)

---

## Finding 6 — Premises and professional licensing are separate *(legal)*

**What is the case.** Rwanda FDA licenses premises — and licenses **retail and wholesale pharmacies as distinct types** with different facility standards (wholesale carries a larger minimum storage area and separately defined sales and administrative space). Separately, the **National Pharmacy Council** registers pharmacists, and a Good Pharmacy Practice standard applies.

**Consequence.** Two things the design lacked:

1. **Organization type is a licence set, not a label.** Retail pharmacy and wholesale pharmacy are both pharmacies; an organization may hold both. Capability derives from held licences.
2. **Dispensing must attach to a registered professional** — council number and licence expiry — not merely "a user".

Sources: [Rwanda FDA licensing guidelines](https://rwandafda.gov.rw/wp-content/uploads/2023/04/Guidelines%20on%20licensing%20of%20public%20and%20private%20manufacturers,%20distributors,wholesalers,retailers%20of%20medical%20products_Rev%204.pdf) · [Good Pharmacy Practice in Rwanda](https://faolex.fao.org/docs/pdf/rwa211197.pdf)

---

## Finding 7 — Medicines are VAT-exempt; the rest of the shop is not *(legal / financial)*

**What was assumed.** A single tax line on the invoice.

**What is actually the case.** Goods and services for health purposes are **exempt** from VAT in Rwanda; pharmaceutical products are not subject to excise. But pharmacies also sell cosmetics, devices and consumables, which are generally standard-rated.

**Consequence.**

- Tax treatment is a **product attribute** driving **per-line** calculation. Every invoice is mixed-treatment.
- **Exempt is not zero-rated.** Input VAT on exempt supplies is not reclaimable, which changes true cost of goods and therefore margin. A system that ignores this overstates profit.
- The EBM payload must carry per-line classification correctly.

Sources: [RRA exempted goods and services](https://www.rra.gov.rw/fileadmin/user_upload/exempted_goods___services.pdf) · [PwC — Rwanda other taxes](https://taxsummaries.pwc.com/rwanda/corporate/other-taxes)

---

## Finding 8 — FEFO, not FIFO *(domain)*

**What was assumed.** Batch-aware stock, with allocation policy unstated.

**What is required.** **First Expired, First Out.** At sale time the system automatically allocates from the nearest-expiry batch with available quantity. Expiry alert thresholds typically 30–90 days depending on category and volume.

**Consequence.** An unstated policy would have defaulted to FIFO or insertion order and silently generated expiry write-offs. FEFO is now load-bearing decision #2, with logged manual override.

Sources: [Rexolia](https://rexolia.com/blog/pharmacy-inventory-management-why-fefo-is-non-negotiable/) · [ShelfLifePro](https://shelflifepro.net/pharmacy/)

---

## Finding 9 — Cold chain was entirely absent *(domain)*

**What was the case.** The design's own worked example was importing **insulin** over a 21–45 day lead time, with no temperature concept anywhere.

**What is required.** Insulin and vaccines require **2–8 °C**; excursions destroy potency. Monitoring uses calibrated data loggers with alarms; distribution uses refrigerated transport with temperature tracking.

**Consequence.** Cold-chain flag per product, temperature class per location, excursion events on the ledger, **automatic quarantine on excursion**, and refrigerated transport declared on shipments. Also a Good Storage and Distribution Practice concern for inspection.

Sources: [Dickson Data](https://dicksondata.com/pharmaceutical-cold-chain-logistics-and-products) · [PharmaSource](https://pharmasource.global/content/guides/category-guide/cold-chain-management-a-comprehensive-guide/)

---

## Finding 10 — GS1 DataMatrix is both compliance and the biggest UX win *(domain)*

**What is the case.** Pharmaceutical packs carry a GS1 DataMatrix encoding GTIN `(01)`, expiry `(17)`, batch `(10)` and, where serialized, serial `(21)`. Regional traceability mandates are phasing in, GTIN/expiry/batch ahead of full serialization.

**Consequence.** Parsing GS1 application identifiers means a scan at receiving **auto-fills batch and expiry**, and a scan at POS resolves the exact batch. That single capability removes most manual data entry in the system. The prior design said only "scan barcode", implying a dumb product lookup, which throws the benefit away.

Sources: [GHSC-PSM GS1 regulation tracker](https://www.ghsupplychain.org/sites/default/files/2025-01/20250109_GS1%20Regulation%20Tracker.pdf) · [GS1 DataMatrix](https://www.gs1ie.org/standards/data-carriers/barcodes/gs1-data-matrix/)

> **VERIFY** — mandate dates applying to Rwanda specifically. Build the capability regardless.

---

## Finding 11 — Mobile money is asynchronous *(domain)*

**What was assumed.** `[Cash] [Mobile Money] [Insurance]` as three equivalent instant actions.

**What is actually the case.** MTN MoMo Collections uses a **request-to-pay plus callback** pattern — initiate, the customer confirms on their handset, the provider calls back with the result. Airtel Money is a separate integration, and both are needed for coverage.

**Consequence.** A sale carries a **pending payment state** that may resolve in seconds, time out, or require reconciliation. Fiscal invoice timing depends on it. The POS must model this explicitly.

Sources: [GBOX — MTN MoMo API in Rwanda](https://gbox.rw/en/blog/mtn-momo-api-integration-rwanda/) · [GBOX — Airtel Money API](https://gbox.rw/en/blog/airtel-money-api-integration-rwanda/)

---

## Lower-severity gaps identified

Not researched externally; identified by domain reasoning against the design.

**Day-one blockers.** Opening stock balances and migration — every pharmacy already holds inventory, and there was no path for it to enter the system. Hardware: thermal receipt printer, barcode scanner, batch label printer, cash drawer.

**Regulated workflows named but never designed.** Expired-stock destruction requires a witnessed certificate, not a status change. Recall execution tracking. Adverse drug reaction reporting to Rwanda FDA.

**Operational.** Till and shift management with X and Z reports. Stock take with variance approval. Supplier returns and credit notes for near-expiry stock. Account customers billed monthly. Locum pharmacist time-boxed access. Generic substitution.

**Platform.** SMS as a first-class channel. Offline sync conflict resolution. Data export on exit. Accounting export.

---

## What the research changed

| Area | Before | After |
|---|---|---|
| Deployment | Cloud SaaS | Cloud **plus local agent at every site** |
| Stock quantity | Pack-level integer | **UoM hierarchy to base unit** |
| Allocation | Unstated | **FEFO, enforced** |
| Insurance | Fee-for-service only | Contract model is configurable; capitation unresolved |
| Tax | One line total | **Per-line, mixed treatment, exempt ≠ zero-rated** |
| Organization | Pharmacy vs wholesaler | **Two licensed pharmacy types; licence set, not label** |
| Patient data | Not addressed | **Consent, retention, read auditing, residency constraint** |
| Controlled drugs | A flag | **Statutory append-only register** |
| Cold chain | Absent | Product flag, location class, excursion quarantine |
| Barcode | "Scan barcode" | **GS1 AI parsing — auto-fills batch and expiry** |
| Payment | Instant | **Asynchronous with pending state** |

---

## Design research — visual direction

Separately from the domain research, the visual direction was validated against published design systems.

The initial direction (light background, white sidebar, teal accent, card-heavy layout) was **retracted** after comparison with Microsoft Fluent 2 and SAP Fiori. Fluent separates neutral, shared and brand palettes and uses lighter neutrals on surfaces to create hierarchy without borders and shadows everywhere; its elevation system derives depth from small differences between surfaces rather than floating every section in a card. Fiori's Quartz Light background is described as subtle, calm and reduced, using a restrained background scheme so content remains the focus.

**Consequence.** The final token set is a cool blue-grey neutral ramp with blue as an accent only, two border weights, five surface levels, and tables promoted over cards. Full specification in [04-design-system.md](04-design-system.md).

One tension in the source material was resolved deliberately: an earlier recommendation to move *away* from SAP toward Linear/Stripe conflicts with the later recommendation to move *toward* Fluent and Fiori. Both agree on the hybrid — **keep enterprise information density, do not inherit enterprise visual age** — so the settled position is Fluent/Fiori structure and surfaces with Linear/Stripe typographic craft.

Sources: [Fluent 2 — colour](https://fluent2.microsoft.design/color) · [Fluent 2 — elevation](https://fluent2.microsoft.design/elevation) · [SAP Fiori — Quartz Light colours](https://www.sap.com/design-system/fiori-design-web/v1-71/foundations/visual/colors/quartz-light-colors)

---

## Open verification register

Carried into [11-risks.md](11-risks.md) as Phase 0 tasks.

| # | Question | Authority | Blocks |
|---|---|---|---|
| 1 | Cloud-hosted per-tenant VSDC permissible? | RRA | Whole architecture |
| 2 | CBHI capitation for contracted private pharmacies? | RSSB | Insurance module |
| 3 | Data residency / cross-border transfer | NCSA | **Infrastructure region** |
| 4 | Current VAT classification for the product mix | Tax adviser | Tax configuration |
| 5 | GS1 mandate dates for Rwanda | Rwanda FDA | Serialization scope |
| 6 | Retail vs wholesale facility standards, current revision | Rwanda FDA | Licence rules |
| 7 | Controlled substance categories, current order | Rwanda FDA | Narcotics register |
