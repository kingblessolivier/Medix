# 11 — Risks

Ranked by the cost of discovering them late. The first three are not build tasks — they are verification tasks, and two can invalidate architecture.

---

## Severity scale

| Level | Meaning |
|---|---|
| **Critical** | Discovering this late forces an architectural rewrite or makes the product unlawful to operate |
| **High** | Forces a significant module rewrite or produces materially wrong numbers |
| **Medium** | Costly rework confined to one module |
| **Low** | Ordinary engineering risk |

---

## Critical

### R1 — VSDC deployment model

**Risk.** RRA's VSDC is distributed as a WAR file deployed on the taxpayer's own local webserver, approved per taxpayer. If that is mandatory, a pure cloud SaaS cannot issue fiscal invoices, and every pharmacy needs a local agent.

**If discovered late.** The deployment model, sync design, update strategy, support model and pricing all change at once. This is not a refactor.

**Mitigation.** Architecture already assumes cloud-plus-agent, so being wrong in the permissive direction costs nothing structural. The agent is independently justified by offline POS.

**Action — V1.** Confirm with RRA whether a cloud-hosted per-tenant VSDC is permissible. **Phase 0, blocking.**

---

### R2 — Data residency under Law 058/2021

**Risk.** Health data is sensitive personal data with cross-border transfer restrictions. Hosting region may be constrained.

**If discovered late.** Migrating a live regulated system between regions with patient data in it, while trading, is among the worst positions to be in.

**Mitigation.** Choose no region before this is answered. Design backups and object storage to inherit the same constraint.

**Action — V2.** Confirm residency and transfer requirements with legal counsel and NCSA guidance. **Phase 0, blocking, before any infrastructure is provisioned.**

---

### R3 — CBHI capitation

**Risk.** CBHI is reportedly moving from retrospective fee-for-service to capitation. If that applies to contracted private pharmacies, the claim-and-reimburse workflow is the wrong shape for the dominant payer.

**If discovered late.** The insurance module — eligibility, coverage calculation, claim submission, rejection handling, receivables aging — is built for a model nobody uses.

**Mitigation.** `SchemeContract.model` supports both shapes. **Do not build the insurance module until this is answered** — the roadmap defers it to Phase 5 for exactly this reason.

**Action — V3.** Confirm with RSSB. **Blocking for Phase 5.**

---

## High

### R4 — Unit-of-measure retrofit

**Risk.** Building pack-level quantities and adding partial-pack dispensing later.

**If discovered late.** Rewrites the ledger, POS, pricing, reorder logic and every stock query. Data written before the change contains meaningless fractional packs.

**Mitigation.** UoM hierarchy is load-bearing decision #2 and lands in Phase 1. **Already mitigated by sequencing.**

---

### R5 — Batch-level costing

**Risk.** Not attributing landed cost to batches means gross margin is a guess.

**If discovered late.** Every figure in the executive portal is wrong, and the first accountant to check destroys trust in the product.

**Mitigation.** `Batch.unit_cost_base` exists from Phase 1. Landed-cost apportionment is tested to sum to the total to the minor unit.

---

### R6 — Landed-cost apportionment on consolidated imports

**Risk.** Freight, duty and clearance are charged to the shipment, not the product. Apportioning them incorrectly across participants corrupts batch cost for everyone in the consolidation.

**If discovered late.** Wrong margins for the highest-value transactions in the system, discovered by a customer rather than by us.

**Mitigation.** `LandedCostComponent` with an explicit apportionment basis. Mandatory test: components sum to the total exactly.

---

### R7 — Offline sync duplication

**Risk.** A site agent retries and creates duplicate sales, corrupting both stock and revenue.

**If discovered late.** Silent data corruption, discovered at reconciliation, with no clean way to unwind.

**Mitigation.** Idempotency keys required on every effectful endpoint, server-side key storage, and an explicit offline test in the development workflow. **Any change to POS or the sync API must be exercised offline.**

