/* Inventory — list template against live data.
 *
 * Row click opens a drawer showing the batch and its ledger history. The
 * ledger is the screen an inspector is shown, so it reads as a register:
 * date, event, in, out, balance, reference.
 *
 * See docs/19-screens.md §6 and §7.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, type Movement, type StockRow } from "@/lib/api";
import { DataTable, DataToolbar, type Column, type Density } from "@/components/data/DataTable";
import { Button, ErrorState, PageHeader, StatusDot, type Tone } from "@/components/ui";
import { DetailList, Drawer } from "@/components/ui/Drawer";

/** Expiry banding. Status is never colour alone — the dot carries a label. */
function expiryTone(days: number): { tone: Tone; label: string } {
  if (days <= 0) return { tone: "bad", label: "Expired" };
  if (days <= 30) return { tone: "bad", label: "Critical" };
  if (days <= 90) return { tone: "warn", label: "Expiring" };
  if (days <= 180) return { tone: "warn", label: "Watch" };
  return { tone: "ok", label: "Healthy" };
}

const MOVEMENT_LABEL: Record<string, string> = {
  OPENING: "Opening",
  PURCHASE_RECEIPT: "Purchase",
  SALE: "Sale",
  SALE_RETURN: "Return",
  TRANSFER_OUT: "Transfer out",
  TRANSFER_IN: "Transfer in",
  ADJUSTMENT: "Adjustment",
  DISPOSAL: "Disposal",
  QUARANTINE: "Quarantine",
  RELEASE: "Release",
  RECALL: "Recall",
  EXPIRY_WRITE_OFF: "Expiry write-off",
  SUPPLIER_RETURN: "Supplier return",
};

