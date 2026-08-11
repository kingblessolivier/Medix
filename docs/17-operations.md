# 17 — Operations

Running Medix in production. Written as runbooks — each section states when to use it, what access is needed, the steps, how to roll back, and when to escalate.

The distinguishing operational fact: **part of the system runs in pharmacies we do not control**, on machines we cannot reach, that must keep trading when we are unreachable.

---

## Environments

| Environment | Purpose | Fiscal | Payments | Data |
|---|---|---|---|---|
| Local | Development | Mock VSDC | Mock | Seeded |
| CI | Automated tests | Mock | Mock | Ephemeral |
| Staging | Pre-release verification | **RRA test VSDC** | Provider sandbox | Anonymized |
| Production | Live | RRA production VSDC | Live | Real |

**Never point a non-production environment at RRA production or at live payment providers.** Configuration guards this, but the discipline matters more than the guard.

Production region is constrained by data residency — see [V2 in 11-risks.md](11-risks.md). **Do not provision production infrastructure until that is answered.**

---

## Deployment

### Cloud

Blue/green. Migrations run before the new version takes traffic.

```bash
make deploy ENV=production VERSION=1.4.0
```

1. Build and tag images
2. Run migrations against production (**forward-compatible only** — see below)
3. Deploy to the idle colour
4. Health check: API, database, Redis, Celery, object storage
5. Shift traffic
6. Hold the previous colour for 30 minutes

**Forward-compatible migrations are mandatory** because old and new code run simultaneously during a shift. Column drops and renames are split across two releases: release N stops using the column, release N+1 drops it.

### Rollback

```bash
make rollback ENV=production
```

Shifts traffic back to the held colour. **Migrations are not rolled back** — this is why they must be forward-compatible. If a migration itself is the problem, roll forward with a fix; do not reverse it against live data.

### Site agents

Agents version independently and must tolerate a cloud **one minor version ahead**.

- Staged rollout: internal sites → 5% → 25% → all
- **Never during trading hours.** Agent updates apply on next restart, and a pharmacy chooses when that is
- An agent that fails to start rolls back to the previous version automatically and reports it
- A site may defer an update for up to one minor version; beyond that, sync warns and then requires an upgrade

---

## Monitoring

### Signals that predict a bad day

| Metric | Alert threshold |
|---|---|
| Fiscal submission success rate | <98% over 15 min |
| Fiscal exception queue depth | >20, or any item older than 4 hours |
| Agent sync lag, p95 | >15 min |
| Agents not seen | >2 hours during trading hours |
| Payment callbacks pending | >5% of sales, or any older than 30 min |
| Ledger vs projection divergence | Any |
| Cross-tenant access attempt reaching a service | Any |
| API error rate | >1% over 5 min |
| Claim submission failures | >5% |

The first four are the ones that matter. Everything else is ordinary web-application monitoring; those four are specific to this system and are how you learn a pharmacy cannot trade.

### Dashboards

**Operations** — request rate, error rate, latency, queue depth, database connections.
**Fiscal** — submissions, acceptances, exceptions by reason, per-site breakdown.
**Sites** — agent health, last seen, version distribution, sync lag, offline sales pending.
**Business** — active sites, transactions per day, consolidated imports in flight.

### Logging

Structured JSON. Every log line carries correlation id, organization id, and — where applicable — site id.

**Never log:** patient identifiers, prescription contents, full payment references, tokens. A log line containing patient data is a data protection incident.

---

## Runbook — fiscal submissions failing at one site

**When:** exception queue growing for a single site.

**Access:** site agent status, fiscal exception queue.

1. Check agent connectivity — last seen, version.
2. Check whether VSDC is reachable **from the agent host**, not from the cloud. This is the most common cause and the most commonly misdiagnosed.
3. Check VSDC status: is it running, activated, is it production or test?
4. Check the exception reason. Common: VSDC not initialized after a machine restart; certificate or activation lapsed; malformed payload after a product configuration change.
5. Fix at the site, then retry the queue: `POST /fiscal/exceptions/{id}/retry/` or bulk retry from the operations console.

**Rollback:** none applicable — sales are already valid, only fiscalization is pending.

**Escalate:** to RRA liaison if VSDC is healthy but rejecting. Do not modify sale records to make submission succeed.

**Do not:** tell the pharmacy to stop selling. Sales remain valid and queue.

---

## Runbook — a site is offline

**When:** agent not seen for more than two hours during trading hours.

1. Confirm it is the site, not us — check other sites in the same region.
2. Contact the pharmacy. Usually power or internet.
3. Confirm they are still trading — **they should be.** Offline POS is the designed behaviour, not a failure.
4. On reconnection, monitor sync: expect a burst of queued sales.
5. Verify exactly-once application — compare local journal count to synced sale count.

**Escalate:** if sync produces duplicates. That is a critical defect; capture the journal and the sync log before doing anything else.

