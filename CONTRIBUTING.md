# Contributing to Medix

## Read first

Three documents are prerequisites, not background reading. Code written without them tends to violate an invariant that is expensive to unwind.

1. [docs/01-overview.md](docs/01-overview.md) — what the system is and why
2. [docs/02-architecture.md](docs/02-architecture.md) — how it is put together
3. [docs/03-data-model.md](docs/03-data-model.md) — the ledger, unit of measure, batches

Then [docs/08-development.md](docs/08-development.md) to get running.

---

## The rules that are not style preferences

These exist because breaking them corrupts data silently rather than throwing an error.

**Never mutate a stock quantity.** All changes go through `inventory.services.post_movement()`. If you are writing `quantity -= n`, stop — you are breaking recall traceability, batch costing and the audit trail at once.

**Never pass a bare integer quantity.** Every quantity carries a unit of measure. A pack is not a tablet.

**Never allocate a batch by anything but FEFO.** Use `allocate_fefo()`. Not insertion order, not primary key.

**Never use a float for money.** Integer minor units with an explicit currency.

**Never use `Model.objects` in a view.** Use `tenant_objects`. An unfiltered query in request-handling code is a data leak waiting for a specific user.

**Never hardcode a regulatory rule.** Classifications, tax treatment, coverage rules and dispensing rules are effective-dated configuration. A transaction from last year must remain explainable under last year's rules.

**Never write a literal colour, size or spacing value in the frontend.** Design tokens only.

---

## Where code goes

```
views.py     → authorize, deserialize, call a service, serialize. No business logic.
services.py  → every business rule. The transaction boundary.
models.py    → fields, constraints, clean(). Never orchestration.
```

A view containing an `if` about domain state is a review failure. So is a model method that reaches into three other models.

Frontend order is fixed: **tokens → primitives → module templates → screens**. A screen that introduces a new primitive inline is a review failure; add it to `components/` where the rest of the design system lives.

---

## Workflow

```bash
git checkout -b feat/inventory-fefo-override
# work
make lint fmt types test
git commit -m "feat(inventory): allow FEFO override with logged reason"
```

Trunk-based. Short-lived branches, squash-merged into `main`, which is always deployable.

### Commits

[Conventional Commits](https://www.conventionalcommits.org/). The changelog is generated from them.

```
feat(sales): keep sale pending while momo callback is outstanding
fix(imports): apportion landed cost without losing a franc to rounding
refactor(core): extract sequence allocation into a service
docs(compliance): record narcotics register requirement
test(inventory): property test for uom round-tripping
```

Scope is the Django app or frontend module.

### Pull requests

State what changed, why, how you verified it, and what a reviewer should look at closely. If you changed behaviour a pharmacist would notice, say so in the words they would use.

**Checklist**

- [ ] Tests pass; new logic has tests
- [ ] Anything touching ledger, FEFO, UoM, money, tax, tenancy or idempotency has explicit tests
- [ ] No direct quantity mutation
- [ ] No `Model.objects` in a view
- [ ] No literal colours, sizes or spacing in frontend code
- [ ] Migrations included, reversible, forward-compatible
- [ ] UI copy within the length limits in docs/23-ui-copy.md
- [ ] Docs updated where behaviour or contract changed
- [ ] `CHANGELOG.md` updated under Unreleased for user-visible change
- [ ] Offline test run if POS, the agent, or the sync API changed

That last one is not optional. Duplicate-sale bugs are silent, corrupt both stock and revenue, and cannot be cleanly unwound.

---

## Adding a module

The order keeps the product coherent:

1. Model the domain — entities, invariants, migrations, model tests
2. Write services — the rules, the transaction boundary, service tests. **Most of the work belongs here**
3. Expose the API — serializers, views, permissions, API tests including cross-tenant
4. Regenerate frontend types: `make schema`
5. Compose the screen from existing components
6. Add document templates if the module produces documents
7. Update docs and changelog

If step 5 needs a new colour or size, stop. That is the moment inconsistency enters a design system — extend the tokens deliberately or reuse what exists.

---

## Reviewing

Look hardest at:

- **Ledger writes** — is this going through `post_movement`? Is the reason recorded?
- **Tenancy** — is every query scoped? Would a foreign tenant get 404, not 403?
- **Quantities** — is UoM carried through, or has something become a bare integer?
- **Money** — integer minor units, currency explicit, no float anywhere on the path?
- **Effective dating** — is this rule evaluated as-of the transaction date, or as-of today?
- **Idempotency** — can this be safely retried by an agent that lost its connection mid-request?
- **Tokens** — any literal design value?

Approve when it is correct, not when it is finished. In this system those are different things.

---

## Documentation

Update docs in the same PR as the change. Stale documentation is worse than none, because it is trusted.

- Behaviour change → the relevant module or design doc
- API contract change → [docs/07-api.md](docs/07-api.md) and regenerate the schema
- A decision that was expensive to make → a new ADR in [docs/10-decisions.md](docs/10-decisions.md)
- A newly discovered risk → [docs/11-risks.md](docs/11-risks.md)
- A new domain term → [docs/12-glossary.md](docs/12-glossary.md)

Write for the reader, lead with the useful part, show rather than describe, and link rather than duplicate.

---

## Guardrails that are product requirements

Not preferences. If a change would weaken one of these, it does not merge regardless of who asked for it.

- **No clinical advice.** Reference product information and official leaflet links only. Never symptom to drug.
- **OCR never authorizes.** A registered pharmacist confirms, and their council number attaches to the dispensing event.
- **Never label an unverified figure "net profit."** Use *estimated operating result*.
- **The Assistant never silently performs** an action that moves stock, money, or a regulated record.

---

## Questions

Domain questions belong in the glossary once answered. Architecture questions that took real thought belong in an ADR. If you had to ask, the next person will too.
