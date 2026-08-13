/* Goods coming back, from a customer or to a supplier.
 *
 * The decision this screen exists to force is **restock or not**. Medicine
 * that has left the premises may not be resaleable — the cold chain is
 * unknown, the pack may have been opened, the patient may have stored it
 * anywhere. Defaulting to "put it back" is how an unsafe pack re-enters
 * supply, so the choice is explicit and neither option is preselected.
 *
 * A refused restock still records the return. The sale is reversed either
 * way; only the stock movement differs.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import {
  DataTable,
  TableTabs,
  type Column,
  type TableTab,
} from "@/components/data/DataTable";
import {
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { ApiFailure, api, type Sale, type SaleLine, type StockRow } from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

const TABS: TableTab[] = [
  { id: "customer", label: "From a customer" },
  { id: "supplier", label: "To a supplier" },
];

export function ReturnsScreen() {
  const [tab, setTab] = useState("customer");
  return (
    <>
      <PageHeader title="Returns" description="Goods coming back." />
      <TableTabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "customer" ? <CustomerReturns /> : <SupplierReturns />}
    </>
  );
}

/* -- from a customer ---------------------------------------------------- */

function CustomerReturns() {
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<{ sale: Sale; line: SaleLine } | null>(null);

  const sales = useQuery({ queryKey: ["sales"], queryFn: () => api.sales() });

  const term = search.trim().toLowerCase();
  const rows = (sales.data?.results ?? [])
    .filter((s) => s.status === "COMPLETED")
    .flatMap((sale) => sale.lines.map((line) => ({ sale, line })))
    .filter(
      (row) =>
        !term ||
        row.sale.number.toLowerCase().includes(term) ||
        row.line.product_name.toLowerCase().includes(term),
    );

  const columns: Column<(typeof rows)[number]>[] = [
    { key: "sale", header: "Sale", mono: true, render: (r) => r.sale.number },
    { key: "product", header: "Product", render: (r) => r.line.product_name },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.line.batch_number },
    {
      key: "qty",
      header: "Dispensed",
      numeric: true,
      render: (r) => `${r.line.quantity} ${r.line.uom_code.toLowerCase()}`,
    },
    {
      key: "value",
      header: "Value",
      numeric: true,
      render: (r) => MONEY.format(r.line.line_total),
    },
  ];

  if (sales.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <div className="mb-3 max-w-md">
        <Field label="Find a sale">
          {(id) => (
            <Input
              id={id}
              icon={Search}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Sale or product"
            />
          )}
        </Field>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.line.id}
        density="compact"
        onRowClick={setChosen}
        caption="Dispensed lines"
        emptyHeading="Nothing dispensed"
        emptyBody="Completed sales appear here."
      />

      <CustomerReturnModal chosen={chosen} onClose={() => setChosen(null)} />
    </>
  );
}

function CustomerReturnModal({
  chosen,
  onClose,
}: {
  chosen: { sale: Sale; line: SaleLine } | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [restock, setRestock] = useState("");
  const [failure, setFailure] = useState("");

  const take = useMutation({
    mutationFn: () =>
      api.returnSaleLine({
        sale_line: chosen!.line.id,
        quantity: Number(quantity),
        uom_code: chosen!.line.uom_code,
        reason,
        restock: restock === "yes",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sales"] });
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      setQuantity("");
      setReason("");
      setRestock("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not returned."),
  });

  if (!chosen) return null;
  const count = Number(quantity);
  const ready =
    count > 0 && count <= chosen.line.quantity && reason.trim() && restock !== "";

  return (
    <Modal
      open
      title="Return from customer"
      subtitle={`${chosen.line.product_name} · ${chosen.sale.number}`}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={take.isPending}
          onClick={() => take.mutate()}
        >
          Record return
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <div className="flex flex-col gap-4">
        <Field
          label="Quantity"
          help={`${chosen.line.quantity} ${chosen.line.uom_code.toLowerCase()} dispensed`}
          required
        >
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={chosen.line.quantity}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>

        {/* Nothing preselected. This is a judgement about whether the
            pack is still safe to dispense, and a default would make it
            for the pharmacist. */}
        <Field
          label="Restock"
          help="Only if the pack is intact and its storage is known."
          required
        >
          {(id) => (
            <Select id={id} value={restock} onChange={(e) => setRestock(e.target.value)}>
              <option value="">Choose</option>
              <option value="no">No — destroy or quarantine</option>
              <option value="yes">Yes — return to the batch</option>
            </Select>
          )}
        </Field>

        <Field label="Reason" required>
          {(id) => (
            <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Field>
      </div>
    </Modal>
  );
}

/* -- to a supplier ------------------------------------------------------ */

function SupplierReturns() {
  const [chosen, setChosen] = useState<StockRow | null>(null);
  const stock = useQuery({ queryKey: ["stock"], queryFn: () => api.stock() });

  /* Quarantined stock is shown here on purpose — the common supplier
     return is something held on arrival, and forcing a release first
     would push unusable stock through available on its way out. */
  const rows = (stock.data?.results ?? []).filter((r) => r.quantity_base > 0);

  const columns: Column<StockRow>[] = [
    { key: "product", header: "Product", render: (r) => r.product_name },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.batch_number },
    { key: "location", header: "Location", render: (r) => r.location_name },
    { key: "status", header: "Status", render: (r) => r.status.toLowerCase() },
    {
      key: "qty",
      header: "Units",
      numeric: true,
      render: (r) => r.quantity_base.toLocaleString(),
    },
    { key: "expiry", header: "Expires", render: (r) => DAY.format(new Date(r.expiry_date)) },
  ];

  if (stock.isPending) return <Skeleton className="h-[300px]" />;

  return (
    <>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        density="compact"
        onRowClick={setChosen}
        caption="Stock that could go back"
        emptyHeading="Nothing held"
      />
      <SupplierReturnModal row={chosen} onClose={() => setChosen(null)} />
    </>
  );
}

function SupplierReturnModal({
  row,
  onClose,
}: {
  row: StockRow | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState("");

  const send = useMutation({
    mutationFn: () =>
      api.returnToSupplier({
        batch: row!.batch,
        location: row!.location,
        quantity: Number(quantity),
        reason,
        status: row!.status,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      setQuantity("");
      setReason("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not returned."),
  });

  if (!row) return null;
  const count = Number(quantity);
  const ready = count > 0 && count <= row.quantity_base && reason.trim().length > 0;

  return (
    <Modal
      open
      title="Return to supplier"
      subtitle={`${row.product_name} · ${row.batch_number}`}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={send.isPending}
          onClick={() => send.mutate()}
        >
          Send back
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <div className="flex flex-col gap-4">
        <Field
          label="Units"
          help={`${row.quantity_base.toLocaleString()} ${row.status.toLowerCase()}`}
          required
        >
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={row.quantity_base}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>
        <Field label="Reason" required>
          {(id) => (
            <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Field>
      </div>
    </Modal>
  );
}
