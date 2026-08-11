# 16 — Security and privacy

Medix holds patient records, prescriptions, controlled-substance dispensing records, commercial pricing between competing organizations, and fiscal data. A breach here is a reportable regulatory incident, not an embarrassment.

Two threats dominate and deserve most of the attention: **cross-tenant leakage** and **unlawful handling of patient data**.

---

## Threat model

| Threat | Impact | Primary control |
|---|---|---|
| Cross-tenant data access | Competitor sees pricing and customers; patient data exposed; reportable breach | Row-level tenancy, automated cross-tenant suite, 404 not 403 |
| Unauthorized patient data access | Breach under Law 058/2021 | Capability checks, read auditing, time-boxed support access |
| Privilege escalation | Unauthorized dispensing, price manipulation, fraudulent claims | Capability-based permissions checked in services |
| Credential compromise | Full account takeover | Short access tokens, rotation, rate limiting, MFA for elevated roles |
| Agent compromise | Fraudulent sales, tampered fiscal records | Per-site revocable credentials, signed sync, server-side validation |
| Fiscal tampering | Tax fraud exposure for the pharmacy | Fiscal records immutable; corrections are new documents |
| Ledger tampering | Concealed theft, corrupted margin, broken traceability | Append-only, no update or delete path, audit stream |
| Insider misuse | Data exfiltration, unlawful dispensing | Audit on reads and writes, export limits, anomaly alerting |
| Supply chain | Malicious dependency | Pinned dependencies, CI audit, lockfiles reviewed |

---

## Authentication

- JWT access tokens, short-lived. Refresh tokens rotate; the old refresh is revoked on use, so replay is detectable.
- Password hashing with Argon2.
- **MFA required** for owner, finance, compliance and platform-admin roles. Optional but encouraged for pharmacists.
- Rate limiting: 10/min per IP on auth endpoints, with lockout and alerting on repeated failure.
- Session listing and remote revocation available to the user and to their organization admin.
- Agent credentials are per-site, revocable independently, and never shared between sites.

## Authorization

**Capability-based, not role-string-based.** A permission check never compares a role name.

```python
# ❌
if user.role == "pharmacist": ...

# ✅
require(user, Capability.VERIFY_PRESCRIPTION, branch=branch)
```

Capabilities derive from three sources, all of which must permit the action:

1. The user's assigned role within the organization
2. The **branch's held licences** — a branch without a valid retail licence cannot open a POS session
3. The user's **professional registration** where the action requires one — an expired pharmacist registration cannot verify a prescription

That third source is what makes licence and registration expiry automatically revoke capability, which is the behaviour a regulator expects.

**Checks live in services, not only in views.** A view-only check is bypassable by any other caller — a Celery task, a management command, the agent sync endpoint.

---

## Tenancy isolation

The single highest-value control in the system.

- Every tenant-scoped model inherits `TenantModel`; `tenant_objects` filters by the active organization from request context.
- `Model.objects` is unfiltered and reserved for admin and background work. **Using it in a view is a review failure**, enforced by an AST lint rule.
- Cross-organization visibility exists only through explicitly modelled sharing relations, each with its own endpoint and its own tests.
- **Cross-tenant records return 404, never 403** — a 403 confirms the record exists elsewhere.
- An automated suite runs every registered endpoint against a foreign tenant on every CI run.

---

## Patient data — Law 058/2021

Health data is sensitive personal data. These are legal obligations, not best practices.

| Obligation | Implementation |
|---|---|
| Lawful basis and consent | Captured at patient record creation with purpose and timestamp |
| Controller registration | Tracked per organization in compliance; surfaced on the compliance dashboard |
| Data protection officer | Recorded per organization; absence is a compliance finding |
| Purpose limitation | Analytics runs on aggregates. No patient-level data leaves the clinical path |
| Retention | Policy per data class with scheduled deletion, subject to legal-hold override where other law requires retention |
| Subject access | Export workflow producing a complete, readable record |
| Erasure | Workflow with legal-hold override — a controlled-substance register entry cannot be erased on request |
| Breach notification | Documented incident procedure with a defined clock |
| Demonstrable access control | **Reads of patient data produce an `AuditEvent`**, not only writes |
| Cross-border transfer | Constrains hosting region — see V2 in [11-risks.md](11-risks.md) |

