# 06 — Compliance

Compliance in Medix is not a reporting module. It is the set of rules that stop the software from doing something unlawful, and it is wired into the transaction path.

**Governing principle: all regulatory rules are versioned configuration with effective dates.** Never Python constants, never React conditionals. Rwanda's regulatory environment is actively maintained and CBHI is currently reforming — a transaction from six months ago must remain explainable under the rules that applied then.

Every rule row carries `effective_from` and `effective_to`, and evaluation is always as-of a date.

> **Status of this document.** The requirements below were established from published Rwandan sources during system research (see [13-research.md](13-research.md)). Items marked **VERIFY** need confirmation with the relevant authority before the dependent module is built. Nothing here is legal advice; the compliance officer signs off on the final rule configuration.

---

## 1. Rwanda FDA — product and premises

### Product registration

The product master mirrors the Rwanda FDA registered-products model:

registration number · brand · generic · strength · dosage form · pack size · shelf life · manufacturer · manufacturer country · marketing authorization holder · local agent · registration date · registration expiry · status

**Enforcement.** A product whose registration is `SUSPENDED`, `WITHDRAWN` or expired cannot be listed for sale or dispensed. Existing stock moves to `QUARANTINED`, not `AVAILABLE`.

### Premises licensing — two pharmacy types

Rwanda FDA licenses retail and wholesale pharmacies as **distinct premises types** with different facility standards. Wholesale premises carry a larger minimum storage area and a separately defined sales and administrative area.

```python
class PremisesLicence:
    organization, branch
    kind        # RETAIL_PHARMACY | WHOLESALE_PHARMACY | IMPORTER | DISTRIBUTOR
    number, issued_on, expiry, status, issuing_authority
    inspection_history
```

**Enforcement.** Capability is derived from held licences. A branch without a valid retail licence cannot open a POS session. A branch without a wholesale licence cannot publish marketplace listings. Licence expiry produces escalating alerts at 90, 60, 30 and 7 days.

### Good Storage and Distribution Practice

Storage conditions, documentation, staffing and premises requirements are inspected. Medix supports this through location temperature classes, the temperature log, the stock ledger as the documentation trail, and inspection records.

---

## 2. Legal status and dispensing

Rwanda distinguishes **Controlled Medicines**, **Prescription-Only Medicines (POM)** and **Over-the-Counter (OTC)** products. POM requires a prescription; OTC does not. Medicines are dispensed through licensed pharmacies.

```python
class LegalStatus(TextChoices):
    OTC        = "OTC"
    POM        = "POM"
    CONTROLLED = "CONTROLLED"
```

**Enforcement at POS.** A POM or CONTROLLED line **blocks** the sale — not a warning, a block — until a prescription is attached and verified.

---

## 3. Controlled substances — Law n° 03/2012

Governed by Law n° 03/2012 on narcotic drugs, psychotropic substances and precursors, with categories set by Ministerial Order (2019).

**The statutory requirement:** every delivery of a narcotic or psychotropic must be **registered immediately on the prescription**, recording the patient's name and address and the denomination of the substance delivered.

This is a distinct artifact, not a flag:

```python
class ControlledDeliveryEntry:
    sale_line, prescription
    patient_name, patient_address     # explicitly captured, required
    substance_denomination, schedule
    quantity_base, uom
    dispensed_by                       # pharmacist, council number resolved at write
    balance_after_base                 # running register balance
    entered_at
```

Append-only. Reconcilable. Reportable in the form the law expects.

**Enforcement.** A controlled sale line cannot be written without a complete register entry in the same transaction. Patient address is mandatory for controlled dispensing and optional otherwise.

---

## 4. Pharmacist registration

Pharmacy premises licensing and **professional registration are separate**. Rwanda has a National Pharmacy Council and a Good Pharmacy Practice standard.

```python
class PharmacistRegistration:
    user, council_number, issued_on, expiry, status
```

**Enforcement.** Dispensing attaches to a user with a current registration. An expired registration cannot verify a prescription or complete a POM sale. Locum access is time-boxed at the grant.

---

## 5. RRA — fiscal invoicing

VAT-registered taxpayers must operate an electronic invoicing system at each sales location and issue EIS/EBM invoices for sales. RRA provides EBM 2.1 and VSDC for integrating private systems.

### The architectural consequence

VSDC is distributed as a **WAR file deployed on the taxpayer's own local webserver**, approved per taxpayer, with separate test and production instances. A pure cloud SaaS cannot issue fiscal invoices on a pharmacy's behalf. This is why Medix has a local agent.

> **VERIFY.** Confirm with RRA whether a cloud-hosted per-tenant VSDC is permissible. This single answer determines cloud-only versus cloud-plus-agent for the whole product.

### Provisioning

Per tenant: apply for VSDC → RRA approval → deploy → initialize → activate → test → production. This is a tracked workflow with states, not a settings field.

### Enforcement

A completed sale must have a fiscal outcome: submitted and accepted, or queued with a visible exception. Sales are never silently unfiscalized. The exception queue is an operational screen, not a log file.

---

## 6. VAT treatment

Goods and services for health purposes are **exempt** from VAT in Rwanda, and pharmaceutical products are not subject to excise.

**Exempt is not zero-rated.** Zero-rated suppliers do not charge output VAT but may reclaim input VAT; exempt suppliers cannot reclaim. This changes true cost of goods and therefore margin.

```python
class TaxTreatment(TextChoices):
    EXEMPT      = "EXEMPT"       # most medicines
    STANDARD    = "STANDARD"     # cosmetics, many devices, consumables
    ZERO_RATED  = "ZERO"
```

**Consequences for the build:**

