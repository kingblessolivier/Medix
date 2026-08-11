# 25 — Request pipeline

How a request travels, what makes it fast, and what stops it being abused.

Speed and protection are the same pipeline. Every layer that blocks an attack also sheds load.

---

## 1 — The path

```
Client
  │
  ▼ TLS 1.3
CDN / edge          static assets, cached responses, geo-block
  │
  ▼
WAF                 OWASP rules, bot signals
  │
  ▼
Load balancer       health checks, connection draining
  │
  ▼
Rate limiter        per IP → per user → per organization
  │
  ▼
Django (ASGI)
  ├── SecurityMiddleware      HSTS, headers
  ├── GZip / Brotli
  ├── CorrelationId           request id into every log line
  ├── Authentication          JWT verify
  ├── Tenant                  resolve org, SET LOCAL medix.org_id
  ├── Audit                   capture actor + intent
  └── View → Serializer → Service → DB
        │
        ├── Redis        cache, sessions, locks, rate counters
        ├── PgBouncer    transaction pooling
        └── Celery       anything that may fail and must retry
```

---

## 2 — Latency budget

| Stage | Budget |
|---|---|
| TLS + edge | 20 ms |
| WAF + LB | 5 ms |
| Auth + tenant | 3 ms |
| View + serialization | 15 ms |
| Database | 40 ms |
| Cache | 2 ms |
| **Total p95** | **< 150 ms** |

| Operation | Target p95 |
|---|---|
| Product search, 50k catalogue | 300 ms |
| FEFO allocation | 50 ms |
| POS line add — local agent | 20 ms |
| Table page | 500 ms |
| Stock balance, 5M movements | 200 ms |
| Document render | 3 s (async) |

**POS is not on this budget.** It talks to the local agent, which never leaves the building. Cloud latency is irrelevant to selling — that is the point of the agent.

---

## 3 — Caching

Four layers, each with an explicit invalidation rule. A cache without one is a bug.

| Layer | Holds | TTL | Invalidated by |
|---|---|---|---|
| CDN | JS, CSS, fonts, images | 1 year, hashed filenames | Deploy |
| Redis — reference | Product types, attribute definitions, UoM, tax rules, categories | 1 h | Write signal |
| Redis — computed | Dashboard aggregates, expiry bands, attention feed | 5 min | Time |
| Query | Materialized views | 15 min | Scheduled refresh |
| Client | TanStack Query | 30 s stale | Mutation invalidates by key |

**Never cached:** stock balances, FEFO results, prices at point of sale, permissions, anything patient-related.

Stock is never cached because a stale balance oversells. FEFO is never cached because it must reflect the batch that exists now.

### Keys are tenant-scoped, always

```python
f"medix:{org_id}:catalog:attrs:{product_type_id}"
```

A cache key without `org_id` is a cross-tenant leak. This is checked in review and by a lint rule.

---

## 4 — Query discipline

The default failure mode of a DRF list endpoint is N+1. It is caught mechanically, not by attention.

```python
# every list endpoint
queryset = (Product.tenant_objects
            .select_related("product_type", "base_uom")
            .prefetch_related("registrations", "listings"))
```

Every list endpoint carries a test:

```python
def test_product_list_query_count():
    with assertNumQueries(4):
        client.get("/api/v1/products/?page_size=100")
```

The number is asserted, not bounded — a change that adds a query fails the build and forces a decision.

### Cursor pagination on high-volume resources

Offset pagination is not offered on the ledger, sales, or audit. `OFFSET 50000` reads and discards 50,000 rows, and rows insert constantly so pages shift under the reader.

```
GET /api/v1/stock-movements/?cursor=cD0yMDI2LTA4LTExVDA5OjE0
```

Cursor encodes `(occurred_at, id)`, matching the index order. Constant cost at any depth.

`count` is omitted where it is expensive; clients must not depend on it.

---

## 5 — Async work

Anything that can fail and must be retried leaves the request.

| Task | Queue | Retry |
|---|---|---|
| Fiscal submission | `fiscal` | Exponential, 24 h, then exception queue |
| Document render | `documents` | 3 attempts |
| Claim submission | `insurance` | Exponential, 12 h |
| Notification / SMS | `notify` | 3 attempts |
| Analytics refresh | `analytics` | Next schedule |
| Agent sync reconciliation | `sync` | 5 attempts |

Separate queues with separate workers, so a slow document render cannot delay a fiscal submission.

**Every task is idempotent.** Retries are normal, not exceptional.

```python
@shared_task(bind=True, max_retries=None, acks_late=True)
def submit_fiscal(self, sale_id, idempotency_key): ...
```

`acks_late=True` — a task lost to a worker crash is redelivered rather than silently dropped.

---

## 6 — Rate limiting

Layered, so a single abusive client is stopped before it reaches application code.

| Scope | Limit | Response |
|---|---|---|
| Auth per IP | 10 / min | 429 + `Retry-After`, lockout after 20 failures |
| Authenticated per user | 600 / min | 429 |
| Per organization | 5,000 / min | 429 |
| Agent sync per site | 120 / min | 429 |
| Assistant per user | 30 / min | 429 |
| Document render per org | 60 / min | Queued |
| Export per user | 10 / hour | 429 |

