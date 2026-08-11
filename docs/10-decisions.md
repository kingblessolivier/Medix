# 10 — Architecture decision records

Decisions that were expensive to make and would be expensive to reverse. Each records the situation, the options weighed, and what we accept as a consequence.

Status values: **Accepted** · **Proposed** · **Deprecated** · **Superseded**

---

## ADR-001: Stock as an append-only ledger

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Engineering, Product

### Context

Stock must support recall traceability, batch-level margin, day-end reconciliation, regulatory audit, and offline capture merging from two sources. The obvious implementation is a quantity column updated in place.

### Decision

Stock is an append-only sequence of `StockMovement` rows. `StockBalance` is a materialized projection, rebuildable from movements at any time. No code path mutates a quantity.

### Options considered

**A — Mutable quantity column**

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Query performance | Excellent |
| Auditability | Requires a parallel log that can drift |
| Offline merge | Conflicting writes; last-write-wins loses sales |

Pros: simple, fast, familiar. Cons: cannot answer recall questions, cannot attribute cost to a batch, audit is bolted on, and offline merge is unsound.

**B — Append-only ledger with projection**

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Query performance | Good via projection; ledger queries are indexed |
| Auditability | Intrinsic |
| Offline merge | Appends merge cleanly by construction |

Pros: every requirement above falls out of the model. Cons: more writes, a projection to maintain, developers must be disciplined.

### Trade-off analysis

Option A is cheaper until the first recall, the first margin question, or the first offline day — at which point it is not recoverable without a rewrite. The offline requirement alone decides it: two sources appending to a log merge deterministically, whereas two sources decrementing a counter do not.

### Consequences

- **Easier:** recall, audit, batch costing, dispute resolution, offline sync.
- **Harder:** naive "how much do I have" queries; must go through the projection.
- **Revisit:** if the movement table exceeds roughly 100M rows per organization, consider periodic snapshot rows to bound replay cost.

### Action items

- [x] `post_movement()` as the sole write path
- [x] `rebuild-balances` command
- [ ] Invariant tests 1, 2 and 10 in [03-data-model.md](03-data-model.md)

---

## ADR-002: Unit-of-measure hierarchy from day one

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Engineering, Product

### Context

Retail pharmacy in this market sells individual tablets and partial strips. Purchasing happens in packs and cartons. Research (finding 3) confirmed partial-pack dispensing is standard, not an edge case.

### Decision

Every product declares a UoM chain to a base unit. All ledger quantities are stored in base units. Quantities are never bare integers; they carry a UoM. Conversion is integer-only.

### Options considered

**A — Pack-level integers, add units later.** Low initial cost, but retrofitting means rewriting the ledger, POS, pricing, reorder logic and every stock query. Fractional packs would appear in data written before the change.

**B — Decimal quantities.** Avoids a UoM model but introduces rounding error into regulated stock counts and makes "3.4 packs" representable, which is meaningless.

**C — UoM hierarchy with integer base units.** Higher initial cost, correct permanently.

### Trade-off analysis

This is the clearest example in the system of a decision that is cheap now and unaffordable later. Option B is worse than A because it looks like it works.

### Consequences

- **Easier:** partial dispensing, per-UoM pricing, honest margin, purchase/dispense asymmetry.
- **Harder:** every quantity-handling code path must be UoM-aware; developers cannot pass integers around.
- **Revisit:** never. This is foundational.

---

## ADR-003: Cloud plus a local site agent

**Status:** Accepted, pending V1 · **Date:** 2026-08-11 · **Deciders:** Engineering, Compliance

### Context

Two independent hard requirements. RRA's VSDC is distributed as a WAR deployed on the taxpayer's own local webserver, approved per taxpayer (research finding 1). Separately, connectivity is not guaranteed and a POS that stops selling offline is worthless.

### Decision

Ship a small Python agent at every pharmacy site. It bridges to the local VSDC, holds an offline journal for POS, drives local hardware, and syncs to the cloud with idempotency keys.

### Options considered

**A — Pure cloud SaaS.** Simplest to operate and update. Cannot issue fiscal invoices if VSDC must be on-premise. Cannot trade offline. Fails two requirements outright.

**B — Cloud plus local agent.** Satisfies both. Costs a deployable artifact per site, a version skew policy, and a support surface on hardware we do not control.

