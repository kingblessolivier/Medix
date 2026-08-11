# 02 — Architecture

## Shape of the system

Medix is a cloud application **plus a small agent running at each pharmacy site**. That second component is not optional, and the reason is worth stating precisely because it drives everything else.

### Why a local agent exists

Two independent hard requirements converge on the same solution.

**Fiscal compliance.** RRA's Virtual Sales Data Controller (VSDC) is distributed as a WAR file deployed on the taxpayer's own local webserver. Your system calls its web services over the local network. Each taxpayer applies for VSDC separately and receives RRA approval before activation, with separate test and production instances. A pure cloud SaaS therefore **cannot issue fiscal invoices** on a pharmacy's behalf.

> **Verification task.** Confirm with RRA whether a cloud-hosted per-tenant VSDC is permissible. If it is, the agent becomes optional for fiscal purposes but is still required for the second reason. See [11-risks.md](11-risks.md).

**Connectivity.** Internet is not guaranteed. A point of sale that stops selling when the connection drops is worthless. The POS must work offline and reconcile later.

One component satisfies both.

### Topology

```
┌─────────────────────────── CLOUD ───────────────────────────┐
│                                                             │
│   React SPA  ──HTTPS──▶  Django + DRF  ──▶  PostgreSQL      │
│   (4 portals)                  │                            │
│                                ├──▶  Redis (cache, queues)  │
│                                ├──▶  Celery (async work)    │
│                                └──▶  Object storage (docs)  │
│                                                             │
└───────────────────────────────▲─────────────────────────────┘
                                │ sync (queued, idempotent)
┌───────────────────────────────┴──── PHARMACY SITE ──────────┐
│                                                             │
│   Medix Agent (Python)                                      │
│     ├── local SQLite store (offline POS journal)            │
│     ├── VSDC bridge  ──LAN──▶  VSDC (RRA WAR on local host) │
│     ├── hardware: barcode scanner, thermal printer,          │
│     │             label printer, cash drawer                 │
│     └── sync engine (outbound queue, conflict resolution)    │
│                                                             │
│   POS browser  ──localhost──▶  Agent                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

The POS front end talks to the agent when it is present, and to the cloud when it is not. Everything else in the product is cloud-only.

## Backend — Django

### Application layout

Each Django app owns a bounded piece of the domain. Domain logic lives in `services.py`, never in views or serializers.

| App | Owns |
|---|---|
| `core` | Organizations, users, roles, permissions, tenancy, audit base models, money, sequences |
| `catalog` | Products, product types, dynamic attributes, UoM hierarchy, registration data, vendor listings |
| `inventory` | Stock ledger, batches, locations, FEFO allocation, transfers, adjustments, cold chain, stock take |
| `commerce` | Marketplace, RFQ, quotations, purchase orders, suppliers, receiving |
| `imports` | Import requests, demand consolidation, supplier sourcing, shipments, landed cost |
| `sales` | POS, sale journal, tills, shifts, day end, prescriptions, returns |
| `insurance` | Schemes, contracts, coverage rules, eligibility, claims, receivables |
| `fiscal` | Invoice engine, tax treatment, VSDC integration boundary, fiscal reconciliation |
| `compliance` | Product registration, premises licences, pharmacist registration, narcotics register, recalls, inspections, ADR reports |
| `documents` | Document templates, numbering, rendering, PDF generation, storage |
| `analytics` | Aggregations, margin, vendor performance, branch comparison, attention feed |
| `assistant` | Search and action layer over the above |

### Layering

```
HTTP  →  urls.py  →  views.py (DRF)  →  serializers.py
                            │
                            ▼
                      services.py          ← all business rules live here
                            │
                            ▼
                       models.py           ← persistence + invariants only
