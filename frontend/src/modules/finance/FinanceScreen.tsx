/* Costs, losses and what is owed.
 *
 * Three things a period report needs as inputs and cannot infer:
 * expenses, stock written off, and money not yet collected.
 *
 * Expenses are dated to when they were **incurred**, not when they were
 * keyed in — a November invoice entered in January belongs to November,
 * or the report records when somebody did their filing rather than what
 * the business did.
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
  type Expense,
  type ReceivablesAgeing,
  type WriteOff,
} from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const TABS: TableTab[] = [
  { id: "expenses", label: "Expenses" },
  { id: "writeoffs", label: "Write-offs" },
  { id: "receivables", label: "Receivables" },
];

export function FinanceScreen() {
  const [tab, setTab] = useState("expenses");
  const [adding, setAdding] = useState(false);

  return (
    <>
      <PageHeader
        title="Finance"
        description="Costs, losses and what is owed."
        actions={
          tab === "expenses" ? (
            <Button
              variant="primary"
              icon={<Plus size={16} strokeWidth={1.9} />}
              onClick={() => setAdding(true)}
            >
              Record expense
            </Button>
          ) : undefined
        }
      />
      <TableTabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "expenses" && <Expenses />}
      {tab === "writeoffs" && <WriteOffs />}
      {tab === "receivables" && <Receivables />}

      <ExpenseModal open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

/* -- expenses ---------------------------------------------------------- */

const EXPENSE_COLUMNS: Column<Expense>[] = [
  { key: "incurred", header: "Incurred", render: (e) => DAY.format(new Date(e.incurred_on)) },
  { key: "category", header: "Category", render: (e) => e.category_name },
  { key: "payee", header: "Payee", render: (e) => e.payee || "—" },
  { key: "description", header: "Description", render: (e) => e.description || "—" },
  {
    key: "amount",
    header: "Amount",
    numeric: true,
    render: (e) => MONEY.format(e.amount),
  },
];

function Expenses() {
  const expenses = useQuery({ queryKey: ["expenses"], queryFn: () => api.expenses() });
  if (expenses.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <DataTable
      columns={EXPENSE_COLUMNS}
      rows={expenses.data?.results ?? []}
      rowKey={(e) => e.id}
      density="compact"
      caption="Recorded expenses"
      emptyHeading="No expenses"
      emptyBody="Rent, salaries and transport belong here."
    />
  );
}

function ExpenseModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState("");
  const [amount, setAmount] = useState("");
  const [incurredOn, setIncurredOn] = useState(
    () => new Date().toISOString().slice(0, 10),
  );
  const [payee, setPayee] = useState("");
  const [description, setDescription] = useState("");
  const [failure, setFailure] = useState("");

  const categories = useQuery({
    queryKey: ["expense-categories"],
    queryFn: () => api.expenseCategories(),
    enabled: open,
  });

  const record = useMutation({
    mutationFn: () =>
      api.recordExpense({
        category,
        amount: Number(amount),
        incurred_on: incurredOn,
        payee,
        description,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["expenses"] });
      queryClient.invalidateQueries({ queryKey: ["finance-dashboard"] });
      setAmount("");
      setPayee("");
      setDescription("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not saved."),
  });

  if (!open) return null;
  const options = categories.data?.results ?? [];
  const ready = category && Number(amount) > 0;

  return (
    <Modal
      open
      title="Record expense"
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={record.isPending}
          onClick={() => record.mutate()}
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
        <Field label="Category" required>
          {(id) => (
            <Select
              id={id}
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">Choose</option>
              {options.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Amount" help="RWF" required>
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          )}
        </Field>

        <Field label="Incurred" help="The date the cost arose, not today." required>
          {(id) => (
            <Input
              id={id}
              type="date"
              value={incurredOn}
              onChange={(e) => setIncurredOn(e.target.value)}
            />
          )}
        </Field>

        <Field label="Payee">
          {(id) => (
            <Input id={id} value={payee} onChange={(e) => setPayee(e.target.value)} />
          )}
        </Field>

        <Field label="Description">
          {(id) => (
            <Input
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}

/* -- write-offs -------------------------------------------------------- */

const WRITE_OFF_COLUMNS: Column<WriteOff>[] = [
  { key: "number", header: "Number", render: (w) => <span className="font-mono">{w.number}</span> },
  { key: "date", header: "Date", render: (w) => DAY.format(new Date(w.written_off_on)) },
  { key: "product", header: "Product", render: (w) => w.product_name },
  { key: "batch", header: "Batch", render: (w) => <span className="font-mono">{w.batch_number}</span> },
  {
    key: "reason",
    header: "Reason",
    render: (w) => (
      <Badge tone={w.reason === "EXPIRY" ? "warn" : "bad"}>{w.reason_label}</Badge>
    ),
  },
  { key: "quantity", header: "Units", numeric: true, render: (w) => MONEY.format(w.quantity_base) },
  { key: "value", header: "Value", numeric: true, render: (w) => MONEY.format(w.value) },
];

function WriteOffs() {
  const writeOffs = useQuery({ queryKey: ["write-offs"], queryFn: () => api.writeOffs() });
  if (writeOffs.isPending) return <Skeleton className="h-[300px]" />;

  const rows = writeOffs.data?.results ?? [];
  const lost = rows.reduce((sum, row) => sum + row.value, 0);

  return (
    <>
      {lost > 0 && (
        <Banner tone="warn" className="mb-3">
          {`${MONEY.format(lost)} RWF written off. Every certificate is in Documents.`}
        </Banner>
      )}
      <DataTable
        columns={WRITE_OFF_COLUMNS}
        rows={rows}
        rowKey={(w) => w.id}
        density="compact"
        caption="Stock written off"
        emptyHeading="Nothing written off"
        emptyBody="Expired and damaged stock is recorded here."
      />
    </>
  );
}

/* -- receivables ------------------------------------------------------- */

function Receivables() {
  const ageing = useQuery({
    queryKey: ["receivables"],
    queryFn: () => api.receivables(),
  });
  if (ageing.isPending) return <Skeleton className="h-[300px]" />;

  const data = ageing.data as ReceivablesAgeing;
  const buckets = Object.keys(data.buckets);

  /* Ageing is a table rather than a chart. Four buckets across a handful
     of customers is a lookup — "who owes what, how late" — and a reader
     answering that needs the number, not its length. */
  const columns: Column<(typeof data.customers)[number]>[] = [
    { key: "customer", header: "Customer", render: (row) => String(row.customer) },
    ...buckets.map((bucket) => ({
      key: bucket,
      header: bucket === "91+" ? "90+ days" : `${bucket} days`,
      numeric: true,
      render: (row: (typeof data.customers)[number]) =>
        Number(row[bucket]) > 0 ? MONEY.format(Number(row[bucket])) : "—",
    })),
    {
      key: "total",
      header: "Total",
      numeric: true,
      render: (row) => MONEY.format(Number(row.total)),
    },
  ];

  return (
    <>
      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {buckets.map((bucket) => (
          <div key={bucket} className="rounded-lg border border-border bg-surface p-3">
            <p className="text-label font-medium text-text-2">
              {bucket === "91+" ? "90+ days" : `${bucket} days`}
            </p>
            <p className="mt-1 text-section font-semibold tabular-nums text-text">
              {MONEY.format(data.buckets[bucket])}
            </p>
          </div>
        ))}
        <div className="rounded-lg border border-brand bg-surface p-3">
          <p className="text-label font-medium text-text-2">Outstanding</p>
          <p className="mt-1 text-section font-semibold tabular-nums text-text">
            {MONEY.format(data.total)}
          </p>
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={data.customers}
        rowKey={(row) => String(row.customer)}
        density="compact"
        caption="Receivables by customer and age"
        emptyHeading="Nothing outstanding"
        emptyBody="Every issued invoice has been paid."
      />
    </>
  );
}
