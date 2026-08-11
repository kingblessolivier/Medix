# Medix

A pharmaceutical commerce, operations, compliance and intelligence platform for Rwanda, serving **retail pharmacies and wholesale pharmacies** over one shared core.

Medix is not five digital notebooks. It is **one transaction ledger with four windows cut into it** — for pharmacists, pharmacy owners, wholesale pharmacies and importers, and regulators.

```
Product → Listing → Batch → Inventory → Purchase → Order → Shipment
  → Goods Receipt → Stock → Prescription → Sale → Insurance Claim
  → EBM Invoice → Financial Record → Executive Report
```

Every screen is a lens on a segment of that chain. If any link is bypassable, the chain rots and the paper notebooks come back.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Django 5 · Django REST Framework · PostgreSQL |
| Frontend | React 19 · TypeScript · Vite · TanStack Query · Tailwind |
| Local agent | Python service at each pharmacy site (VSDC bridge + offline POS) |
| Auth | JWT, per-organization multi-tenancy with row-level isolation |

---

## Documentation

Start at **[docs/00-index.md](docs/00-index.md)**. Documents 01–03 are prerequisites for writing any code.

| # | Document | What it covers |
|---|---|---|
| 01 | [Overview](docs/01-overview.md) | Problem, thesis, two pharmacy types, actors, consolidation, guardrails |
| 02 | [Architecture](docs/02-architecture.md) | Topology, Django layering, tenancy, local agent, offline, deployment |
| 03 | [Data model](docs/03-data-model.md) | Stock ledger, UoM hierarchy, batches and cost, entities, invariants |
| 04 | [Design system](docs/04-design-system.md) | Tokens, both themes, type, icons, shell, components, states |
| 05 | [Modules](docs/05-modules.md) | Organization types, module map, every module |
| 06 | [Compliance](docs/06-compliance.md) | Rwanda FDA, RRA/EBM, VAT, data protection, narcotics, cold chain, GS1 |
| 07 | [API](docs/07-api.md) | Conventions, money and quantity shapes, errors, idempotency, agent sync |
| 08 | [Development](docs/08-development.md) | Setup, conventions, git, adding a module |
| 09 | [Roadmap](docs/09-roadmap.md) | Phases 0–8, dependency graph, release policy |
| 10 | [Decisions](docs/10-decisions.md) | ADR-001 to ADR-010, pending decisions |
| 11 | [Risks](docs/11-risks.md) | Severity-ranked register, verification tasks V1–V8 |
| 12 | [Glossary](docs/12-glossary.md) | Domain, regulatory and system terms |
| 13 | [Research](docs/13-research.md) | Eleven findings, what each changed, sources |
| 14 | [Requirements](docs/14-requirements.md) | Goals, non-goals, user stories, P0/P1/P2, success metrics |
| 15 | [Testing](docs/15-testing.md) | The mandatory ten, E2E, performance, CI |
| 16 | [Security](docs/16-security.md) | Threat model, capability permissions, patient data, audit |
| 17 | [Operations](docs/17-operations.md) | Deployment, monitoring, runbooks, site onboarding, on-call |
| 18 | [Document design](docs/18-document-design.md) | Modern document standard, anatomy, print, preview parity |
| 19 | [Screens](docs/19-screens.md) | Screen-by-screen specification |
| 20 | [Git workflow](docs/20-git-workflow.md) | Branches, PR, staging → main, releases, hotfix |
| 21 | [Data visualization](docs/21-data-visualization.md) | Chart forms, validated palettes, banned charts, inventory |
| 22 | [Components](docs/22-components.md) | Every component — tables, inputs, dropdowns, search, icons, surfaces |
| 23 | [UI copy](docs/23-ui-copy.md) | Voice, length limits, banned words |
| 24 | [Database](docs/24-database.md) | Physical schema, keys, indexes, partitioning, RLS, pooling |
| 25 | [Request pipeline](docs/25-request-pipeline.md) | Latency budget, caching, async, rate limits, protection |
| 26 | [Typography](docs/26-typography.md) | Font families, loading, scale, numerals, print |
| 27 | [Layout](docs/27-layout.md) | Grid, spacing, responsive, pagination, scroll, z-index |

Also: [CONTRIBUTING](CONTRIBUTING.md) · [CHANGELOG](CHANGELOG.md) · [CLAUDE.md](CLAUDE.md) · `medix-system-design.html` (published visual specification).

---

## The four load-bearing decisions

These are settled and non-negotiable. Everything else is downstream of them.

**1. Stock is an append-only ledger, never a mutable counter.**
`stock = stock - 1` kills the system. Every movement is an immutable row referencing why. Recall answers in seconds, margin knows batch cost, disputes settle by replay, and audit comes free.

**2. Allocation is FEFO.**
First *Expired*, First Out — not FIFO. The system auto-selects the nearest-expiry batch with available stock. Override requires a reason and is logged.

**3. Unit-of-measure hierarchy from day one.**
This market sells six tablets, not a box of a hundred. Carton → pack → blister → unit, all reconciling against one batch. Retrofitting this means rewriting the ledger, POS, and pricing.

**4. Cloud plus a local agent.**
RRA's VSDC runs on the taxpayer's own local server, so a pure cloud SaaS cannot issue fiscal invoices. The same agent is what keeps POS working offline. Two hard requirements, one component.

---

## Two things to verify before building the modules they affect

| Question | Blocks | Why it matters |
|---|---|---|
| Can VSDC be hosted per-tenant in the cloud, or must it be on-premise? | Fiscal module | Decides cloud-only vs cloud+agent for the whole product |
| Does CBHI capitation apply to contracted private pharmacies? | Insurance module | May invalidate the claim-and-reimburse model for the dominant payer |

See [docs/11-risks.md](docs/11-risks.md).

---

## Quick start

```bash
make setup
```

Then see [docs/08-development.md](docs/08-development.md).

---

## Project layout

```
Medix/
├── backend/           Django project
│   ├── config/        settings, urls, wsgi/asgi
│   ├── core/          tenancy, audit, base models, permissions
│   ├── catalog/       products, attributes, UoM, registration
│   ├── inventory/     ledger, batches, locations, FEFO, cold chain
│   ├── commerce/      marketplace, RFQ, orders, procurement
│   ├── imports/       import requests, consolidation, shipments
│   ├── sales/         POS, prescriptions, tills, day end
│   ├── insurance/     schemes, eligibility, claims
│   ├── fiscal/        invoice engine, VSDC bridge
│   ├── compliance/    licences, registrations, recalls, narcotics register
│   ├── documents/     document generation and rendering
│   └── analytics/     reporting and executive intelligence
├── frontend/          React application
│   └── src/
│       ├── design/    tokens, primitives
│       ├── components/ui, navigation, data, transaction, pharma
│       ├── modules/   feature modules mirroring backend apps
│       └── portals/   pharmacy, owner, wholesaler, admin
├── agent/             local site agent (VSDC bridge, offline sync)
└── docs/              this documentation
```

---

## Non-negotiable guardrails

- **Medix never gives clinical advice.** It carries approved reference product information and links official leaflets. It never maps symptom to drug. Diagnosis and prescribing are regulated professional acts.
- **OCR never authorizes.** It may extract prescription fields; a registered pharmacist confirms, and their council number attaches to the dispensing event.
- **Never overstate profit.** If there is not enough accounting data for a reliable net figure, the label reads *estimated operating result*.
- **The Assistant assists.** It never silently performs an action that moves stock, money, or a regulated record.
