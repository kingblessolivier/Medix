# 24 — Database design

PostgreSQL 16. Physical design: schema, keys, indexes, partitioning, constraints, and the query patterns they exist to serve.

[03-data-model.md](03-data-model.md) is the logical model. This is how it lands on disk.

---

## 1 — Sizing target

| Entity | Year 1 | Year 3 | Growth |
|---|---|---|---|
| `stock_movement` | 50M | 400M | Append-only, never deleted |
| `sale` / `sale_line` | 8M / 25M | 60M / 190M | Append-only |
| `audit_event` | 40M | 300M | Append-only |
| `batch` | 2M | 12M | Slow |
| `product` | 100k | 300k | Slow |
| `organization` | 500 | 3,000 | Very slow |

Three tables carry 95% of the volume and all three are append-only. That shapes every decision below.

---

## 2 — Keys

**UUIDv7 primary keys** on every table.

```sql
id uuid PRIMARY KEY DEFAULT uuidv7()
```

| Property | Why it matters here |
|---|---|
| Time-sortable | Index locality equal to a bigserial — no random B-tree page splits |
| Generated client-side | **The offline agent creates rows without a round trip** |
| Non-guessable | No enumeration of another tenant's records |
| Globally unique | Merging offline journals cannot collide |

Sequential integers are rejected because the agent cannot allocate them offline. Random UUIDv4 is rejected because random inserts fragment a 400M-row index.

Human-facing numbers (`SAL-2026-00982`) are a separate indexed column, never the key.

---

## 3 — Tenancy on disk

Every tenant-scoped table carries `organization_id uuid NOT NULL`, and **it is the leading column of every composite index**.

```sql
CREATE INDEX idx_batch_org_product_expiry
  ON batch (organization_id, product_id, expiry_date)
  WHERE status = 'AVAILABLE';
```

Leading `organization_id` means every query is index-bound to one tenant's slice. A missing tenant predicate degrades to a sequential scan, which is slow enough to be noticed in review and in monitoring — the performance characteristic doubles as a leak alarm.

### Row-level security as a backstop

Application filtering via `tenant_objects` is the primary control. RLS is the net beneath it.

```sql
ALTER TABLE stock_movement ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON stock_movement
  USING (organization_id = current_setting('medix.org_id')::uuid);
```

Middleware sets `medix.org_id` per request. A forgotten filter returns zero rows instead of another pharmacy's data.

Cross-tenant reads (regulator, wholesale seeing its own orders) run under a role that bypasses RLS and are confined to explicitly modelled sharing endpoints.

---

## 4 — Partitioning

Three tables partition by range on time. Everything else is a single table.

```sql
CREATE TABLE stock_movement (
    id                 uuid        NOT NULL DEFAULT uuidv7(),
    organization_id    uuid        NOT NULL,
    location_id        uuid        NOT NULL,
    batch_id           uuid        NOT NULL,
    product_id         uuid        NOT NULL,
    kind               movement_kind NOT NULL,
    quantity_base      bigint      NOT NULL,
    balance_after_base bigint      NOT NULL,
    reason             text,
    performed_by_id    uuid        NOT NULL,
    occurred_at        timestamptz NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    idempotency_key    text        NOT NULL,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
```

Monthly partitions, created three months ahead by a scheduled job.

| Benefit | Detail |
|---|---|
| Query pruning | Almost every ledger query is time-bounded |
| Index size | Each partition's index stays in cache |
| Retention | Detach a partition instead of a 50M-row `DELETE` |
| Maintenance | `VACUUM` and `REINDEX` run per partition |

Partitioned: `stock_movement`, `audit_event`, `sale_line`. `sale` stays whole — it is looked up by number and by recent date, and it is an order of magnitude smaller.

**Partitions are never dropped.** Cold partitions move to cheaper storage; a stock ledger must remain replayable from zero.

---

## 5 — Indexes

Indexes exist for a named query. An index without one gets removed.

### Ledger

```sql
-- balance rebuild + batch history
CREATE INDEX idx_sm_org_batch_time
  ON stock_movement (organization_id, batch_id, occurred_at);

-- product movement across locations
CREATE INDEX idx_sm_org_product_time
  ON stock_movement (organization_id, product_id, occurred_at DESC);

-- recall: who received this batch
CREATE INDEX idx_sm_batch_kind
  ON stock_movement (batch_id, kind)
  WHERE kind IN ('SALE','TRANSFER_OUT');

-- offline dedup
CREATE UNIQUE INDEX idx_sm_idem
  ON stock_movement (organization_id, idempotency_key);
```

### FEFO — the hottest query in the system