- Tax treatment is a **product attribute**, resolved per line against rules effective on the sale date.
- **Every invoice is mixed-treatment.** A basket with paracetamol and shampoo has two treatments on one document.
- Irrecoverable input VAT on exempt supplies must be reflected in landed cost, or margin is overstated.
- The EBM payload must carry per-line tax classification correctly.

> **VERIFY.** Confirm the current classification list with a tax adviser. Configure, do not hardcode.

---

## 7. Data protection — Law 058/2021

Health data is **sensitive personal data** under Rwanda's Law No. 058/2021 relating to the protection of personal data and privacy. The supervisory authority is the National Cyber Security Authority.

### Obligations that affect the build

| Obligation | Implementation |
|---|---|
| Lawful basis and consent | Consent capture on patient record creation, with purpose and timestamp |
| Register as data controller | Organizational task, tracked in compliance |
| Designate a data protection officer | Organizational task, recorded per organization |
| Purpose limitation | Patient data is not reused for analytics without aggregation and de-identification |
| Retention | Retention policy per data class, with scheduled deletion jobs |
| Subject access and erasure | Export and erasure workflows, with legal-hold override where retention is required by other law |
| Breach notification | Incident procedure with a defined clock |
| Demonstrable access control | Reads of patient data are recorded as `AuditEvent`, not only writes |
| Cross-border transfer | **Constrains hosting region** |

> **VERIFY — blocking.** Confirm data residency and cross-border transfer requirements **before** choosing an infrastructure region. Retrofitting a region change after go-live is severe.

### Practical rules

- Patient identifiers never appear in URLs or query strings.
- Analytics runs on aggregates; no patient-level export outside the clinical path.
- Support access to patient data is explicit, time-boxed, reason-logged.
- Backups inherit the residency constraint.

---

## 8. Insurance

RSSB operates CBHI (Mutuelle de Santé) and contracts selectively with private pharmacies against explicit criteria. A pharmacy that is not contracted cannot bill the scheme.

> **VERIFY — blocking for the insurance module.** CBHI is reportedly moving from retrospective fee-for-service reimbursement toward a **capitation** model in which providers are paid in advance for planned services. Confirm with RSSB whether this applies to contracted private pharmacies. If it does, the claim-and-reimburse workflow is the wrong shape for the dominant payer.

The data model supports both (`SchemeContract.model = FEE_FOR_SERVICE | CAPITATION`), but the workflows differ substantially and only one should be built first.

---

## 9. Cold chain

Vaccines and biologics such as insulin require 2–8 °C storage; excursions destroy potency. Monitoring uses calibrated data loggers with alarms.

```python
Location.temperature_class  # AMBIENT | COOL_15_25 | COLD_2_8 | FROZEN
Product.cold_chain          # bool
TemperatureExcursion        # location, window, min/max, batches quarantined
```

**Enforcement.**

- A cold-chain batch cannot be placed in an `AMBIENT` location.
- A recorded excursion **automatically quarantines** affected batches pending assessment — release requires an explicit decision with a reason.
- Cold-chain shipments carry a temperature log that becomes part of the receiving record.
- Import of cold-chain products requires refrigerated transport to be declared on the shipment.

---

## 10. Traceability — GS1

Pharmaceutical packs carry a GS1 DataMatrix encoding GTIN `(01)`, batch `(10)`, expiry `(17)` and, where serialized, serial `(21)`. Regional traceability mandates are phasing in, with GTIN, expiry and batch preceding full serialization.

> **VERIFY.** Confirm the mandate dates that apply to Rwanda specifically against the current regulation tracker.

**Build for it regardless.** Parsing GS1 application identifiers means a scan at receiving auto-fills batch and expiry, and a scan at POS resolves the exact batch. That single capability removes most manual data entry in the system, mandate or not.

---

## 11. Recalls, disposal and pharmacovigilance

**Recall.** A recall notice targets a product or a specific batch, then tracks execution per location: quantity on hand, action taken, completion. Recall traceability is the clearest justification for the append-only ledger — "who received units from batch AMX-2601" must be answerable in seconds.

**Disposal.** Destruction of expired or recalled stock is a regulated act requiring a witnessed certificate. It is never a status change alone.

```python
class DisposalRecord:
    batch, quantity_base, method, reason
    witness_name, witness_role, authority_reference
    certificate  FK Document
    performed_at, performed_by
```

**Adverse events.** A pharmacist can raise an adverse drug reaction report against a product and batch, for submission to Rwanda FDA.

---

## 12. Compliance dashboard

One screen answering *what is about to become a problem*:

- Licences expiring — premises, by branch and kind
- Pharmacist registrations expiring
- Product registrations expiring or suspended
- Batches expiring, by risk band
- Open recalls with incomplete execution
- Fiscal exceptions unresolved
- Temperature excursions pending assessment
- Data protection tasks — DPO designated, controller registration current

---

## Verification register

| # | Question | Authority | Blocks |
|---|---|---|---|
| 1 | Is a cloud-hosted per-tenant VSDC permissible? | RRA | Fiscal module, whole architecture |
| 2 | Does CBHI capitation apply to contracted private pharmacies? | RSSB | Insurance module |
| 3 | Data residency and cross-border transfer requirements | NCSA | **Infrastructure region — before any deployment** |
| 4 | Current VAT classification for the product mix | Tax adviser | Tax configuration |
| 5 | GS1 traceability mandate dates for Rwanda | Rwanda FDA | Serialization scope |
| 6 | Retail vs wholesale facility standards, current revision | Rwanda FDA | Licence validation rules |
| 7 | Controlled substance categories, current Ministerial Order | Rwanda FDA | Narcotics register configuration |

Each becomes a tracked task in Phase 0. See [09-roadmap.md](09-roadmap.md).
