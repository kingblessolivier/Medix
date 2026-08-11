# Medix — working instructions

Read `docs/01-overview.md`, `docs/02-architecture.md` and `docs/03-data-model.md` before writing code in this repo. They are prerequisites, not background reading.

## What this is

A pharmaceutical commerce, operations, compliance and intelligence platform for Rwanda. One transaction ledger, four portals (retail pharmacy, owner, wholesale pharmacy/importer, regulator).

**Two licensed pharmacy types share one core.** Retail and wholesale are both Rwanda FDA–licensed pharmacies with a responsible pharmacist; they differ in who they sell to. Catalog, batches, ledger, FEFO, UoM, compliance and analytics are identical. Capability derives from **held licences**, never a single type field — an organization may hold retail, wholesale and importer licences at once.

Stack: Django 5 + DRF + PostgreSQL backend, React 19 + TypeScript + Vite + Tailwind frontend, plus a Python local agent that runs at each pharmacy site.

## Rules that override convenience

**Never mutate stock quantity directly.** There is no `quantity` column you decrement. All stock changes go through `inventory.services.post_movement()`, which appends a `StockMovement` row. Balances are derived. If you find yourself writing `stock.quantity -= n`, stop — you are breaking the audit trail, recall traceability, and batch costing at once.

**All quantities carry a unit of measure.** Never store a bare integer quantity. Use `Quantity(value, uom)` and convert through `catalog.uom.to_base()`. A pack is not a tablet.

**Allocation is FEFO.** Use `inventory.services.allocate_fefo()`. Never pick a batch by insertion order or by id.

**Money is integer minor units.** RWF amounts are stored as `BigIntegerField` in minor units with an explicit currency. Never `FloatField`, never bare `DecimalField` without currency.

**Every model that a user can change inherits `core.models.AuditedModel`.** It carries created/modified/approved/rejected by and at, plus reason. Do not add ad-hoc audit fields.

**Every tenant-scoped query goes through the tenant manager.** Models inherit `core.models.TenantModel`; default manager filters by the active organization. Never use `Model.objects.all()` in a view — use `Model.tenant_objects`.

**Regulatory rules are versioned configuration with effective dates**, never hardcoded constants in Python or React. A transaction from six months ago must remain explainable under the rules that applied then.

## Frontend rules

**Build the design system before screens.** Order is tokens → primitives → module templates → screens. Never design screen by screen; that is how enterprise apps become inconsistent.

**Use design tokens, never literal colors.** `var(--surface)` not `#FFFFFF`. Tokens are defined once in `src/design/tokens.css` and flow to Tailwind theme config.

**One icon family: Lucide.** 16–18px at 1.75–2 stroke. Never mix in another icon set, never use emoji as icons.

**Tables are first-class.** Use the shared `DataTable`. It provides sort, filter, column visibility, density, row selection, bulk actions and pagination. Do not hand-roll a `<table>` for a data list.

**Drawers for inspection, pages for workflows.** Click a row → drawer. Full pages only for purchase order creation, import request, receiving, POS, prescription processing, insurance claim, product creation.

**Type scale is 20/13.** Page title 20/600, section 14/600, body and table 13/400, table header 12/600, label 12/500, helper 11/400. Do not introduce other sizes.

**The interface states, it does not explain.** Buttons and labels 3 words, page descriptions 8, errors 12. No "please", no "sorry", no "successfully", no reassurance, no teaching. `Submission failed. Draft saved.` not a paragraph. See `docs/23-ui-copy.md`.

**Charts: run the palette validator, never eyeball colour.** Four categorical slots in light, three in dark — green, amber and red are reserved for status. More series means facet or fold into Other, never a new hue. See `docs/21-data-visualization.md`.

**Git: branch → PR → `staging` → `main`.** Never commit directly to a protected branch. See `docs/20-git-workflow.md`.

## Guardrails that are product requirements, not style

- No clinical advice. Reference product information and official leaflet links only. Never map symptom to drug.
- OCR extracts; a registered pharmacist authorizes. Dispensing attaches a council registration number.
- Never label an unverified figure "net profit" — use "estimated operating result".
- The Assistant never silently performs an action that moves stock, money, or a regulated record.

## Conventions

- Python: `ruff` + `black`, type hints on service functions, tests with `pytest`.
- TypeScript: `strict: true`, no `any`, `eslint` + `prettier`.
- Migrations are reviewed like code. Never edit an applied migration.
- Domain logic lives in `services.py` per app, not in views or serializers.
- Tests for anything touching the ledger, FEFO, UoM conversion, money, or tax are mandatory.

## Currency, time, language

- Currency RWF, integer minor units, `Money` value object.
- Timezone `Africa/Kigali`; store UTC, render local.
- Language: **English only**. No localization layer, no translation keys, no i18n library.