```sql
CREATE INDEX idx_balance_fefo
  ON stock_balance (organization_id, product_id, location_id, expiry_date)
  INCLUDE (batch_id, quantity_base)
  WHERE status = 'AVAILABLE' AND quantity_base > 0;
```

Partial (only sellable rows) and covering (`INCLUDE` avoids a heap fetch). FEFO runs on every POS line and must be index-only.

```sql
SELECT batch_id, quantity_base
FROM stock_balance
WHERE organization_id = $1 AND product_id = $2 AND location_id = $3
  AND status = 'AVAILABLE' AND quantity_base > 0 AND expiry_date > $4
ORDER BY expiry_date
LIMIT 10;
```

### Expiry

```sql
CREATE INDEX idx_batch_expiry_risk
  ON batch (organization_id, expiry_date)
  WHERE status <> 'DISPOSED';
```

### Search

Trigram, not full-text — users type partial product names and misspellings.

```sql
CREATE EXTENSION pg_trgm;

CREATE INDEX idx_product_name_trgm
  ON product USING gin (name gin_trgm_ops, generic_name gin_trgm_ops);

CREATE INDEX idx_batch_number_trgm
  ON batch USING gin (batch_number gin_trgm_ops);
```

### Dynamic attributes

```sql
CREATE INDEX idx_product_attrs ON product USING gin (attributes jsonb_path_ops);
```

`jsonb_path_ops` — smaller and faster than the default for containment, which is the only operator used.

### Document numbers

```sql
CREATE UNIQUE INDEX idx_doc_number ON document (organization_id, type_id, number);
```

---

## 6 — Constraints

The database enforces what the application must never get wrong.

```sql
-- money is never negative where it cannot be
ALTER TABLE sale_line ADD CONSTRAINT ck_line_total_nonneg
  CHECK (line_total >= 0);

-- quantities are integers in base units, never fractional
ALTER TABLE stock_movement ADD CONSTRAINT ck_qty_nonzero
  CHECK (quantity_base <> 0);

-- expiry must be after manufacture
ALTER TABLE batch ADD CONSTRAINT ck_batch_dates
  CHECK (manufacture_date IS NULL OR expiry_date > manufacture_date);

-- a cold-chain batch cannot sit in an ambient location
-- enforced by trigger — it spans two tables

-- one balance row per (location, batch, status)
ALTER TABLE stock_balance ADD CONSTRAINT uq_balance
  UNIQUE (location_id, batch_id, status);

-- controlled dispensing requires patient address
ALTER TABLE controlled_delivery_entry ADD CONSTRAINT ck_patient_address
  CHECK (length(trim(patient_address)) > 0);
```

### Immutability enforced by grant

```sql
REVOKE UPDATE, DELETE ON stock_movement          FROM medix_app;
REVOKE UPDATE, DELETE ON audit_event             FROM medix_app;
REVOKE UPDATE, DELETE ON controlled_delivery_entry FROM medix_app;
REVOKE UPDATE, DELETE ON fiscal_record           FROM medix_app;
```

Application discipline is good. A revoked grant is better — theft cannot be concealed by editing history even with a shell.

---

## 7 — Money and quantity

```sql
amount_minor  bigint      NOT NULL,
currency      char(3)     NOT NULL DEFAULT 'RWF'
```

`bigint` minor units. **No `float`, `real`, or `double precision` anywhere in the schema** — a CI check greps the migration output for them.

Quantities are `bigint` in base units, always paired with a `uom_id` on any row a user entered.

---

## 8 — Balance projection

`stock_balance` is derived and disposable. Updated in the same transaction as the movement:

```sql
INSERT INTO stock_balance (organization_id, location_id, batch_id, status,
                           product_id, expiry_date, quantity_base)
VALUES (...)
ON CONFLICT (location_id, batch_id, status)
DO UPDATE SET quantity_base = stock_balance.quantity_base + EXCLUDED.quantity_base,
              updated_at = now();
```

Same transaction, so they cannot diverge under normal operation. Divergence means a bug, and the alarm for it is a scheduled reconciliation job:

```sql
SELECT b.batch_id, b.quantity_base AS projected, SUM(m.quantity_base) AS actual
FROM stock_balance b
JOIN stock_movement m USING (batch_id, location_id)
GROUP BY b.batch_id, b.location_id, b.quantity_base
HAVING b.quantity_base <> SUM(m.quantity_base);
```

Expected result: zero rows. Anything else pages someone.

---

## 9 — Sequences

Gap-free per organization, per type, per year. Fiscal and controlled-substance sequences must have no gaps — a gap is an audit finding — so `SERIAL` is unusable (it gaps on rollback).

```sql
CREATE TABLE document_sequence (
    organization_id uuid NOT NULL,
    type_code       text NOT NULL,
    year            int  NOT NULL,
    next_value      bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (organization_id, type_code, year)
);
```