**C — Fully local install per pharmacy.** Solves both, loses everything the platform exists for — marketplace, consolidation, cross-organization visibility.

### Trade-off analysis

Option C sacrifices the reason the product exists. Between A and B, the deciding factor is that A is not merely inconvenient but non-compliant, if V1 confirms on-premise VSDC. Even if V1 permits cloud-hosted VSDC, the offline requirement independently justifies B.

### Consequences

- **Easier:** offline trading, fiscal compliance, local hardware, low-latency POS.
- **Harder:** deployment, updates, support, version skew, per-site credentials.
- **Accepted:** agents may run one minor version behind the cloud. The API must tolerate this — a pharmacy cannot be forced to upgrade mid-trading-day.
- **Revisit:** if V1 permits cloud VSDC **and** connectivity becomes reliable, the agent could reduce to a thin hardware bridge. Not expected soon.

### Action items

- [ ] **V1** — confirm with RRA whether cloud-hosted per-tenant VSDC is permissible
- [ ] Define the agent version-skew support window

---

## ADR-004: Shared-schema multi-tenancy with row-level isolation

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Engineering

### Context

Hundreds of organizations of four types, with deliberate cross-organization visibility (a wholesale pharmacy sees orders placed with it; a regulator sees across all). Healthcare data makes leakage catastrophic.

### Options considered

| | Shared schema, row-level | Schema per tenant | Database per tenant |
|---|---|---|---|
| Cross-org queries | Natural | Painful | Very painful |
| Migration cost | One | N schemas | N databases |
| Isolation strength | Application-enforced | Strong | Strongest |
| Operational cost | Low | Medium | High |

### Decision

Shared schema, row-level isolation via `TenantModel` and a `tenant_objects` manager bound to request context. Cross-organization visibility through explicitly modelled sharing relations.

### Trade-off analysis

The marketplace makes cross-organization queries a core feature, not an exception — which penalizes schema and database separation heavily. Isolation strength is the trade, and it is mitigated by making the unsafe path visibly wrong: using `Model.objects` in a view is a review failure, and a cross-tenant test suite runs against every endpoint in CI.

### Consequences

- **Easier:** marketplace, consolidation, regulatory oversight, migrations, cost.
- **Harder:** isolation depends on discipline and tests rather than the database.
- **Mitigation:** automated cross-tenant suite; 404 rather than 403 for cross-tenant records so existence is not confirmed.
- **Revisit:** if a customer contractually requires physical isolation.

---

## ADR-005: Regulatory rules as versioned configuration

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Engineering, Compliance

### Context

Rwanda's regulatory environment is actively maintained; CBHI is mid-reform. Product classifications, dispensing rules, tax treatment, insurance coverage and document requirements all change.

### Decision

All such rules are database rows with `effective_from` and `effective_to`. Evaluation is always as-of a date. No regulatory constant in Python, none in React.

### Consequences

- **Easier:** rule changes without deployment; historical transactions remain explainable under the rules that applied then.
- **Harder:** every rule evaluation needs a date; more moving parts; a configuration UI is required.
- **Non-negotiable:** an audit two years from now must reproduce the decision made today.

---

## ADR-006: Organization type is a licence set, not a label

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Product, Compliance

### Context

The initial model treated "pharmacy" and "wholesaler" as different kinds of business. Research (finding 6) established that Rwanda FDA licenses **retail pharmacy** and **wholesale pharmacy** as two premises types — both are pharmacies, both need a responsible pharmacist — and one organization commonly holds both, often alongside an importer licence.

### Decision

`Organization` holds a set of `PremisesLicence` records, one per branch per kind. Capability is derived from held, valid licences. `primary_type` exists only for defaulting the UI.

### Options considered

**A — Single `type` enum.** Simple, and wrong. Cannot represent a wholesale pharmacy with a retail counter, which is common.

**B — Licence set with derived capability.** Models reality, and makes licence expiry automatically revoke capability — which is what the regulator expects.

### Consequences

- **Easier:** mixed businesses, correct licence enforcement, expiry revoking capability for free.
- **Harder:** no single "what type is this org" check; permissions must ask "does this branch hold a valid licence of kind X".
- **Correct:** an expired retail licence should stop the POS. With option A it would not.

