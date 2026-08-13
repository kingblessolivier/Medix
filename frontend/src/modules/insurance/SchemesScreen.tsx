/* Setting insurance up, which nothing could do.
 *
 * Without a contract `check_eligibility` finds no cover, so every insured
 * patient is charged in full — with no error, no warning and no claim.
 * The Claims screen read "No claims" forever and nothing said why. That
 * is the worst kind of missing feature: it looks like the pharmacy has
 * no insurance business rather than like a setup step nobody did.
 *
 * Three things, in the order they have to exist:
 *
 *   Scheme    who the insurer is — RSSB, CBHI, a private one.
 *   Contract  what was agreed with them, and how they pay. Fee for
 *             service raises a claim per sale; capitation is paid per
 *             member per period and raises none.
 *   Cover     how much of what, resolved narrowest first.
 *
 *   Members   which patient belongs to which scheme. Without one the
 *             first three cover nobody — eligibility matches a patient
 *             to a scheme through this row, so a contract with no
 *             members charges every patient in full exactly as before.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ApiFailure,
  api,
  type CoverageRule,
  type Member,
  type Scheme,
  type SchemeContract,
} from "@/lib/api";
import { DataTable, TableTabs, type Column, type TableTab } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  Checkbox,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

/* Stored values, from insurance/models.py. */
const MODELS = [
  ["FEE_FOR_SERVICE", "Fee for service"],
  ["CAPITATION", "Capitation"],
] as const;

const PERIODS = [
  ["MONTH", "Per member per month"],
  ["QUARTER", "Per member per quarter"],
] as const;

const SCOPES = [
  ["ALL", "Everything"],
  ["CATEGORY", "A therapeutic category"],
  ["LEGAL_STATUS", "A legal status"],
  ["PRODUCT", "One product"],
] as const;

const TABS: TableTab[] = [
  { id: "schemes", label: "Schemes" },
  { id: "contracts", label: "Contracts" },
  { id: "cover", label: "Cover" },
  { id: "members", label: "Members" },
];

export function SchemesScreen() {
  const [tab, setTab] = useState("schemes");

  const schemes = useQuery({ queryKey: ["schemes"], queryFn: () => api.schemes() });
  const contracts = useQuery({
    queryKey: ["scheme-contracts"],
    queryFn: () => api.schemeContracts(),
  });

  if (schemes.isPending || contracts.isPending) return <Skeleton className="h-[400px]" />;

  const allSchemes = schemes.data?.results ?? [];
  const allContracts = contracts.data?.results ?? [];
  const noScheme = allSchemes.length === 0;
  const noContract = allContracts.length === 0;

  return (
    <>
      <PageHeader title="Insurance" description="Who covers what, and how they pay" />

      {/* Silence is the failure here: with no contract every insured
          patient is charged in full and nothing says so. */}
      {noScheme ? (
        <NextAction
          heading="Add a scheme"
          detail="Until one exists every patient pays in full."
        />
      ) : noContract ? (
        <NextAction
          heading="Add a contract"
          detail="A scheme with no contract covers nothing."
        />
      ) : null}

      <TableTabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "schemes" && <Schemes schemes={allSchemes} />}
      {tab === "contracts" && (
        <Contracts contracts={allContracts} schemes={allSchemes} />
      )}
      {tab === "cover" && <Cover contracts={allContracts} />}
      {tab === "members" && <Members schemes={allSchemes} />}
    </>
  );
}

/* -- schemes ------------------------------------------------------------ */

