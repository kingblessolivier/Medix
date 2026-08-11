# 20 — Git workflow

Repository: **https://github.com/kingblessolivier/Medix.git**

```
feature branch → PR → staging → main → production
```

No direct commits to `staging` or `main`. Every change is a pull request.

---

## Branches

| Branch | Protected | Deploys to | Merges from |
|---|---|---|---|
| `main` | Yes | Production | `staging` only |
| `staging` | Yes | Staging | Feature branches |
| `feat/*` `fix/*` `chore/*` `docs/*` | No | — | — |

`main` is always deployable. `staging` is always releasable.

### Naming

```
feat/inventory-fefo-allocation
fix/pos-duplicate-on-reconnect
chore/upgrade-drf
docs/compliance-narcotics-register
hotfix/fiscal-submission-retry
```

Scope matches the Django app or frontend module. Branch from `staging`, not `main`.

---

## Flow

```bash
git checkout staging && git pull
git checkout -b feat/inventory-fefo-allocation

# work
make lint fmt types test

git commit -m "feat(inventory): FEFO allocation with logged override"
git push -u origin feat/inventory-fefo-allocation
gh pr create --base staging
```

Squash-merge into `staging`. Delete the branch.

### Promoting to production

```bash
gh pr create --base main --head staging --title "Release 0.2.0"
```

Merge commit, not squash — `main` keeps the release history. Tag after merge.

```bash
git tag -a v0.2.0 -m "Retail POS, fiscal, local agent"
git push origin v0.2.0
```

---

## Commits

Conventional Commits. The changelog is generated from them.

```
feat(sales): keep sale pending while momo callback outstanding
fix(imports): apportion landed cost without rounding loss
refactor(core): extract sequence allocation into a service
docs(compliance): record narcotics register requirement
test(inventory): property test for uom round-tripping
chore(deps): bump django to 5.1.4
```

Types: `feat` `fix` `refactor` `perf` `test` `docs` `chore` `ci`

Breaking changes use `!` and a `BREAKING CHANGE:` footer.

---

## Pull requests

### Template

```markdown
## What
One line.

## Why
Link the issue or state the reason.

## Verification
How you tested. Include offline test result if POS, agent, or sync changed.

## Review focus
Where to look hardest.
```

### Required checks

| Check | Blocks merge |
|---|---|
| `lint` — ruff, black, eslint, prettier | Yes |
| `types` — mypy, tsc | Yes |
| `migrations` — no missing migrations | Yes |
| `schema` — OpenAPI diff committed | Yes |
| `test-backend` — pytest, real Postgres | Yes |
| `test-frontend` — vitest | Yes |
| `design-rules` — token, icon, type-scale lint | Yes |
| `test-e2e` — playwright | On POS, agent, sync changes |
| `security` — dependency audit, secret scan | Yes |

### Review

One approval to `staging`. **Two approvals to `main`.**

Reviewer checklist in [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Branch protection

**`staging`** — PR required, 1 approval, checks must pass, branch up to date, no force push.

**`main`** — PR required from `staging` only, 2 approvals, checks must pass, linear history, no force push, no deletion, admins included.

---

## Environments

| Branch | Environment | Fiscal | Payments | Data |
|---|---|---|---|---|
| PR | Preview (ephemeral) | Mock | Mock | Seeded |
| `staging` | Staging | **RRA test VSDC** | Sandbox | Anonymized |
| `main` | Production | RRA production | Live | Real |

Never point a non-production environment at RRA production or live payment providers.

---

## Release

1. Freeze `staging`, run the manual checklist in [15-testing.md](15-testing.md)
2. Move `CHANGELOG.md` entries from Unreleased into a version
3. Open `staging → main`, two approvals
4. Merge, tag, deploy
5. Monitor 30 minutes — fiscal success rate, sync lag, error rate
6. Unfreeze

Site agents release separately. See [09-roadmap.md](09-roadmap.md).

---

## Hotfix

Production-only path.

```bash
git checkout -b hotfix/fiscal-retry main
# fix
gh pr create --base main
# merge, tag, deploy
git checkout staging && git merge main   # back-merge, always
```

Back-merging into `staging` is mandatory. A hotfix that only lands on `main` is reintroduced by the next release.

---

## CODEOWNERS

```
/backend/core/           @kingblessolivier
/backend/inventory/      @kingblessolivier
/backend/fiscal/         @kingblessolivier
/backend/compliance/     @kingblessolivier
/agent/                  @kingblessolivier
/frontend/src/design/    @kingblessolivier
/docs/                   @kingblessolivier
```

Ledger, fiscal, compliance and the agent always need an owner review — silent failures there corrupt data rather than throwing.

---

## Never

- Commit to `staging` or `main` directly
- Force-push a protected branch
- Merge with failing checks
- Skip the offline test when POS, agent or sync changed
- Commit `.env`, credentials, or real patient data
- Edit an applied migration
- Merge a PR that adds a literal colour or mutates stock quantity directly