---

## ADR-007: Django + DRF + PostgreSQL, React + TypeScript + Vite

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Engineering

### Context

A transaction-heavy, audit-heavy, regulation-heavy system with a data-dense interface, built by a small team.

### Decision

Django 5 with DRF and PostgreSQL on the backend. React 19 with TypeScript, Vite, TanStack Query and Tailwind on the frontend. Python for the agent, sharing domain code with the backend.

### Rationale

Postgres gives transactional integrity, strong constraints, partial indexes and JSONB for dynamic product attributes — all directly used by the data model. Django gives migrations, admin (genuinely useful for regulatory reference data), and a mature permission substrate. Sharing Python between backend and agent means the ledger and UoM logic exist once, which matters because a divergence there produces silent data corruption.

Alternatives considered and rejected: Node/NestJS (would duplicate domain logic in the agent or force a second language there), Rails (smaller local hiring pool), Go (faster, but slower to build regulated CRUD-heavy domains).

### Consequences

- **Easier:** correctness, migrations, shared domain logic, hiring in the region.
- **Harder:** Python throughput ceilings — mitigated by Celery and read projections.
- **Revisit:** only if a specific endpoint becomes a measured bottleneck.

---

## ADR-008: Type scale at 20/13

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Design, Product

### Context

The source design material contained two conflicting scales: 24/14 (Linear/Stripe end) and 20/13 (Fiori end). Both are defensible.

### Decision

20px page titles, 13px body and table text.

### Rationale

At 24/14 the same table loses roughly four visible rows at typical laptop height. In a system where the table *is* the interface and a pharmacist is scanning expiring batches, **rows on screen beats generous headings**. Craft is preserved through the Linear/Stripe end of the range — tight letter-spacing, weight 500–600 rather than 700, hierarchy from colour and space rather than bold.

### Consequences

- **Easier:** density, comparison, less scrolling.
- **Harder:** less breathing room; spacing and colour must carry hierarchy that size no longer does.
- **Revisit:** if usability testing shows fatigue at 13px among older users, revisit as an accessibility setting rather than a global change.

---

## ADR-009: Dark theme is in scope for v1

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Design, Product

### Context

The source design material specified light only. But the product's own worked scenarios describe pharmacists working at 11pm, and night shifts are a real use case in this domain.

### Decision

Both themes are first-class. Dark is a purpose-built Fluent-style neutral ramp, not an inversion, with the brand lifted to `#4CA6E8` because `#0078D4` fails on a dark ground.

### Rationale

Retrofitting a theme after screens exist means auditing every literal colour in the codebase. Since the token structure already names every surface as a level, supporting a second theme costs one extra token block now and nearly nothing later.

### Consequences

- **Easier:** night use, accessibility, future theming.
- **Harder:** every component must be checked in both themes; design review doubles.
- **Enforced:** literal colour values in components are a review failure.

---

## ADR-010: Documents are designed artifacts, not digitized forms

**Status:** Accepted · **Date:** 2026-08-11 · **Deciders:** Design, Product

### Context

Documents leave the system and are seen by suppliers, customers, insurers and inspectors more often than the UI is. The default path — HTML-to-PDF with browser styling — produces something that looks like a form printout.

### Decision

A single template family with a shared five-region anatomy, styled from the same design tokens as the application, rendered to both web preview and PDF from **one** template so the two cannot diverge. Near-monochrome for thermal and laser printers. Full specification in [18-document-design.md](18-document-design.md).

### Consequences

- **Easier:** consistency, brand credibility, preview/PDF parity by construction.
- **Harder:** headless Chromium in the rendering path; visual regression tests per template.
- **Constraint:** RRA-mandated fiscal invoice content is authoritative and overrides layout preference wherever they conflict.

---

## Pending decisions

| # | Decision | Blocked by | Owner |
|---|---|---|---|
| P1 | Insurance workflow shape — fee-for-service or capitation | V3, RSSB | Product |
| P2 | Infrastructure region | V2, data residency | Engineering + Legal |
| P3 | Monetization — subscription, transaction fee, or both | Business | Business |
| P4 | Serialization scope | GS1 mandate dates for Rwanda | Compliance |
| P5 | Mobile money provider priority | Commercial terms | Engineering |