Sliding window in Redis. Export is limited tightly because bulk export is the exfiltration path that matters in a health system.

---

## 7 — Idempotency

Every endpoint with a financial or stock effect requires a key.

```http
POST /api/v1/sales/
Idempotency-Key: 018f3c2a-7b1e-7c3d-9e4f-1a2b3c4d5e6f
```

```
key present? ──no──▶ 400
       │yes
       ▼
Redis SETNX medix:{org}:idem:{key} → "processing", TTL 24h
       │
   ├── already "processing" ──▶ 409 retry later
   ├── already has a stored response ──▶ replay it, do not execute
   └── acquired ──▶ execute, store response, return
```

Backed by a unique index in Postgres, so a Redis flush cannot cause a double-apply. This is what makes an agent safe to retry indefinitely after a lost connection.

---

## 8 — Protection

### Request integrity

| Control | Implementation |
|---|---|
| TLS | 1.3 only, HSTS preload |
| Headers | CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` |
| CORS | Explicit origin allowlist, no wildcard |
| Body size | 10 MB, 25 MB for document upload |
| Timeout | 30 s request, 10 s DB statement |
| Slowloris | Handled at the load balancer |

### Input

- ORM only. Raw SQL requires review and parameterization.
- Serializer validation before any service call — services trust their inputs because views guarantee them.
- Uploads: type sniffed not trusted, size-limited, metadata stripped, stored outside the web root, served through an authorizing endpoint.
- Outbound calls restricted to an allowlist of provider hosts — no SSRF surface.

### Authorization on every layer

```python
# view — coarse
permission_classes = [IsAuthenticated, HasCapability]

# service — authoritative
def complete_sale(sale, *, actor):
    require(actor, Capability.COMPLETE_SALE, branch=sale.branch)
```

The service check is the real one. A view-only check is bypassable by a Celery task, a management command, or the agent sync endpoint.

Capability derives from role **and** branch licence **and** professional registration — an expired pharmacist registration cannot verify a prescription, enforced in the service.

### The agent is untrusted

It runs on hardware in a pharmacy on a shared network.

- Per-site credentials, revocable independently.
- Sync payloads signed; signature and site binding both verified.
- **The server recomputes everything** — balances, prices, tax, totals. An agent-reported total is a claim, not a fact.
- An implausible sale is routed to an exception queue, not applied.
- Compromise of one agent is contained to one site by construction.

---

## 9 — Degradation

Ranked by what must survive.

| Failure | Behaviour |
|---|---|
| Cloud unreachable | **POS keeps selling.** Agent journals locally, syncs later |
| Redis down | Cache misses to DB; rate limiting fails **closed** on auth, open elsewhere |
| Replica down | Analytics routes to primary, with a load warning |
| Fiscal provider down | Sales complete, fiscal queues, exception surfaces |
| Payment provider down | Payment marked pending, cash offered |
| Primary DB down | Read-only mode for cloud; **POS unaffected** |

The ordering is deliberate: **selling never stops**. Everything else may degrade.

---

## 10 — Observability

Structured JSON, one line per request, always carrying `correlation_id`, `organization_id`, and `site_id` where applicable.

```json
{"ts":"2026-08-11T09:14:22Z","level":"info","correlation_id":"018f3c2a…",
 "organization_id":"…","method":"POST","path":"/api/v1/sales/",
 "status":201,"duration_ms":87,"db_ms":41,"queries":6}
```

**Never logged:** patient identifiers, prescription contents, tokens, full payment references. A log line containing patient data is a data protection incident.

| Alert | Threshold |
|---|---|
| Error rate | >1% / 5 min |
| p95 latency | >500 ms / 5 min |
| Queue depth | >1,000 |
| Fiscal success | <98% / 15 min |
| Agent sync lag p95 | >15 min |
| Agents unseen in trading hours | >2 h |
| Cross-tenant attempt reaching a service | Any |
| Ledger vs projection divergence | Any |

The last two should be permanently zero. Any occurrence is an incident.

---

## 11 — Frontend performance

| Technique | Effect |
|---|---|
| Route-level code splitting | Each portal loads only its own bundle |
| TanStack Query | Dedupe, background refetch, 30 s stale |
| Virtualized rows | Tables over 100 rows render only what is visible |
| Optimistic updates | POS cart responds instantly, reconciles after |
| Debounced search | 250 ms |
| Prefetch on hover | Table row hover warms the drawer query |
| Immutable asset caching | Hashed filenames, 1-year TTL |

Budgets: initial bundle < 200 KB gzipped per portal, LCP < 1.5 s, INP < 200 ms.

---

## 12 — Load targets

| Metric | Year 1 |
|---|---|
| Concurrent users | 2,000 |
| Requests / s sustained | 400 |
| Peak (end of month) | 1,200 |
| Sites syncing concurrently | 500 |
| Sales / day | 30,000 |

Load-tested before each phase release at 3× target. The end-of-month peak matters — that is when every pharmacy closes its books at once.
