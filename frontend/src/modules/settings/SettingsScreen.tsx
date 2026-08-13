/* The configuration that used to be editable only in a database.
 *
 * Four things live here, and three of them are **effective-dated**:
 * alert thresholds, tax rules and controlled quotas. Those are never
 * edited in place — a decision from eight months ago has to stay
 * explainable under the rules that applied then, so changing one closes
 * the current row and opens the next. The screen says so rather than
 * offering an edit that would quietly rewrite history.
 *
 * Manufacturers and categories are ordinary reference data and edit
 * normally.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import {
  ApiFailure,
  api,
  type AlertRule,
  type Category,
  type ControlledQuota,
  type Manufacturer,
  type TaxRule,
} from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const TABS: TableTab[] = [
  { id: "manufacturers", label: "Manufacturers" },
  { id: "categories", label: "Categories" },
  { id: "thresholds", label: "Alert thresholds" },
  { id: "tax", label: "Tax rules" },
  { id: "quotas", label: "Controlled quotas" },
];

export function SettingsScreen() {
  const [tab, setTab] = useState("manufacturers");

  return (
    <>
      <PageHeader title="Settings" description="Reference data and thresholds." />
      <TableTabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "manufacturers" && <Manufacturers />}
      {tab === "categories" && <Categories />}
      {tab === "thresholds" && <Thresholds />}
      {tab === "tax" && <TaxRules />}
      {tab === "quotas" && <Quotas />}
    </>
  );
}

/* -- manufacturers ------------------------------------------------------ */

function Manufacturers() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Manufacturer | null>(null);
  const [adding, setAdding] = useState(false);

  const manufacturers = useQuery({
    queryKey: ["manufacturers"],
    queryFn: () => api.manufacturers(),
  });

  const columns: Column<Manufacturer>[] = [
    { key: "name", header: "Name", render: (m) => m.name },
    { key: "country", header: "Country", render: (m) => m.country_of_origin || "—" },
    {
      key: "gmp",
      header: "GMP",
      render: (m) =>
        m.gmp_certified ? (
          <Badge tone="ok">Certified</Badge>
        ) : (
          /* A purchasing decision, not a label: a depot may be barred
             from importing from an uncertified site. */
          <Badge tone="bad">Not certified</Badge>
        ),
    },
    {
      key: "products",
      header: "Products",
      numeric: true,
      render: (m) => m.product_count.toLocaleString(),
    },
    {
      key: "active",
      header: "",
      render: (m) => (m.is_active ? null : <Badge tone="neutral">Inactive</Badge>),
    },
  ];

  if (manufacturers.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button
          variant="primary"
          icon={<Plus size={16} strokeWidth={1.9} aria-hidden />}
          onClick={() => setAdding(true)}
        >
          Add manufacturer
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={manufacturers.data?.results ?? []}
        rowKey={(m) => m.id}
        density="compact"
        onRowClick={setEditing}
        caption="Manufacturers"
        emptyHeading="No manufacturers"
        emptyBody="Add one to attribute products to it."
      />
      <ManufacturerModal
        manufacturer={editing}
        open={adding || Boolean(editing)}
        onClose={() => {
          setEditing(null);
          setAdding(false);
        }}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ["manufacturers"] })}
      />
    </>
  );
}

function ManufacturerModal({
  manufacturer,
  open,
  onClose,
  onSaved,
}: {
  manufacturer: Manufacturer | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(manufacturer?.name ?? "");
  const [country, setCountry] = useState(manufacturer?.country_of_origin ?? "");
  const [gmp, setGmp] = useState(manufacturer?.gmp_certified ?? true);
  const [failure, setFailure] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.saveManufacturer(
        { name, country_of_origin: country, gmp_certified: gmp },
        manufacturer?.id,
      ),
    onSuccess: () => {
      onSaved();
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  if (!open) return null;

  return (
    <Modal
      open
      title={manufacturer ? manufacturer.name : "Add manufacturer"}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!name.trim()}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save
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
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Country of origin">
          {(id) => (
            <Input id={id} value={country} onChange={(e) => setCountry(e.target.value)} />
          )}
        </Field>
        <Field label="GMP certified" help="Good Manufacturing Practice.">
          {(id) => (
            <Select
              id={id}
              value={gmp ? "yes" : "no"}
              onChange={(e) => setGmp(e.target.value === "yes")}
            >
              <option value="yes">Certified</option>
              <option value="no">Not certified</option>
            </Select>
          )}
        </Field>
      </div>
    </Modal>
  );
}

/* -- categories --------------------------------------------------------- */

function Categories() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [failure, setFailure] = useState("");

  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api.categories() });

  const save = useMutation({
    mutationFn: () => api.saveCategory({ name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["categories"] });
      setName("");
      setFailure("");
      setAdding(false);
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  if (categories.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button
          variant="primary"
          icon={<Plus size={16} strokeWidth={1.9} aria-hidden />}
          onClick={() => setAdding(true)}
        >
          Add category
        </Button>
      </div>
      <DataTable
        columns={[{ key: "name", header: "Therapeutic category", render: (c: Category) => c.name }]}
        rows={categories.data?.results ?? []}
        rowKey={(c) => c.id}
        density="compact"
        caption="Therapeutic categories"
        emptyHeading="No categories"
        emptyBody="Categories group products for browsing and reporting."
      />
      {adding && (
        <Modal
          open
          title="Add category"
          onClose={() => setAdding(false)}
          footer={
            <Button
              variant="primary"
              className="w-full"
              disabled={!name.trim()}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Save
            </Button>
          }
        >
          {failure && (
            <Banner tone="bad" className="mb-4">
              {failure}
            </Banner>
          )}
          <Field label="Name" required>
            {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
        </Modal>
      )}
    </>
  );
}

/* -- effective-dated configuration -------------------------------------- */

/** Shown above every dated table. The reason there is no edit action. */
function DatedNotice() {
  return (
    <Banner tone="info" className="mb-3">
      Superseded, never edited. Adding a row closes the one before it.
    </Banner>
  );
}

function Thresholds() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.alertRules() });

  const columns: Column<AlertRule>[] = [
    { key: "code", header: "Alert", mono: true, render: (r) => r.code },
    { key: "severity", header: "Severity", render: (r) => r.severity },
    {
      key: "threshold",
      header: "Fires at",
      render: (r) =>
        Object.entries(r.threshold)
          .map(([key, value]) => `${value} ${key}`)
          .join(", ") || "—",
    },
    { key: "from", header: "From", render: (r) => DAY.format(new Date(r.effective_from)) },
    {
      key: "to",
      header: "Until",
      render: (r) => (r.effective_to ? DAY.format(new Date(r.effective_to)) : "In force"),
    },
  ];

  if (rules.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <DatedNotice />
      <div className="mb-3 flex justify-end">
        <Button
          variant="primary"
          icon={<Plus size={16} strokeWidth={1.9} aria-hidden />}
          onClick={() => setAdding(true)}
        >
          Supersede a threshold
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={rules.data?.results ?? []}
        rowKey={(r) => r.id}
        density="compact"
        caption="Alert thresholds"
        emptyHeading="No thresholds"
      />
      {adding && (
        <ThresholdModal
          codes={[...new Set((rules.data?.results ?? []).map((r) => r.code))]}
          onClose={() => setAdding(false)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["alert-rules"] });
            setAdding(false);
          }}
        />
      )}
    </>
  );
}

