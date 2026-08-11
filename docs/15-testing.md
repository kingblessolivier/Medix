# 15 — Testing

In most systems a bug throws an error. In this one, the worst bugs are silent: stock drifts, margin is wrong by a few percent, a duplicate sale appears after a reconnect, tax is misclassified on one line. Nothing crashes and nobody notices for months.

**Testing here is weighted toward the paths where failure is silent, not toward coverage percentage.**

---

## Shape

```
        ╱  E2E — Playwright, ~30 flows
       ╱    critical journeys only
      ╱─────────────────────────────
     ╱   Integration — API + DB
    ╱      every endpoint, every permission
   ╱───────────────────────────────────────
  ╱   Unit — services, heavy
 ╱       the ledger, FEFO, UoM, money, tax
╱─────────────────────────────────────────────
```

Most of the value sits in the service layer, which is why business rules live there and not in views.

---

## The mandatory ten

These are not negotiable. A PR touching any of them without a corresponding test does not merge.

### 1. Ledger integrity

```python
def test_movements_sum_to_balance(): ...
def test_balance_after_matches_running_sum(): ...
def test_replay_from_zero_reproduces_state(): ...
def test_no_code_path_mutates_quantity(): ...   # AST scan for forbidden patterns
```

The replay test is the one that matters most: rebuild every balance from movements and assert equality with the projection. It catches an entire class of bug that no unit test of a single service will.

### 2. FEFO

```python
def test_picks_nearest_expiry_first(): ...
def test_skips_expired_batches(): ...
def test_skips_non_available_status(): ...
def test_spans_multiple_batches_when_needed(): ...
def test_raises_rather_than_partially_allocating(): ...
def test_manual_override_requires_reason_and_is_logged(): ...
```

### 3. Unit of measure

```python
def test_conversion_round_trips_exactly(): ...
def test_never_produces_fractional_base_units(): ...
def test_purchase_in_packs_dispense_in_units_reconciles(): ...
def test_rejects_bare_integer_quantity(): ...
```

Property-based testing (Hypothesis) is worth it here — generate random UoM chains and quantities and assert the invariants hold.

### 4. Money

```python
def test_no_float_anywhere_in_money_path(): ...
def test_currency_mismatch_raises(): ...
def test_minor_unit_arithmetic_is_exact(): ...
def test_apportionment_sums_to_total_exactly(): ...
```

### 5. Tax

```python
def test_mixed_basket_computes_per_line(): ...
def test_exempt_is_not_zero_rated(): ...
def test_uses_rules_effective_on_sale_date(): ...   # not today's rules
def test_irrecoverable_input_vat_reflected_in_cost(): ...
```

The effective-date test is the one people forget. Freeze time, write a sale dated last year, assert last year's rules applied.

### 6. Tenancy

A generated suite that runs against **every registered endpoint**:

```python
@pytest.mark.parametrize("endpoint", all_registered_endpoints())
def test_cross_tenant_access_returns_404(endpoint): ...
def test_no_view_uses_unfiltered_manager(): ...     # AST scan
```

Returning 404 rather than 403 is asserted explicitly — a 403 confirms the record exists in another organization.

### 7. Idempotency

```python
def test_repeated_key_returns_original_response(): ...
def test_repeated_key_does_not_double_apply(): ...
def test_concurrent_same_key_applies_once(): ...
```

The concurrency case needs real threads, not sequential calls.

### 8. Prescription gating

```python
def test_pom_line_blocks_completion_without_prescription(): ...
def test_expired_pharmacist_registration_cannot_verify(): ...
def test_ocr_extract_alone_does_not_authorize(): ...
def test_controlled_line_creates_exactly_one_register_entry(): ...
def test_controlled_entry_requires_patient_address(): ...
```

### 9. Landed cost

```python
def test_components_apportion_to_exact_total(): ...
def test_each_participant_batch_gets_correct_unit_cost(): ...
def test_partial_arrival_allocates_per_policy(): ...
def test_one_consignment_produces_separate_grns(): ...
```

Rounding is the trap: apportioning 320,000 across three participants must not lose or invent a franc.

### 10. Offline sync

```python
def test_queued_batch_applies_exactly_once(): ...
def test_partial_batch_success_reports_per_operation(): ...
def test_replay_after_timeout_does_not_duplicate(): ...
def test_agent_one_minor_version_behind_still_syncs(): ...
```

---

## Backend

**pytest** with `pytest-django`. Factories via `factory_boy` — never fixtures, which drift. Time frozen with `time_machine` for anything expiry-related.

```python
# tests/factories.py
class BatchFactory(DjangoModelFactory):
    expiry_date = factory.LazyFunction(lambda: date.today() + timedelta(days=365))
```