```

Rules:

- A view never contains a business rule. It authorizes, deserializes, calls a service, serializes.
- A service function is the transaction boundary. It takes and returns plain values or model instances, and is independently testable.
- A model may enforce an invariant (a constraint, a clean method) but never orchestrates a workflow.
- Anything touching the ledger, FEFO, UoM, money, or tax **must** have tests.

### Multi-tenancy

Shared database, shared schema, **row-level isolation by organization**.

- Every tenant-scoped model inherits `core.models.TenantModel`, which carries `organization` and exposes `tenant_objects`, a manager filtered by the active organization from request context.
- `Model.objects` remains unfiltered for admin and background work, and using it in a view is a review failure.
- A `TenantMiddleware` resolves the active organization from the authenticated user and sets it in a context var.
- Cross-organization visibility (a wholesaler seeing an order placed with them, a regulator seeing everything) is granted through explicit **sharing relations**, never by disabling the tenant filter.

Multi-tenancy leakage in healthcare is catastrophic. Treat every unfiltered query as a defect.

### Audit

`core.models.AuditedModel` provides `created_by`, `created_at`, `modified_by`, `modified_at`, `approved_by`, `approved_at`, `rejected_by`, `rejected_at`, `reason`. Every user-mutable model inherits it. Do not add ad-hoc audit fields.

Separately, `core.models.AuditEvent` records an append-only stream of significant actions — who, what, when, from where, old value, new value. Reads of patient data are also recorded, because data protection law requires it.

### Money

- Stored as `BigIntegerField` in **minor units** plus an explicit currency code. Never `FloatField`.
- A `Money` value object handles arithmetic, and refuses to add two different currencies.
- RWF is the operating currency. Import quotations may be in USD or EUR, in which case the quote records currency, rate, rate date, and whether the rate is fixed or indicative.

### Async work

Celery with Redis. Used for document rendering, fiscal submission retries, claim submission, notification dispatch, analytics rollups, and sync reconciliation. Anything that can fail and must be retried goes on a queue, never in a request.

### Configuration over constants

Regulatory rules — product classifications, dispensing rules, tax treatment, insurance coverage, document requirements — are **versioned database configuration with effective dates**, not Python constants and not React conditionals.

A transaction from six months ago must remain explainable under the rules that applied then. Every rule row carries `effective_from` and `effective_to`, and evaluation is always as-of a date.

## Frontend — React

### Structure

```
src/
├── design/
│   ├── tokens.css          the single source of colour, type, spacing, radius
│   └── theme.ts            token types + Tailwind bridge
├── components/
│   ├── ui/                 Button, Input, Select, Badge, Dialog, Drawer,
│   │                       Dropdown, Tooltip, Tabs, Card, Skeleton, EmptyState
│   ├── navigation/         AppShell, Sidebar, TopBar, Breadcrumb, CommandPalette
│   ├── data/               DataTable, DataToolbar, Pagination, ColumnSelector,
│   │                       DensityControl, FilterBar
│   ├── transaction/        TransactionHeader, TransactionTabs, TransactionSection,
│   │                       ItemGrid, ApprovalTimeline, StatusStepper
│   └── pharma/             BatchStatus, ExpiryIndicator, StockIndicator,
│                           ProductBadge, BarcodeScanner, ColdChainBadge
├── modules/                one folder per backend app
├── portals/                pharmacy | owner | wholesaler | admin
└── lib/                    api client, auth, money, uom, offline queue
```

### Build order — this is a rule, not a preference

```
tokens  →  primitives  →  module templates  →  screens
```

Never design screen by screen. That is how enterprise applications become fifty individually designed pages that happen to share a logo.

A **module template** is the reusable arrangement for a class of screen: the list template (page header + toolbar + DataTable + drawer), the transaction template (document header + tabs + sections + item grid + approval timeline), the console template (header + sections + timeline). Individual screens configure a template; they rarely lay out from scratch.

### State

- **Server state**: TanStack Query. Cache keys are resource-scoped and organization-scoped.
- **UI state**: React state and context. No global store unless a real need appears.
- **Offline**: an outbound mutation queue persisted to IndexedDB, drained by the agent bridge or directly when online.

### Routing and portals

One SPA, four portal shells. The portal is resolved from the authenticated user's active organization type and role, and determines the navigation tree and default landing route. Route guards are derived from permissions, never from role name string comparison.

## The interaction model

```
SEARCH  →  TABLE  →  DETAIL DRAWER  →  FULL TRANSACTION
```

Drawers preserve context and are used for preview, quick edit, quick information, and activity history. Full pages are reserved for genuine workflows: purchase order creation, import request, receiving, POS, prescription processing, insurance claim, product creation.

This is what makes an ERP feel fast rather than click-heavy.

## Offline strategy

Only the POS is offline-capable. Everything else requires connectivity.

| Concern | Approach |
|---|---|
| Catalogue and stock | Agent holds a read replica of the site's products, batches and balances, refreshed on connect |
| Sale capture | Written to the local journal first, always. The sale is real the moment it is local |
| Fiscal invoice | Submitted to local VSDC immediately; VSDC itself queues to RRA |
| Sync | Outbound queue with idempotency keys. Server deduplicates by key |
| Conflict | Sales never conflict — they are appends. Stock balance is recomputed server-side from the merged ledger |
| Oversell | Agent enforces the site's own balance. Cross-site oversell is resolved server-side and surfaced as an exception, not silently corrected |
| Clock | Agent records both local and server-received timestamps; fiscal ordering uses the VSDC response |

The ledger design is what makes this tractable. Appending movements from two sources merges cleanly; reconciling two mutable counters does not.

## Deployment

| Environment | Purpose |
|---|---|
| Local | Docker Compose — Postgres, Redis, backend, frontend, mock VSDC |
| Staging | Full stack, RRA **test** VSDC, anonymized data |
| Production | Hosting region constrained by data protection law — see below |

### Data residency

Rwanda's Law 058/2021 treats health data as sensitive personal data and constrains cross-border transfer. Hosting region must be confirmed against that law **before** infrastructure is chosen, not after. See [06-compliance.md](06-compliance.md).

### Observability

Structured JSON logs with organization and correlation id on every request. Error tracking. Metrics on fiscal submission success rate, sync lag per site, claim turnaround, and queue depth — these are the operational signals that actually predict a bad day.

## Integration boundaries

Every external system sits behind an interface owned by us, so a provider change is a new adapter and not a rewrite.

| Boundary | Interface | Notes |
|---|---|---|
| Fiscal | `fiscal.services.FiscalIntegrationService` | VSDC today. The POS must never know the shape of the RRA API |
| Payments | `sales.payments.PaymentProvider` | MTN MoMo and Airtel Money. Both are **asynchronous** — request-to-pay then callback. A sale therefore has a pending payment state that may resolve later or time out |
| Insurance | `insurance.providers.SchemeAdapter` | Per-scheme. Coverage rules are configuration |
| Messaging | `core.notify.Channel` | SMS, email, in-app. SMS is first-class, not an afterthought |
| Regulatory reference | `compliance.sources` | Rwanda FDA product data ingestion |

## Security

- JWT access and refresh, short-lived access tokens, rotation on refresh.
- Permissions are capability-based and checked in services, not only in views.
- Patient data access is logged as an `AuditEvent`, including reads.
- Locum and temporary access is time-boxed at the grant, not by convention.
- Secrets from environment, never committed. Agent credentials are per-site and revocable.
- Rate limiting on authentication and on the assistant.