function ThresholdModal({
  codes,
  onClose,
  onSaved,
}: {
  codes: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [code, setCode] = useState(codes[0] ?? "");
  const [severity, setSeverity] = useState("WARNING");
  const [key, setKey] = useState("days");
  const [value, setValue] = useState("");
  const [failure, setFailure] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.saveAlertRule({
        code,
        severity,
        threshold: { [key]: Number(value) },
        effective_from: new Date().toISOString().slice(0, 10),
      }),
    onSuccess: onSaved,
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  return (
    <Modal
      open
      title="Supersede a threshold"
      subtitle="Takes effect today. The current row stays readable."
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!code || !value}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}
      <div className="flex flex-col gap-4">
        <Field label="Alert" required>
          {(id) => (
            <Select id={id} value={code} onChange={(e) => setCode(e.target.value)}>
              {codes.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Severity" required>
          {(id) => (
            <Select id={id} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="CRITICAL">Critical — blocks</option>
              <option value="WARNING">Warning — needs acknowledging</option>
              <option value="INFO">Info — never interrupts</option>
            </Select>
          )}
        </Field>
        <Field label="Measured in" required>
          {(id) => (
            <Select id={id} value={key} onChange={(e) => setKey(e.target.value)}>
              <option value="days">Days</option>
              <option value="percent">Percent</option>
            </Select>
          )}
        </Field>
        <Field label="Fires at" required>
          {(id) => (
            <Input
              id={id}
              type="number"
              min={0}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}

function TaxRules() {
  const rules = useQuery({ queryKey: ["tax-rules"], queryFn: () => api.taxRules() });

  const columns: Column<TaxRule>[] = [
    { key: "treatment", header: "Treatment", render: (r) => r.treatment },
    {
      key: "rate",
      header: "Rate",
      numeric: true,
      render: (r) => `${(r.rate_basis_points / 100).toFixed(2)}%`,
    },
    { key: "from", header: "From", render: (r) => DAY.format(new Date(r.effective_from)) },
    {
      key: "to",
      header: "Until",
      render: (r) => (r.effective_to ? DAY.format(new Date(r.effective_to)) : "In force"),
    },
  ];

  if (rules.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <DatedNotice />
      <DataTable
        columns={columns}
        rows={rules.data?.results ?? []}
        rowKey={(r) => r.id}
        density="compact"
        caption="Tax rules"
        emptyHeading="No tax rules"
        emptyBody="A sale cannot price a standard-rated item without one."
      />
    </>
  );
}

function Quotas() {
  const quotas = useQuery({
    queryKey: ["controlled-quotas"],
    queryFn: () => api.controlledQuotas(),
  });

  const columns: Column<ControlledQuota>[] = [
    { key: "schedule", header: "Schedule", render: (q) => q.schedule },
    { key: "period", header: "Period", render: (q) => q.period.toLowerCase() },
    {
      key: "limit",
      header: "Limit",
      numeric: true,
      render: (q) => q.limit_base.toLocaleString(),
    },
    {
      key: "authority",
      header: "Authority",
      mono: true,
      render: (q) => q.authority_reference || "—",
    },
    { key: "from", header: "From", render: (q) => DAY.format(new Date(q.effective_from)) },
  ];

  if (quotas.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <DatedNotice />
      <DataTable
        columns={columns}
        rows={quotas.data?.results ?? []}
        rowKey={(q) => q.id}
        density="compact"
        caption="Controlled substance quotas"
        emptyHeading="No quotas recorded"
        emptyBody="No quota on file means the check does not apply."
      />
    </>
  );
}