---

### R8 — Multi-tenancy leakage

**Risk.** A missing tenant filter exposes one pharmacy's data to another. In healthcare this is a reportable breach.

**Mitigation.** `tenant_objects` as the default manager; `Model.objects` in a view is a review failure; an automated cross-tenant suite runs against every endpoint in CI; cross-tenant records return 404, not 403.

---

### R9 — Fiscal exceptions accumulating unseen

**Risk.** Sales complete but fiscal submission fails, and the backlog is invisible until RRA raises it.

**Mitigation.** A completed sale must have a fiscal outcome — accepted, or visible in an exception queue. The queue is an operational screen, not a log file, and submission success rate is a monitored metric.

---

## Medium

### R10 — Scope

**Risk.** Medix is genuinely five products. Attempting them concurrently delivers none.

**Mitigation.** Phased roadmap with a single pilot target (Phase 2, retail single site). Non-goals documented explicitly in [14-requirements.md](14-requirements.md).

### R11 — Tax classification

**Risk.** Medicines are VAT-exempt, other stock is not, and exempt is not zero-rated. Getting this wrong misstates both tax and margin.

**Mitigation.** Tax treatment is a product attribute resolved per line against effective-dated rules. Confirm the classification list with a tax adviser; configure rather than hardcode.

### R12 — Cold chain omission

**Risk.** Temperature-sensitive stock handled as ambient. Patient safety and GSDP exposure.

**Mitigation.** Product flag, location temperature class, excursion capture with automatic quarantine. Validation prevents placing a cold-chain batch in an ambient location.

### R13 — Adoption

**Risk.** Pharmacists revert to notebooks if the system is slower than paper at the counter.

**Mitigation.** POS median transaction time at or below the paper baseline by day 14 is an explicit success metric. Offline capability removes the most common reason to fall back.

### R14 — Consolidation commitment

**Risk.** One participant withdrawing drops a consolidated import below MOQ and breaks it for everyone.

**Mitigation.** Explicit binding commitment point and deposit, with allocation policy stated **before** commitment.

### R15 — Agent version skew

**Risk.** Sites update on their own schedule; a cloud change breaks an older agent mid-trading-day.

**Mitigation.** The API tolerates agents one minor version behind. Additive changes only within a version; clients ignore unknown fields and handle unknown enum values.

---

## Low

| # | Risk | Mitigation |
|---|---|---|
| R16 | GS1 mandate dates unconfirmed | Build the parsing capability regardless — it pays for itself in data entry alone |
| R17 | Mobile money provider terms | Both providers behind one `PaymentProvider` interface |
| R18 | UI copy growing verbose over time | Length limits enforced in review; see docs/23-ui-copy.md |
| R19 | Document rendering performance | Render on a queue, cache immutably by document version |
| R20 | Ledger table growth | Indexed by tenant, location, batch and time; snapshot rows if replay cost becomes material |

---

## Verification register

Tracked as Phase 0 tasks.

| # | Question | Authority | Blocks | Status |
|---|---|---|---|---|
| V1 | Cloud-hosted per-tenant VSDC permissible? | RRA | Architecture | Open |
| V2 | Data residency / cross-border transfer | NCSA, legal | Infrastructure region | Open |
| V3 | CBHI capitation for contracted private pharmacies? | RSSB | Insurance module | Open |
| V4 | VAT classification for the product mix | Tax adviser | Tax configuration | Open |
| V5 | GS1 mandate dates for Rwanda | Rwanda FDA | Serialization scope | Open |
| V6 | Retail and wholesale facility standards, current revision | Rwanda FDA | Licence rules | Open |
| V7 | Controlled substance categories, current Ministerial Order | Rwanda FDA | Narcotics register | Open |
| V8 | Monetization model | Business | Onboarding, portal priority | Open |

---

## Review

This register is reviewed at every phase boundary. A risk is closed only when the mitigation is **implemented and tested**, not when it is designed.
