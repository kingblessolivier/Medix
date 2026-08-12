# Documentation index

Medix — a pharmaceutical commerce, operations, compliance and intelligence platform for Rwanda, serving **retail pharmacies and wholesale pharmacies** over one transaction ledger.

---

## Read in this order

### Before writing any code — prerequisites

| # | Document | Why it is a prerequisite |
|---|---|---|
| [01](01-overview.md) | **Overview** | The problem, the thesis, actors, wedge, guardrails |
| [02](02-architecture.md) | **Architecture** | Services, the local agent, offline strategy, tenancy |
| [03](03-data-model.md) | **Data model** | The ledger, unit of measure, batches — the three decisions that cannot be retrofitted |

Code written without these tends to violate an invariant that is expensive to unwind.

### Then, by what you are doing

| Doing | Read |
|---|---|
| Setting up, writing code | [08 Development](08-development.md) · [CONTRIBUTING](../CONTRIBUTING.md) |
| Building a screen | [04 Design system](04-design-system.md) · [19 Screens](19-screens.md) |
| Building a feature | [05 Modules](05-modules.md) · [14 Requirements](14-requirements.md) |
| Building an endpoint | [07 API](07-api.md) |
| Anything regulated | [06 Compliance](06-compliance.md) |
| Producing a document | [18 Document design](18-document-design.md) |
| Writing tests | [15 Testing](15-testing.md) |
| Deploying, on call | [17 Operations](17-operations.md) · [16 Security](16-security.md) |
| Planning | [09 Roadmap](09-roadmap.md) · [11 Risks](11-risks.md) |
| Wondering why | [10 Decisions](10-decisions.md) · [13 Research](13-research.md) |
| Confused by a term | [12 Glossary](12-glossary.md) |

---

## Full set

**Foundation**
- [01 — Overview](01-overview.md) — problem, thesis, actors, demand consolidation, business model, guardrails
- [02 — Architecture](02-architecture.md) — topology, Django layering, tenancy, the local agent, offline, deployment, integration boundaries
- [03 — Data model](03-data-model.md) — stock ledger, UoM hierarchy, batches and cost, catalog, sales, prescriptions, insurance, imports, compliance entities, invariants

**Design**
- [04 — Design system](04-design-system.md) — tokens, both themes, typography, icons, metrics, shell, layout rules, components, states, accessibility, ten principles
- [18 — Document design](18-document-design.md) — modern document standard, anatomy, typography, print, preview parity, numbering, register
- [19 — Screens](19-screens.md) — screen-by-screen specification

**Product**
- [05 — Modules](05-modules.md) — organization types, module map, every module
- [14 — Requirements](14-requirements.md) — problem, goals, non-goals, personas, user stories, P0/P1/P2, non-functional, success metrics
- [09 — Roadmap](09-roadmap.md) — phases 0–8, dependency graph, release policy

**Engineering**
- [07 — API](07-api.md) — conventions, money and quantity shapes, pagination, errors, idempotency, endpoints, agent sync
- [08 — Development](08-development.md) — setup, layout, conventions, git, adding a module, troubleshooting
- [15 — Testing](15-testing.md) — the mandatory ten, backend, frontend, E2E, performance, CI
- [16 — Security](16-security.md) — threat model, auth, capability model, tenancy, patient data, agent security, audit, incident response
- [17 — Operations](17-operations.md) — environments, deployment, monitoring, runbooks, backups, site onboarding, on-call

**Regulatory and context**
- [06 — Compliance](06-compliance.md) — Rwanda FDA, legal status, narcotics register, pharmacist registration, RRA fiscal, VAT, data protection, insurance, cold chain, GS1, recalls
- [13 — Research](13-research.md) — eleven findings, what each changed, sources, verification register
- [11 — Risks](11-risks.md) — severity-ranked register, verification tasks V1–V8
- [10 — Decisions](10-decisions.md) — ADR-001 to ADR-010, pending decisions
- [12 — Glossary](12-glossary.md) — domain, regulatory and system terms

**Repository root**
- [README](../README.md) · [CLAUDE.md](../CLAUDE.md) · [CONTRIBUTING](../CONTRIBUTING.md) · [CHANGELOG](../CHANGELOG.md)
- `medix-system-design.html` — published visual specification

---

## The load-bearing facts

If you remember nothing else from this documentation set:

**Two pharmacy types, one core.** Retail and wholesale are both Rwanda FDA–licensed pharmacies sharing catalog, batches, ledger, FEFO, UoM, compliance and analytics. They differ in who they sell to. Capability derives from **held licences**, never a type label.

**Stock is a ledger, not a counter.** No mutable quantity column exists. `post_movement()` is the only write path.

**FEFO, not FIFO.** Nearest expiry first, always, with logged override.

**Quantities carry a unit of measure.** Six tablets from a pack of a hundred is the normal case in this market.

**Cloud plus a local agent.** VSDC is on-premise and connectivity is not guaranteed. One component solves both.

**Regulatory rules are effective-dated configuration.** A transaction from last year must remain explainable under last year's rules.

**Three questions are still open and two can invalidate architecture** — VSDC deployment model, data residency, CBHI capitation. See [11-risks.md](11-risks.md).

---

## Keeping this current

Documentation is updated in the same pull request as the change it describes. Stale documentation is worse than none, because it is trusted.

| Changed | Update |
|---|---|
| Behaviour | The module or design doc |
| API contract | [07](07-api.md) and regenerate the schema |
| A decision that took real thought | A new ADR in [10](10-decisions.md) |
| A newly found risk | [11](11-risks.md) |
| A domain term | [12](12-glossary.md) |
| Anything user-visible | [CHANGELOG](../CHANGELOG.md) |
- [28 — Distribution specification](28-distribution-spec.md) — depot-to-retail model, the schema reconciliation, and the open queue
