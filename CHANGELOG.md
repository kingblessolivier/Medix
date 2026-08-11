# Changelog

All notable changes to Medix are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Every user-visible change lands here in the same pull request that makes it.** Entries are written for the reader — a pharmacist, an owner, an implementer — not as a restatement of the commit message.

Categories: `Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security` · `Compliance`

`Compliance` is a Medix-specific category for changes that affect regulatory behaviour. These are called out separately because they may require a customer to be notified, or a rule configuration to be reviewed.

---

## [Unreleased]

### Added
- Full system documentation set — overview, architecture, data model, design system, modules, compliance, API, development, roadmap, decisions, risks, glossary, research, requirements, testing, security, operations, document design
- `CLAUDE.md` working instructions for the repository
- Published visual system specification (`medix-system-design.html`)

### Decisions recorded
- **ADR-001** Stock as an append-only ledger
- **ADR-002** Unit-of-measure hierarchy from day one
- **ADR-003** Cloud plus a local site agent
- **ADR-004** Shared-schema multi-tenancy with row-level isolation
- **ADR-005** Regulatory rules as versioned configuration
- **ADR-006** Organization type is a licence set, not a label
- **ADR-007** Django + DRF + PostgreSQL, React + TypeScript + Vite
- **ADR-008** Type scale at 20/13
- **ADR-009** Dark theme in scope for v1
- **ADR-010** Documents are designed artifacts, not digitized forms

### Notes
Research across Rwandan regulatory, payer and domain sources produced eleven findings that changed the design before implementation began. Three were architectural: VSDC's on-premise deployment model, CBHI's reported move to capitation, and partial-pack dispensing requiring a unit-of-measure hierarchy. Recorded in [docs/13-research.md](docs/13-research.md).

The organization model was corrected to treat **retail pharmacy and wholesale pharmacy as two licensed pharmacy types** sharing one core, rather than as different kinds of business.

---

## Release history

*No releases yet. Phase 0 in progress — see [docs/09-roadmap.md](docs/09-roadmap.md).*

Planned milestones:

| Version | Phase | Contents |
|---|---|---|
| 0.1.0 | Phase 1 | Catalog, UoM, stock ledger, FEFO |
| 0.2.0 | Phase 2 | Retail POS, fiscal, local agent — **pilot** |
| 0.3.0 | Phase 3 | Wholesale, marketplace, multi-branch |
| 0.4.0 | Phase 4 | Imports and demand consolidation |
| 0.5.0 | Phase 5 | Insurance *(gated on V3)* |
| 0.6.0 | Phase 6 | Compliance and cold chain |
| 0.7.0 | Phase 7 | Executive intelligence |
| 1.0.0 | Phase 8 | Assistant, polish, general availability |

---

## Writing entries

**Good**
```
### Fixed
- Sales no longer duplicate when a site reconnects after more than 24 hours offline.
  The agent was regenerating idempotency keys on retry.

### Compliance
- Controlled substance dispensing now records the patient's address, required by
  Law n° 03/2012. Existing entries are unaffected; new entries cannot be saved without it.
```

**Not good**
```
### Fixed
- Fixed sync bug
- Updated models
```

Say what changed from the user's side, and where it matters, why.

---

## Agent versioning

Site agents version independently of the cloud and are listed separately once releases begin. An agent must tolerate a cloud **one minor version ahead** — see [ADR-003](docs/10-decisions.md).

Agent entries carry an explicit compatibility line:

```
## Agent [1.2.0]
Compatible with cloud 0.4.x – 0.5.x
```
