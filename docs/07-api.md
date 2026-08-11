# 07 — API

REST over HTTPS, JSON, versioned by URL prefix. Django REST Framework.

Base: `/api/v1/`

---

## Conventions

**Resources are plural nouns.** `/api/v1/products/`, `/api/v1/stock-movements/`.

**Actions that are not CRUD are sub-resources**, not verbs in the path:

```
POST /api/v1/purchase-orders/{id}/approve/
POST /api/v1/sales/{id}/complete/
POST /api/v1/goods-receipts/{id}/post/
POST /api/v1/batches/{id}/quarantine/
```

**Identifiers.** UUIDv7 primary keys in the path. Human-facing numbers (`SAL-2026-00982`) are searchable fields, never path identifiers.

**Tenancy is implicit.** The organization is resolved from the token. There is no `organization_id` query parameter, and supplying one is ignored. Cross-organization reads happen through explicitly modelled sharing relations with their own endpoints.

**Casing.** `snake_case` in JSON, matching the backend. The frontend client does not transform it.

---

## Authentication

```http
POST /api/v1/auth/token/          → { access, refresh }
POST /api/v1/auth/token/refresh/  → { access, refresh }   # rotates
POST /api/v1/auth/logout/
GET  /api/v1/auth/me/             → user, organizations, active org, permissions
```

`Authorization: Bearer <access>`. Access tokens are short-lived; refresh rotates and the old refresh is revoked.

A user may belong to several organizations. The active one is selected explicitly and carried in the token:

```http
POST /api/v1/auth/switch-organization/   { "organization_id": "..." }
```

---

## Money

Money is always an object, never a bare number. There is no float anywhere in the API.

```json
{ "amount": 2800000, "currency": "RWF", "display": "RWF 28,000.00" }
```

`amount` is **minor units**. Clients must not do arithmetic on `display`.

## Quantity

Quantity is always an object carrying its unit of measure.

```json
{
  "value": 6,
  "uom": { "code": "UNIT", "name": "Capsule" },
  "base_value": 6
}
```

`base_value` is included on responses so clients never need to convert. Requests may send `value` + `uom` and the server converts.

---

## Pagination

Cursor-based. Offset pagination is not offered on ledger or transaction endpoints, where rows are inserted constantly.

```json
{
  "results": [...],
  "next": "https://.../products/?cursor=cD0yMDI2LTA4LTEx",
  "previous": null,
  "count": 1284
}
```

`?page_size=` up to 200. `count` is omitted on very large ledger queries where it is expensive; clients must not depend on it being present.

---

## Filtering, search, ordering

```
GET /api/v1/products/?search=amox&product_type=MEDICINE&legal_status=POM
GET /api/v1/batches/?expiry_before=2026-12-31&status=AVAILABLE&ordering=expiry_date
GET /api/v1/stock-movements/?batch={id}&occurred_after=2026-08-01
```

`search` is a single full-text parameter per resource. Field filters are explicit and whitelisted — no arbitrary ORM traversal from query parameters.

---

## Errors

One shape everywhere. Never a raw exception, never an HTML error page.

```json
{
  "error": {
    "code": "prescription_required",
    "message": "This sale contains a prescription-only product.",
    "detail": "Amoxicillin 500mg is POM. Attach a verified prescription to continue.",
    "field": null,
    "meta": { "sale_line_id": "...", "product_id": "..." }
  }
}
```

`message` is safe to show a user. `detail` adds context. `code` is stable and is what clients branch on — never parse `message`.

Validation errors carry a list:

```json
{ "error": { "code": "validation_error", "message": "...", "errors": [
  { "field": "expiry_date", "code": "past_date", "message": "Expiry date cannot be in the past." }
]}}
```

| Status | Used for |
|---|---|
| 400 | Validation failure |
| 401 | Missing or invalid token |
| 403 | Authenticated but not permitted, including licence-derived refusals |
| 404 | Not found, **or** not visible to this tenant (never 403 — do not confirm existence) |
| 409 | State conflict — already approved, already posted, stale version |
| 422 | Domain rule violation — insufficient stock, prescription required, expired registration |
| 429 | Rate limited |
| 503 | Downstream unavailable — fiscal, payment provider |

The 404-for-cross-tenant rule matters: returning 403 tells an attacker the record exists in another organization.

---

## Idempotency

Any endpoint that creates a financial or stock effect **requires** an idempotency key.

```http
POST /api/v1/sales/
Idempotency-Key: 018f3c2a-7b1e-7c3d-9e4f-1a2b3c4d5e6f
```

The server stores key → response for 24 hours. A repeat returns the original response without re-executing. This is what makes offline sync safe: the agent may retry indefinitely without creating duplicate sales.

Required on: sales, payments, goods receipts, transfers, adjustments, claim submissions.