export function InventoryScreen() {
  const [density, setDensity] = useState<Density>("compact");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<StockRow | null>(null);

  const stock = useQuery({
    queryKey: ["stock"],
    queryFn: () => api.stock(),
  });

  const rows = useMemo(() => {
    const all = stock.data?.results ?? [];
    if (!search.trim()) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (r) =>
        r.product_name.toLowerCase().includes(needle) ||
        r.batch_number.toLowerCase().includes(needle),
    );
  }, [stock.data, search]);

  const columns: Column<StockRow>[] = [
    {
      key: "product",
      header: "Product",
      sortable: true,
      render: (r) => r.product_name,
      sortValue: (r) => r.product_name,
    },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.batch_number },
    {
      key: "expiry",
      header: "Expiry",
      sortable: true,
      render: (r) => formatMonth(r.expiry_date),
      sortValue: (r) => r.expiry_date,
    },
    {
      key: "qty",
      header: "Qty",
      numeric: true,
      sortable: true,
      render: (r) => r.quantity_base.toLocaleString(),
      sortValue: (r) => r.quantity_base,
    },
    { key: "location", header: "Location", render: (r) => r.location_name },
    {
      key: "status",
      header: "Status",
      render: (r) => {
        const { tone, label } = expiryTone(r.days_to_expiry);
        return <StatusDot tone={tone}>{label}</StatusDot>;
      },
    },
  ];

  if (stock.isError) {
    return (
      <>
        <PageHeader title="Inventory" description="Stock, batches, expiry" />
        <ErrorState message="Couldn't load stock." onRetry={() => stock.refetch()} />
      </>
    );
  }

  const bands = countBands(stock.data?.results ?? []);

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Stock, batches, expiry"
        actions={<Button variant="primary">Adjust stock</Button>}
      />

      {/* Borderless, one hairline beneath. Never cards. */}
      <div className="mb-4 flex flex-wrap gap-8 border-b border-hair pb-4">
        <Metric label="Batches" value={stock.data?.count ?? 0} />
        <Metric label="Critical" value={bands.critical} tone="text-bad-text" />
        <Metric label="Expiring" value={bands.expiring} tone="text-warn-text" />
        <Metric label="Healthy" value={bands.healthy} tone="text-ok-text" />
      </div>

      <DataToolbar
        search={search}
        onSearch={setSearch}
        density={density}
        onDensity={setDensity}
        right={<Button>Export</Button>}
      />

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        density={density}
        loading={stock.isLoading}
        caption="Stock balances by expiry"
        onRowClick={setSelected}
        emptyHeading={search ? `No results for "${search}"` : "No stock"}
        emptyAction={search ? undefined : <Button variant="primary">Receive stock</Button>}
      />

      <BatchDrawer row={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <p className="text-help text-text-2">{label}</p>
      <p className={`text-metric font-semibold tabular ${tone ?? ""}`}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function BatchDrawer({ row, onClose }: { row: StockRow | null; onClose: () => void }) {
  const movements = useQuery({
    queryKey: ["movements", row?.batch],
    queryFn: () => api.movements(`?batch=${row!.batch}`),
    enabled: Boolean(row),
  });

  if (!row) return null;
  const { tone, label } = expiryTone(row.days_to_expiry);

  return (
    <Drawer
      open
      title={row.product_name}
      subtitle={`Batch ${row.batch_number}`}
      onClose={onClose}
      footer={<Button className="w-full">View full history</Button>}
    >
      <DetailList
        rows={[
          ["Quantity", row.quantity_base.toLocaleString()],
          ["Expiry", formatMonth(row.expiry_date)],
          ["Days remaining", row.days_to_expiry.toLocaleString()],
          ["Status", <StatusDot tone={tone}>{label}</StatusDot>],
          ["Location", row.location_name],
          ["Stock status", row.status.toLowerCase()],
        ]}
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">Movements</h3>
      {movements.isLoading ? (
        <p className="text-body text-text-2">Loading…</p>
      ) : (movements.data?.results.length ?? 0) === 0 ? (
        <p className="text-body text-text-2">No movements</p>
      ) : (
        <Ledger movements={movements.data!.results} />
      )}
    </Drawer>
  );
}

/** The ledger, readable. This is the view an inspector is shown. */
function Ledger({ movements }: { movements: Movement[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-content">
            <th className="px-2 py-1.5 text-left text-label font-semibold text-text-2">Date</th>
            <th className="px-2 py-1.5 text-left text-label font-semibold text-text-2">Event</th>
            <th className="px-2 py-1.5 text-right text-label font-semibold text-text-2">In</th>
            <th className="px-2 py-1.5 text-right text-label font-semibold text-text-2">Out</th>
            <th className="px-2 py-1.5 text-right text-label font-semibold text-text-2">Balance</th>
          </tr>
        </thead>
        <tbody>
          {movements.map((m) => (
            <tr key={m.id} className="border-t border-hair">
              <td className="px-2 py-1.5 text-body text-text-2">{formatDay(m.occurred_at)}</td>
              <td className="px-2 py-1.5 text-body">
                {MOVEMENT_LABEL[m.kind] ?? m.kind}
                {m.reference && (
                  <span className="ml-1 font-mono text-help text-text-3">{m.reference}</span>
                )}
              </td>
              <td className="px-2 py-1.5 text-right text-body tabular">
                {m.quantity_base > 0 ? m.quantity_base.toLocaleString() : ""}
              </td>
              <td className="px-2 py-1.5 text-right text-body tabular">
                {m.quantity_base < 0 ? Math.abs(m.quantity_base).toLocaleString() : ""}
              </td>
              <td className="px-2 py-1.5 text-right text-body tabular font-medium">
                {m.balance_after_base.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function countBands(rows: StockRow[]) {
  let critical = 0;
  let expiring = 0;
  let healthy = 0;
  for (const r of rows) {
    const { tone } = expiryTone(r.days_to_expiry);
    if (tone === "bad") critical += 1;
    else if (tone === "warn") expiring += 1;
    else healthy += 1;
  }
  return { critical, expiring, healthy };
}

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric" });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });

function formatMonth(iso: string): string {
  return MONTH.format(new Date(iso));
}

function formatDay(iso: string): string {
  return DAY.format(new Date(iso));
}