```sql
UPDATE document_sequence
   SET next_value = next_value + 1
 WHERE organization_id = $1 AND type_code = $2 AND year = $3
RETURNING next_value - 1;
```

Row lock held to commit. Serializes per organization per type — acceptable, since a single pharmacy issues at most a few sales per second.

---

## 10 — Concurrency

| Concern | Approach |
|---|---|
| Isolation | `READ COMMITTED` default |
| Stock allocation | `SELECT … FOR UPDATE` on the balance rows FEFO selected |
| Optimistic locking | `version` integer on mutable resources; `If-Match` from the API |
| Sequences | Row lock, above |
| Deadlock avoidance | Locks acquired in a fixed order: organization → location → batch |

Ledger appends take no locks — they are inserts.

---

## 11 — Connections

PgBouncer in **transaction** pooling mode.

```
app pods (60 workers) ──▶ PgBouncer (25 server conns) ──▶ Postgres
```

Django runs `CONN_MAX_AGE=0`; PgBouncer owns pooling. Transaction mode forbids session state — no `SET` outside a transaction, no server-side prepared statements without `prepared_statements=False` on the driver.

`medix.org_id` for RLS is set with `SET LOCAL` inside the transaction, which is transaction-mode safe.

Separate pools: `web` (25), `worker` (15), `readonly` (10, routed to a replica).

---

## 12 — Read replicas

| Traffic | Target |
|---|---|
| Writes, POS reads, FEFO | Primary |
| Analytics, reports, exports | Replica |
| Document rendering | Replica |
| Agent bootstrap snapshot | Replica |

FEFO and POS never hit a replica — replication lag would allow overselling. Django routes by database alias, chosen explicitly, never inferred.

---

## 13 — Analytics

Executive queries aggregate millions of rows and cannot run live on the primary.

**Materialized views**, refreshed on a schedule, concurrently:

```sql
CREATE MATERIALIZED VIEW mv_margin_by_category AS
SELECT organization_id, branch_id, category_id,
       date_trunc('day', s.occurred_at) AS day,
       SUM(sl.line_total) AS revenue,
       SUM(sl.quantity_base * b.unit_cost_base) AS cogs
FROM sale_line sl
JOIN sale s ON s.id = sl.sale_id
JOIN batch b ON b.id = sl.batch_id
JOIN product p ON p.id = sl.product_id
WHERE s.status = 'COMPLETED'
GROUP BY 1,2,3,4;

CREATE UNIQUE INDEX ON mv_margin_by_category (organization_id, branch_id, category_id, day);
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_margin_by_category;
```

Refresh every 15 minutes off-peak, hourly during trading. The executive portal states its data age — `As of 14:15` — rather than implying real time.

**COGS comes from `batch.unit_cost_base`**, which is landed cost. That single join is why margin is real rather than estimated.

---

## 14 — Migrations

- Django migrations, reviewed like code, **forward-compatible only** — old and new code run together during a blue/green shift.
- Column drops and renames split across two releases.
- `CREATE INDEX CONCURRENTLY` for anything on a large table, in its own migration with `atomic = False`.
- Never `ALTER TABLE … ADD COLUMN … DEFAULT <volatile>` on a large table.
- A migration that would lock a hot table for more than a second is rejected in review.
- Never edit an applied migration.

---

## 15 — Backup and retention

| What | Method | Retention |
|---|---|---|
| Full | `pg_basebackup`, daily | 30 days |
| WAL | Continuous archive | 7 days, PITR |
| Logical | `pg_dump` per organization, weekly | 90 days — supports export-on-exit |

Restore tested monthly. Backups inherit the **data residency constraint** — see [V2 in 11-risks.md](11-risks.md).

Retention deletion is partition detachment, never `DELETE`. Ledger, fiscal and controlled-substance partitions are **never** detached.

---

## 16 — Extensions

| Extension | Use |
|---|---|
| `pg_trgm` | Fuzzy product and batch search |
| `btree_gin` | Composite GIN with scalar columns |
| `pg_stat_statements` | Query performance monitoring |
| `pgcrypto` | Field-level encryption where required |

Not used: PostGIS (no spatial requirement), TimescaleDB (native partitioning is sufficient).

---

## 17 — Monitoring

| Signal | Threshold |
|---|---|
| Query p95 | >200 ms |
| Sequential scans on partitioned tables | Any |
| Replication lag | >5 s |
| Connection pool saturation | >80% |
| Bloat on hot tables | >20% |
| Ledger vs projection divergence | Any |
| Longest transaction | >30 s |

`pg_stat_statements` reviewed weekly. Any query in the top 20 by total time without a matching index gets one or gets rewritten.
