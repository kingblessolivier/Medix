# 08 — Development

Everything needed to go from a clean machine to a running Medix, and the conventions that keep the codebase coherent once you are there.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 | 3.13+ not yet validated against all dependencies |
| Node | 22 LTS or 24 | |
| PostgreSQL | 16 | 15 works; 16 is what CI runs |
| Redis | 7 | Cache, Celery broker |
| Docker + Compose | current | Easiest path for services |
| Git | 2.40+ | |

---

## Setup

```bash
git clone <repo> Medix
cd Medix
make setup
```

`make setup` runs:

1. `docker compose up -d postgres redis` — services
2. Creates the Python virtualenv and installs `backend/requirements/dev.txt`
3. `python manage.py migrate`
4. `python manage.py seed_demo` — reference data plus a demo retail pharmacy, wholesale pharmacy and importer
5. `npm ci` in `frontend/`

Then:

```bash
make dev
```

Runs backend on `:8000`, frontend on `:5173`, Celery worker, and the mock VSDC on `:8081`.

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API | http://localhost:8000/api/v1/ |
| API docs | http://localhost:8000/api/v1/docs/ |
| Django admin | http://localhost:8000/admin/ |
| Mock VSDC | http://localhost:8081/ |

Demo logins are printed by `seed_demo`.

### Environment

Copy `.env.example` to `.env`. Never commit `.env`.

```bash
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=1
DATABASE_URL=postgres://medix:medix@localhost:5432/medix
REDIS_URL=redis://localhost:6379/0
FISCAL_BACKEND=mock          # mock | vsdc
PAYMENTS_BACKEND=mock        # mock | momo | airtel
TIME_ZONE=Africa/Kigali
```

`FISCAL_BACKEND=mock` and `PAYMENTS_BACKEND=mock` are the default for local work. **Never point a development environment at RRA production.** Use the RRA test VSDC in staging only.

---

## Repository layout

```
Medix/
├── backend/
│   ├── config/            settings/{base,dev,staging,prod}.py, urls, celery
│   ├── core/              tenancy, audit, money, sequences, permissions
│   ├── catalog/  inventory/  commerce/  imports/  sales/
│   ├── insurance/  fiscal/  compliance/  documents/  analytics/
│   ├── requirements/      base.txt, dev.txt, prod.txt
│   └── manage.py
├── frontend/
│   ├── src/design/        tokens.css, theme.ts
│   ├── src/components/    ui, navigation, data, transaction, pharma
│   ├── src/modules/       mirrors backend apps
│   ├── src/portals/       pharmacy, owner, wholesale, admin
│   └── src/lib/           api, auth, money, uom, offline
├── agent/                 local site agent
├── docs/
├── docker-compose.yml
└── Makefile
```

### Anatomy of a backend app

```
inventory/
├── models.py        persistence + invariants only
├── services.py      ALL business rules. The transaction boundary
├── selectors.py     read queries used by more than one place
├── serializers.py   API shape
├── views.py         authorize → deserialize → call service → serialize
├── urls.py
├── admin.py
├── tasks.py         Celery
├── migrations/
└── tests/
    ├── test_services.py    the important ones
    ├── test_api.py
    └── test_models.py
```

---

## Conventions

### Python

- `ruff` for lint and import order, `black` for formatting, line length 100.
- Type hints on every service function signature. `mypy` runs in CI, non-blocking initially, blocking once coverage of `services.py` is complete.
- Docstrings on service functions state **what invariant they preserve**, not what the code does.

### The layering rule

```
views.py  → authorize, deserialize, call a service, serialize. No business logic.
services.py → the transaction boundary. All rules live here.
models.py → fields, constraints, `clean()`. Never orchestration.
```

A view containing an `if` about domain state is a review failure. So is a model method that touches three other models.

### Naming

- Services are verbs: `post_movement`, `allocate_fefo`, `complete_sale`, `apportion_landed_cost`.
- Selectors are nouns: `expiring_batches`, `stock_balance_for`.
- Booleans read as assertions: `is_cold_chain`, `requires_prescription`.
- Money variables always end `_amount` or are typed `Money`. Quantities always carry a UoM.

### Migrations

- Reviewed like code.
- Never edit an applied migration — write a new one.
- Data migrations are separate from schema migrations.
- Every migration must be reversible or explicitly documented as not.
- CI fails on missing migrations (`makemigrations --check --dry-run`).

### TypeScript

- `strict: true`. `any` is banned; `unknown` plus narrowing where a type is genuinely open.
- Components are function components with typed props. No default exports except route modules.
- API types are **generated from the OpenAPI schema**, never hand-written.
- `eslint` + `prettier` on commit.

### Frontend structure rules