**Database.** Real PostgreSQL, never SQLite. Constraints, partial indexes and JSONB behaviour all differ, and the data model uses all three.

**Service tests are the priority.** A service test sets up state, calls one service function, and asserts both the return value and the invariant it was supposed to preserve.

```python
def test_complete_sale_posts_movements_and_leaves_balance_consistent():
    sale = given_a_sale_with(product, qty=Quantity(6, UNIT))
    complete_sale(sale, idempotency_key=uuid7())
    assert_ledger_consistent(product, location)
    assert sale.status == SaleStatus.COMPLETED
```

**API tests** cover the contract: status codes, error shapes, permissions, pagination. Every endpoint gets an unauthenticated test, an unauthorized test, and a cross-tenant test.

---

## Frontend

**Vitest** plus **React Testing Library**. Test behaviour, not implementation — query by role and label, never by class name.

```tsx
it("blocks completion when a POM line has no prescription", async () => {
  renderPOS({ lines: [pomLine] });
  await user.click(screen.getByRole("button", { name: /complete sale/i }));
  expect(screen.getByText(/prescription required/i)).toBeVisible();
});
```

**Design system tests.** Automated checks that the rules in [04-design-system.md](04-design-system.md) hold:

```
- no literal hex colours in src/ outside tokens.css
- no font-size outside the declared scale
- no icon import outside lucide-react
- every interactive element has an accessible name
```

These run as lint rules, so violations fail before review rather than during it.

**Visual regression** on the component library and on every document template, in both themes.

---

## End-to-end

Playwright against a seeded stack. ~30 flows, chosen because they cross module boundaries where unit tests cannot reach.

| Flow | Asserts |
|---|---|
| Receive stock → sell → day end | Ledger, fiscal, reconciliation all agree |
| POM sale without prescription | Blocked with an explanatory message |
| Controlled sale | Register entry created with patient address |
| Partial pack dispensing | Six units from a pack of 100 reconcile |
| **Offline trading day** | Sales journal locally, sync once, no duplicates |
| Import request → consolidation → short arrival | Allocation per policy, per-participant GRNs |
| Recall | Every unit traced to location or sale |
| Cold chain excursion | Batches auto-quarantined |
| Cross-tenant attempt | 404, nothing leaked |
| Document preview vs PDF | Byte-level content parity |

### The offline test is the one that matters

```bash
make test-e2e-offline
```

Starts the agent, cuts its egress, trades a full day, restores connectivity, and asserts exactly-once application. **Any change to POS, the agent, or the sync API must run this.** Duplicate-sale bugs are silent, corrupt both stock and revenue, and cannot be cleanly unwound.

---

## Performance

Not exhaustive — a few guards on the paths that will actually degrade.

| Check | Budget |
|---|---|
| Product search, 50k products | <300 ms p95 |
| Stock balance query, 5M movements | <200 ms p95 |
| POS line add, local | <100 ms |
| Table page render | <500 ms p95 |
| Document render | <3 s |
| Agent sync, 500 queued ops | <30 s |

Plus an `assertNumQueries` guard on list endpoints, because N+1 regressions arrive quietly with a serializer change.

---

## Security testing

- Cross-tenant suite (above) — the highest-value security test in the system
- Authorization matrix: every role against every endpoint
- Patient data access produces an `AuditEvent` — asserted, including on reads
- Rate limits enforced
- No secrets in the repository (CI scan)
- Dependency audit in CI, failing on high severity

---

## CI

```yaml
on: [push, pull_request]

lint          ruff · black --check · eslint · prettier --check
types         mypy · tsc --noEmit
migrations    makemigrations --check --dry-run
schema        regenerate OpenAPI, fail on uncommitted diff
test-backend  pytest --cov, real Postgres
test-frontend vitest
design-rules  token / icon / type-scale lint
test-e2e      playwright, main + PRs touching POS or agent
security      dependency audit · secret scan
```

Merge requires all green. The schema check exists so an unintended API contract change cannot merge silently.

---

## Coverage

Overall target ≥80%, but the number is not the point. **100% on the mandatory ten** — ledger, FEFO, UoM, money, tax, tenancy, idempotency, prescription gating, landed cost, sync — and coverage there is enforced separately.

Untested code in those paths is a merge blocker regardless of the overall figure.

---

## Manual testing before each release

Automation cannot cover these.

- [ ] Full trading day on real hardware — scanner, thermal printer, cash drawer
- [ ] Offline for a full day, then reconnect
- [ ] Every document printed monochrome on a real printer and checked for legibility
- [ ] UI copy pass — nothing over the length limits in docs/23-ui-copy.md
- [ ] Tablet POS with touch only
- [ ] Both themes on a real screen, not a simulated one
- [ ] Keyboard-only completion of the whole POS flow