function Schemes({ schemes }: { schemes: Scheme[] }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState("");

  const save = useMutation({
    mutationFn: () => api.saveScheme({ name, code, is_active: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["schemes"] });
      setAdding(false);
      setName("");
      setCode("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const columns: Column<Scheme>[] = [
    { key: "name", header: "Scheme", render: (s) => s.name },
    { key: "code", header: "Code", mono: true, render: (s) => s.code },
    {
      key: "active",
      header: "Status",
      render: (s) =>
        s.is_active ? (
          <StatusPill tone="ok">Active</StatusPill>
        ) : (
          <StatusPill tone="neutral">Inactive</StatusPill>
        ),
    },
  ];

  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button
          variant="primary"
          icon={<Plus size={15} strokeWidth={2} aria-hidden />}
          onClick={() => setAdding(true)}
        >
          Add scheme
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={schemes}
        rowKey={(s) => s.id}
        density="compact"
        caption="Insurance schemes"
        emptyHeading="No schemes"
        emptyBody="The insurer or health-financing body."
      />

      {adding && (
        <Modal
          open
          title="Add scheme"
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              disabled={!name.trim() || !code.trim()}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Add scheme
            </Button>
          }
        >
          {failure && (
            <Banner tone="bad" className="mb-4">
              {failure}
            </Banner>
          )}
          <div className="flex flex-col gap-4">
            <Field label="Name" required>
              {(id) => (
                <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />
              )}
            </Field>
            <Field label="Code" help="Short. It appears on every claim." required>
              {(id) => (
                <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} />
              )}
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}

/* -- contracts ---------------------------------------------------------- */

function Contracts({
  contracts,
  schemes,
}: {
  contracts: SchemeContract[];
  schemes: Scheme[];
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [scheme, setScheme] = useState("");
  const [reference, setReference] = useState("");
  const [model, setModel] = useState<string>("FEE_FOR_SERVICE");
  const [window, setWindow] = useState("90");
  const [terms, setTerms] = useState("30");
  const [capitation, setCapitation] = useState("");
  const [period, setPeriod] = useState<string>("MONTH");
  const [from, setFrom] = useState("");
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (schemes[0] && !scheme) setScheme(schemes[0].id);
  }, [schemes, scheme]);

  const save = useMutation({
    mutationFn: () =>
      api.saveSchemeContract({
        scheme,
        reference,
        model,
        is_contracted: true,
        claim_window_days: Number(window) || 90,
        payment_terms_days: Number(terms) || 30,
        capitation_amount: model === "CAPITATION" ? Number(capitation) || 0 : null,
        capitation_period: period,
        effective_from: from,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheme-contracts"] });
      setAdding(false);
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const columns: Column<SchemeContract>[] = [
    { key: "scheme", header: "Scheme", render: (c) => c.scheme_name },
    { key: "reference", header: "Reference", mono: true, render: (c) => c.reference || "—" },
    { key: "model", header: "Paid by", render: (c) => c.model_label },
    {
      key: "claims",
      header: "Claims",
      render: (c) =>
        c.claims_per_sale ? (
          <Badge tone="info">Per sale</Badge>
        ) : (
          /* Under capitation the scheme has already paid for the period.
             Claiming as well would be asking twice. */
          <Badge tone="neutral">None raised</Badge>
        ),
    },
    { key: "from", header: "From", render: (c) => c.effective_from },
    {
      key: "terms",
      header: "Terms",
      render: (c) => `${c.payment_terms_days} days`,
    },
  ];

  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button
          variant="primary"
          icon={<Plus size={15} strokeWidth={2} aria-hidden />}
          disabled={schemes.length === 0}
          onClick={() => setAdding(true)}
        >
          Add contract
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={contracts}
        rowKey={(c) => c.id}
        density="compact"
        caption="Scheme contracts"
        emptyHeading="No contracts"
        emptyBody="A scheme with no contract covers nothing."
      />

      {adding && (
        <Modal
          open
          title="Add contract"
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              disabled={!scheme || !from}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Add contract
            </Button>
          }
        >
          {failure && (
            <Banner tone="bad" className="mb-4">
              {failure}
            </Banner>
          )}
          <div className="flex flex-col gap-4">
            <Field label="Scheme" required>
              {(id) => (
                <Select id={id} value={scheme} onChange={(e) => setScheme(e.target.value)}>
                  {schemes.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            <Field
              label={
                (
                  <Help term="Paid by">
                    Fee for service raises a claim per sale. Capitation pays a fixed
                    amount per member per period and raises no claims at all.
                  </Help>
                ) as unknown as string
              }
              required
            >
              {(id) => (
                <Select id={id} value={model} onChange={(e) => setModel(e.target.value)}>
                  {MODELS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            {model === "CAPITATION" && (
              <>
                <Banner tone="info">
                  No claims are raised. The scheme pays per member.
                </Banner>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Amount" required>
                    {(id) => (
                      <Input
                        id={id}
                        type="number"
                        min={0}
                        value={capitation}
                        onChange={(e) => setCapitation(e.target.value)}
                        className="tabular text-right"
                      />
                    )}
                  </Field>
                  <Field label="Period">
                    {(id) => (
                      <Select
                        id={id}
                        value={period}
                        onChange={(e) => setPeriod(e.target.value)}
                      >
                        {PERIODS.map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                </div>
              </>
            )}

            <Field label="Reference" help="What the scheme calls this contract.">
              {(id) => (
                <Input
                  id={id}
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                />
              )}
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Claim window" help="Days to submit before it lapses.">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    min={1}
                    value={window}
                    onChange={(e) => setWindow(e.target.value)}
                    className="tabular text-right"
                  />
                )}
              </Field>
              <Field label="Payment terms">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    min={0}
                    value={terms}
                    onChange={(e) => setTerms(e.target.value)}
                    className="tabular text-right"
                  />
                )}
              </Field>
            </div>

            <Field label="Effective from" help="Cover before this date is unchanged." required>
              {(id) => (
                <Input
                  id={id}
                  type="date"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}

/* -- cover -------------------------------------------------------------- */

function Cover({ contracts }: { contracts: SchemeContract[] }) {
  const queryClient = useQueryClient();
  const [contract, setContract] = useState("");
  const [adding, setAdding] = useState(false);
  const [scope, setScope] = useState<string>("ALL");
  const [percent, setPercent] = useState("80");
  const [maximum, setMaximum] = useState("");
  const [excluded, setExcluded] = useState(false);
  const [from, setFrom] = useState("");
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (contracts[0] && !contract) setContract(contracts[0].id);
  }, [contracts, contract]);

  const rules = useQuery({
    queryKey: ["coverage-rules", contract],
    queryFn: () => api.coverageRules(contract),
    enabled: Boolean(contract),
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveCoverageRule({
        contract,
        scope,
        // Basis points, never a float: 33.33% is not representable in
        // binary and a rate that drifts across two screens invites the
        // question of which one is lying.
        coverage_basis_points: Math.round(Number(percent) * 100),
        maximum_amount: maximum === "" ? null : Number(maximum),
        is_excluded: excluded,
        effective_from: from,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["coverage-rules", contract] });
      setAdding(false);
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const columns: Column<CoverageRule>[] = [
    {
      key: "scope",
      header: "Applies to",
      render: (r) => r.product_name || r.category_name || r.scope_label,
    },
    {
      key: "cover",
      header: "Covered",
      numeric: true,
      render: (r) =>
        r.is_excluded ? (
          /* Not the same as 0%. Both reach the same number and only one
             stops a claim line being raised at all. */
          <span className="text-bad-text">Excluded</span>
        ) : (
          `${(r.coverage_basis_points / 100).toFixed(0)}%`
        ),
    },
    {
      key: "maximum",
      header: "Capped at",
      numeric: true,
      render: (r) => (r.maximum_amount === null ? "—" : MONEY.format(r.maximum_amount)),
    },
    { key: "from", header: "From", render: (r) => r.effective_from },
  ];

  if (contracts.length === 0) {
    return (
      <p className="text-body text-text-2">Add a contract first.</p>
    );
  }

  return (
    <>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div className="w-64">
          <Field label="Contract">
            {(id) => (
              <Select
                id={id}
                value={contract}
                onChange={(e) => setContract(e.target.value)}
              >
                {contracts.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.scheme_name}
                    {row.reference ? ` · ${row.reference}` : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Button
          variant="primary"
          icon={<Plus size={15} strokeWidth={2} aria-hidden />}
          onClick={() => setAdding(true)}
        >
          Add rule
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={rules.data?.results ?? []}
        rowKey={(r) => r.id}
        density="compact"
        loading={rules.isLoading}
        caption="Coverage rules"
        emptyHeading="No rules"
        emptyBody="Nothing is covered until one exists."
      />

      {adding && (
        <Modal
          open
          title="Add rule"
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              disabled={!from}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Add rule
            </Button>
          }
        >
          <Consequence
            lines={[
              "Applies to sales from its start date onward.",
              "A narrower rule beats a wider one.",
            ]}
          />
          {failure && (
            <Banner tone="bad" className="mt-3">
              {failure}
            </Banner>
          )}
          <div className="mt-4 flex flex-col gap-4">
            <Field label="Applies to" required>
              {(id) => (
                <Select id={id} value={scope} onChange={(e) => setScope(e.target.value)}>
                  {SCOPES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            <Checkbox
              checked={excluded}
              onChange={setExcluded}
              label="Not covered"
            />

            {!excluded && (
              <>
                <Field label="Covered" help="Percent the scheme pays." required>
                  {(id) => (
                    <Input
                      id={id}
                      type="number"
                      min={0}
                      max={100}
                      value={percent}
                      onChange={(e) => setPercent(e.target.value)}
                      className="tabular text-right"
                    />
                  )}
                </Field>
                <Field label="Capped at" help="Leave blank for no cap.">
                  {(id) => (
                    <Input
                      id={id}
                      type="number"
                      min={0}
                      value={maximum}
                      onChange={(e) => setMaximum(e.target.value)}
                      className="tabular text-right"
                    />
                  )}
                </Field>
              </>
            )}

            <Field label="Effective from" required>
              {(id) => (
                <Input
                  id={id}
                  type="date"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}


/* -- members ------------------------------------------------------------ */

/* Which patient belongs to which scheme.
 *
 * The step that makes the other three do anything: eligibility matches a
 * patient to a scheme through this row, so a contract with no members
 * charges every patient in full exactly as it did before any of it
 * existed. */
function Members({ schemes }: { schemes: Scheme[] }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [patient, setPatient] = useState("");
  const [scheme, setScheme] = useState("");
  const [memberNumber, setMemberNumber] = useState("");
  const [principal, setPrincipal] = useState("");
  const [failure, setFailure] = useState("");

  const members = useQuery({
    queryKey: ["members", search],
    queryFn: () => api.members(search),
  });
  const patients = useQuery({
    queryKey: ["patients"],
    queryFn: () => api.patients(),
    enabled: adding,
  });

  useEffect(() => {
    if (schemes[0] && !scheme) setScheme(schemes[0].id);
  }, [schemes, scheme]);

  const save = useMutation({
    mutationFn: () =>
      api.saveMember({
        patient,
        scheme,
        member_number: memberNumber,
        principal_name: principal,
        is_active: true,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["members"] });
      setAdding(false);
      setMemberNumber("");
      setPrincipal("");
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  const columns: Column<Member>[] = [
    { key: "patient", header: "Patient", render: (m) => m.patient_name },
    { key: "scheme", header: "Scheme", render: (m) => m.scheme_name },
    { key: "number", header: "Member number", mono: true, render: (m) => m.member_number },
    {
      key: "principal",
      header: "Principal",
      render: (m) => m.principal_name || <span className="text-text-3">—</span>,
    },
    {
      key: "valid",
      header: "Cover",
      render: (m) =>
        m.is_currently_valid ? (
          <StatusPill tone="ok">Valid</StatusPill>
        ) : (
          /* Lapsed and inactive reach the same result at the counter —
             the patient pays in full — so both read as one word. */
          <StatusPill tone="bad">Not valid</StatusPill>
        ),
    },
  ];

  return (
    <>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div className="w-64">
          <Field label="Find a member">
            {(id) => (
              <Input
                id={id}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Name or number"
              />
            )}
          </Field>
        </div>
        <Button
          variant="primary"
          icon={<Plus size={15} strokeWidth={2} aria-hidden />}
          disabled={schemes.length === 0}
          onClick={() => setAdding(true)}
        >
          Add member
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={members.data?.results ?? []}
        rowKey={(m) => m.id}
        density="compact"
        loading={members.isLoading}
        caption="Scheme members"
        emptyHeading="No members"
        emptyBody="A contract with no members covers nobody."
      />

      {adding && (
        <Modal
          open
          title="Add member"
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              disabled={!patient || !scheme || !memberNumber.trim()}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Add member
            </Button>
          }
        >
          <Consequence
            lines={[
              "Cover applies to this patient from now on.",
              "Their co-pay is worked out at the counter.",
            ]}
          />
          {failure && (
            <Banner tone="bad" className="mt-3">
              {failure}
            </Banner>
          )}
          <div className="mt-4 flex flex-col gap-4">
            <Field label="Patient" required>
              {(id) => (
                <Select
                  id={id}
                  value={patient}
                  onChange={(e) => setPatient(e.target.value)}
                >
                  <option value="">Choose a patient</option>
                  {(patients.data?.results ?? []).map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.full_name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Scheme" required>
              {(id) => (
                <Select id={id} value={scheme} onChange={(e) => setScheme(e.target.value)}>
                  {schemes.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Member number" help="As printed on their card." required>
              {(id) => (
                <Input
                  id={id}
                  value={memberNumber}
                  onChange={(e) => setMemberNumber(e.target.value)}
                  className="font-mono"
                />
              )}
            </Field>
            <Field label="Principal" help="Whose membership it is, if a dependant.">
              {(id) => (
                <Input
                  id={id}
                  value={principal}
                  onChange={(e) => setPrincipal(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}