---

## Runbook — ledger and projection diverge

**When:** the divergence alert fires.

**This is serious.** The ledger is authoritative, so divergence means either the projection updater has a bug or something wrote a balance directly.

1. Do **not** patch the projection.
2. Identify the affected products and locations.
3. `make rebuild-balances --organization=<id>` on a replica first; compare.
4. If the rebuild matches expectations, apply to production.
5. Find the cause. A direct balance write is a critical defect — find the code path and add the regression test before closing.

**Escalate:** always. Divergence indicates a bug in the foundational invariant of the system.

---

## Runbook — payment stuck pending

**When:** payment callbacks pending beyond threshold.

1. Check provider status — MTN or Airtel outage.
2. Query provider transaction status directly for a sample.
3. If the provider confirms success, apply the resolution manually via the reconciliation tool, which posts the same idempotent operation the callback would have.
4. If the provider confirms failure, the sale resolves to failed and the pharmacy re-takes payment.
5. If the provider is down: instruct sites to use cash and complete mobile money out of band.

**Never:** mark a payment confirmed without provider confirmation.

---

## Runbook — restoring data

**When:** data loss or corruption.

1. Identify the scope and the last known-good point.
2. Restore to a **new** instance. Never restore over production.
3. Verify: ledger replay reproduces balances; fiscal records intact; document sequences have no gaps.
4. Reconcile the gap between restore point and now from agent journals — sites hold their own sales and can resync.
5. Cut over during a maintenance window.

**The agent journals are the safety net.** Offline-first means each site holds an independent record of its own sales.

---

## Backups

| What | Frequency | Retention | Restore target |
|---|---|---|---|
| PostgreSQL full | Daily | 30 days | <2 hours |
| PostgreSQL WAL | Continuous | 7 days | Point-in-time |
| Object storage (documents) | Continuous replication | Versioned, 90 days | <1 hour |
| Agent journals | Local, per site | 90 days | Site-local |

Backups inherit the **data residency constraint**. Restore is tested monthly — a backup that has never been restored is a hypothesis, not a backup.

---

## Onboarding a site

The most operationally involved routine task.

**Prerequisites:** organization created, premises licence recorded and valid, responsible pharmacist registered, hardware present.

1. **Verify licences.** Retail, wholesale, or both. Capability derives from these.
2. **Register the responsible pharmacist** — council number and expiry.
3. **VSDC provisioning** — apply to RRA, await approval, deploy, initialize, activate. Test before production. This is the long pole; start it first.
4. **Install the agent**, bind per-site credentials, verify VSDC reachability from the agent host.
5. **Hardware** — scanner, thermal printer, label printer, cash drawer.
6. **Opening balances** — import existing stock with batch and expiry. Produces `OPENING` movements. Reconcile against a physical count before going live.
7. **Users and roles.**
8. **Test trading day** — sales in the RRA test environment, then an offline period, then sync.
9. **Go live** — switch fiscal to production, monitor the first full day closely.

**Rollback:** a site can trade on paper and back-enter. Opening balances can be re-imported before go-live but not after trading begins.

---

## Regulatory operations

**Licence expiry.** Alerts at 90, 60, 30 and 7 days. At expiry, capability is revoked automatically — a branch cannot open a POS session on an expired retail licence. This is intended behaviour; operations should ensure it never comes as a surprise.

**Recall.** When Rwanda FDA issues one: create the recall, target product or batch, and every affected location appears with quantity on hand. Track execution to completion. **The ledger answers where every unit went**, including units already sold.

**Inspection.** An inspector may request stock records, the controlled-substances register, temperature logs, or disposal certificates. All are exportable directly. Preparation should be an export, not a project.

**Data subject request.** Access requests produce a complete export. Erasure requests run with legal-hold override — controlled-substance register entries and fiscal records cannot be erased.

---

## Secret rotation

| Secret | Frequency | Notes |
|---|---|---|
| Django secret key | Annually | Invalidates sessions; schedule off-hours |
| Database credentials | Quarterly | Rolling |
| Agent credentials | Annually or on suspicion | Per site, independent |
| Payment provider keys | Per provider policy | Coordinate with provider |
| Object storage keys | Quarterly | |

---

## On-call

| Severity | Definition | Response | Escalate |
|---|---|---|---|
| **P1** | Sites cannot trade; data loss; suspected breach | 15 min | Immediate |
| **P2** | Fiscal failing broadly; sync broken; payments stuck | 1 hour | 2 hours |
| **P3** | Single site affected; degraded feature | Next business day | 1 day |
| **P4** | Cosmetic, minor | Backlog | — |

A single site unable to trade is **P2**, not P1 — offline POS means they can usually still sell. A site unable to sell at all is P1.

Any suspected data breach is P1 regardless of scope, and triggers the incident procedure in [16-security.md](16-security.md).