---

## Concurrency

Mutable resources carry a `version` integer. Updates send `If-Match: <version>`; a mismatch returns 409 with both versions in `meta`. The ledger needs none of this — it is append-only.

---

## Core endpoints

### Catalog
```
GET    /products/                     list, search, filter
POST   /products/
GET    /products/{id}/
PATCH  /products/{id}/
GET    /products/{id}/batches/
GET    /products/{id}/suppliers/      vendor listings
GET    /products/{id}/documents/
GET    /product-types/{id}/attributes/
```

### Inventory
```
GET    /stock/                        balances by product/location/status
GET    /stock/{product_id}/batches/
GET    /stock-movements/              the ledger, read-only
POST   /stock-adjustments/
POST   /stock-transfers/
POST   /stock-transfers/{id}/dispatch/
POST   /stock-transfers/{id}/receive/
POST   /batches/{id}/quarantine/
POST   /batches/{id}/release/
GET    /expiry/                       banded expiry exposure
POST   /stock-takes/{id}/post/
```

`stock-movements` has no `POST`. Movements are created only as a side effect of a domain action, which is enforced at the service layer, not by convention.

### Allocation
```
POST   /allocations/preview/
```
Returns the FEFO batch selection for a proposed issue without committing it. The POS calls this to show which batch will be used before completion.

### Commerce
```
GET    /listings/                     marketplace
POST   /requisitions/
POST   /rfqs/                         broadcast
POST   /quotations/{id}/accept/
POST   /purchase-orders/
POST   /purchase-orders/{id}/approve/
POST   /goods-receipts/
POST   /goods-receipts/{id}/post/     creates batches + movements
```

### Imports
```
POST   /import-requests/
POST   /import-requests/{id}/commit/
GET    /consolidations/               importer demand board
POST   /consolidations/
POST   /consolidations/{id}/allocate/ applies allocation policy on arrival
GET    /shipments/{id}/temperature/
```

### Sales
```
POST   /sales/                        Idempotency-Key required
POST   /sales/{id}/lines/
POST   /sales/{id}/prescription/
POST   /sales/{id}/payments/
POST   /sales/{id}/complete/
POST   /sales/{id}/void/
GET    /shifts/current/
POST   /shifts/{id}/close/            X/Z reports
```

### Insurance
```
POST   /insurance/eligibility/
POST   /insurance/coverage-preview/
POST   /claims/
POST   /claims/{id}/submit/
```

### Fiscal
```
POST   /fiscal/submit/{sale_id}/
GET    /fiscal/exceptions/
POST   /fiscal/exceptions/{id}/retry/
```

### Compliance
```
GET    /compliance/dashboard/
GET    /licences/  /pharmacist-registrations/
POST   /recalls/  /recalls/{id}/actions/
POST   /disposals/
GET    /controlled-register/          statutory register export
```

### Documents
```
GET    /documents/{id}/               metadata
GET    /documents/{id}/preview/       HTML — same template as the PDF
GET    /documents/{id}/pdf/           rendered PDF
POST   /documents/{id}/reissue/       new version, supersession recorded
```

---

## Webhooks and callbacks

Inbound, from providers:

```
POST /api/v1/callbacks/payments/{provider}/
POST /api/v1/callbacks/fiscal/
```

Signature-verified, idempotent by provider reference, and they always return 200 quickly — processing happens on a queue. A payment callback that times out must never cause the provider to think the notification failed.

---

## Agent sync API

Used only by site agents, authenticated with per-site credentials.

```
GET  /api/v1/agent/bootstrap/        catalogue + balances snapshot for the site
GET  /api/v1/agent/changes/?since=   incremental
POST /api/v1/agent/sync/             batch of queued local operations
```

`POST /agent/sync/` accepts an array of operations, each with its own idempotency key, and returns per-operation results. Partial success is normal and expected: some operations apply, others conflict, and the response says which — the agent does not retry the whole batch.

---

## Rate limits

| Scope | Limit |
|---|---|
| Auth endpoints | 10/min per IP |
| Standard authenticated | 600/min per user |
| Agent sync | 120/min per site |
| Assistant | 30/min per user |
| Document rendering | 60/min per organization |

`429` responses carry `Retry-After`.

---

## Versioning

`/api/v1/` is stable. Additive changes — new fields, new endpoints, new enum values — ship without a version bump, so **clients must ignore unknown fields and handle unknown enum values gracefully**. Breaking changes create `/api/v2/` with both served during a published deprecation window.

---

## Documentation

OpenAPI 3.1 generated from serializers via `drf-spectacular`, served at `/api/v1/schema/` with Swagger UI at `/api/v1/docs/`. The schema is generated in CI and a diff against the committed schema fails the build — so an unintended contract change cannot merge silently.