- **Tokens before components, components before screens.** A screen that introduces a new colour, size, or spacing value is a review failure — extend the token set deliberately or reuse.
- Every data list uses `DataTable`. Do not hand-roll `<table>`.
- Every form field uses the `ui/` primitives. Do not style a bare `<input>`.
- Icons come from Lucide, sized 16–18, stroke 1.75–2. No other icon source.

---

## Git workflow

Trunk-based with short-lived branches.

```
main                    always deployable
feat/inventory-fefo     short-lived, squash-merged
fix/pos-pending-payment
chore/upgrade-drf
```

### Commits

Conventional Commits. The changelog is generated from them, so they matter.

```
feat(inventory): FEFO allocation with logged override
fix(sales): keep sale pending when momo callback is late
refactor(core): extract sequence allocation into a service
docs(compliance): record narcotics register requirement
test(imports): landed cost apportionment sums to total
```

Scope is the app or frontend module.

### Pull requests

Must state: what changed, why, how it was verified, and anything a reviewer should look at closely.

**Required before merge**

- [ ] Tests pass; new logic has tests
- [ ] Anything touching ledger, FEFO, UoM, money or tax has explicit tests
- [ ] No literal colour values in frontend code
- [ ] No `Model.objects` in a view — `tenant_objects` only
- [ ] No direct quantity mutation
- [ ] Migrations included and reversible
- [ ] UI copy within the length limits in docs/23-ui-copy.md
- [ ] Docs updated when behaviour or contract changed
- [ ] `CHANGELOG.md` updated under Unreleased for user-visible change

---

## Testing

Full strategy in [15-testing.md](15-testing.md). The short version:

```bash
make test              # everything
make test-backend      # pytest
make test-frontend     # vitest
make test-e2e          # playwright
```

**Mandatory test coverage.** These are not negotiable, because a silent failure in any of them corrupts data rather than throwing:

1. Stock ledger — movements sum to balances; replay reproduces state
2. FEFO — never picks a later batch when an earlier one has stock
3. UoM — conversions round-trip; no fractional base units
4. Money — no float anywhere; currency mismatch raises
5. Tax — per-line, mixed baskets, effective-dated rules
6. Tenancy — a cross-tenant access suite that runs against every endpoint
7. Idempotency — repeated POST with the same key does not double-apply
8. Prescription gating — POM cannot complete without verification
9. Landed cost — apportionment sums to the total, to the minor unit
10. Offline sync — replayed batches apply exactly once

Use factories (`factory_boy`), not fixtures. Freeze time with `time_machine` for anything expiry-related.

---

## Working with the local agent

```bash
make agent
```

Runs the agent against the local backend with a mock VSDC. To exercise offline behaviour:

```bash
make agent-offline     # blocks the agent's egress to the API
```

Complete sales, then restore connectivity and confirm they sync exactly once. **Any change to POS or the sync API must be tested this way** — the offline path is where duplicate-sale bugs live.

---

## Language

**English only.** No `gettext`, no `react-i18next`, no translation keys.

Strings live inline where they are used. Review them against the length limits in [23-ui-copy.md](23-ui-copy.md) — buttons and labels 3 words, page descriptions 8, errors 12.

---

## Adding a module

The order is fixed, and following it is what keeps the product coherent.

1. **Model the domain** — entities, invariants, migrations. Write model tests.
2. **Write services** — the rules, the transaction boundary. Write service tests. Most of the work belongs here.
3. **Expose the API** — serializers, views, permissions. Write API tests including cross-tenant.
4. **Regenerate frontend types** from the OpenAPI schema.
5. **Compose the screen from existing components.** If a genuinely new primitive is needed, add it to `components/` with the rest of the design system — never inline into a screen.
6. **Documents**, if the module produces any — template extending `base_document.html`.
7. **Update docs** — module spec, API, changelog.

If step 5 requires a new colour or size, stop and revisit the token set deliberately. That is the moment inconsistency enters a design system.

---

## Common commands

```bash
make dev                 backend + frontend + worker + mock VSDC
make migrate             apply migrations
make makemigrations      generate
make shell               Django shell_plus
make seed_demo           reset demo data
make lint                ruff + eslint
make fmt                 black + prettier
make types               mypy + tsc
make schema              regenerate OpenAPI + frontend types
make rebuild-balances    rebuild StockBalance from the ledger
```

`make rebuild-balances` exists because the ledger is authoritative and projections are disposable. If a balance ever looks wrong, rebuild and compare — do not patch the projection.

---

## Troubleshooting

**Balances disagree with the ledger.** Run `make rebuild-balances`. If they still disagree, a code path is mutating a projection directly — that is the bug.

**Sales duplicated after reconnect.** The idempotency key was not sent or not persisted by the agent. Check the outbound queue, not the server.

**Fiscal submission stuck pending.** Confirm the mock or real VSDC is reachable from the agent host, not from the cloud. This is the most common environment mistake.

**Frontend types out of date.** `make schema`. Never edit generated types by hand.