### Practical rules

- Patient identifiers never appear in URLs, query strings, logs, or error messages.
- Support access to patient data is explicit, time-boxed and reason-logged. There is no standing admin view of patient records.
- Backups inherit the residency constraint.
- Exports are rate-limited and audited; bulk patient export requires elevated capability.

---

## The ledger and fiscal records

Both are **append-only by design**, which is a security property as much as a data-modelling one.

- `StockMovement` has no update or delete path. A correction is a compensating movement with a reason, so theft cannot be concealed by editing history.
- Fiscal records are immutable once accepted. A correction is a credit note, never an edit.
- `ControlledDeliveryEntry` is append-only and is excluded from erasure workflows, because statute requires retention.
- Database-level: revoke `UPDATE` and `DELETE` on these tables from the application role in production. Application discipline is good; a database grant is better.

---

## The site agent

The agent runs on hardware we do not control, in a pharmacy, possibly on a shared network.

- Per-site credentials, revocable, rotated on a schedule.
- Sync payloads are signed; the server validates the signature and the site binding.
- **The server never trusts agent-supplied state.** Balances, prices, tax and totals are recomputed server-side. An agent that reports an implausible sale gets it rejected into an exception queue, not applied.
- The local journal is encrypted at rest.
- The agent listens on localhost only. It is not a network service.
- Compromise of one agent is contained to one site by construction.

---

## Application security

- **Injection** — ORM only; raw SQL requires review and parameterization. No string-built queries.
- **XSS** — React escapes by default; `dangerouslySetInnerHTML` is banned outside the document renderer, where input is system-generated.
- **CSRF** — token auth with `SameSite` cookies for the session flows that use them.
- **File upload** — prescription images and documents are type-checked, size-limited, stripped of metadata, stored outside the web root, and served through an authorizing endpoint. Never directly addressable.
- **SSRF** — outbound calls only to configured provider hosts from an allowlist.
- **Secrets** — environment only. CI scans for committed secrets. Rotation documented in [17-operations.md](17-operations.md).
- **Dependencies** — pinned, lockfiles reviewed, CI audit failing on high severity.
- **Headers** — HSTS, CSP, `X-Content-Type-Options`, `Referrer-Policy` on all responses.

---

## Audit

Two layers, deliberately.

**`AuditedModel`** — who created, modified, approved or rejected a record, when, and why. Answers *what is the state of this thing and who put it there*.

**`AuditEvent`** — an append-only stream of actor, action, subject, before, after, IP, user agent, time. Answers *what happened, in order*. Includes reads of patient data.

Retention matches the longest applicable regulatory requirement. Audit records are never deleted by a retention job without an explicit legal review.

---

## Monitoring and response

Alert on:

- Repeated authentication failure, especially across accounts
- Any cross-tenant access attempt reaching a service (should be zero)
- Bulk patient data export
- Fiscal submission failure rate above threshold
- Agent reporting implausible values
- Privilege grants outside business hours
- Ledger and projection divergence

### Incident response

1. **Contain** — revoke credentials, isolate the affected agent or account.
2. **Assess** — reconstruct scope from the audit stream. This is what it exists for.
3. **Notify** — regulator and data subjects per the Law 058/2021 clock, and RRA if fiscal records are implicated.
4. **Remediate** — fix, then add the regression test.
5. **Review** — written post-incident review; controls updated.

The audit stream is what makes step 2 possible in hours instead of weeks. It is a security control, not just a compliance artifact.

---

## Pre-release checklist

- [ ] Cross-tenant suite green across every endpoint
- [ ] Authorization matrix tested for every role
- [ ] No secrets in the repository
- [ ] Dependency audit clean at high severity
- [ ] Patient data reads produce audit events
- [ ] Rate limits enforced and verified
- [ ] `UPDATE`/`DELETE` revoked on ledger and fiscal tables in production
- [ ] Agent credentials rotated and revocation tested
- [ ] Backups encrypted, restore tested, residency confirmed
- [ ] Security headers present on all responses
