/* Pharmacist overview — an operational workspace, not a dashboard.
 *
 * Demonstrates the shell and the design system against real-shaped data.
 * The KPI strip is borderless; the table gets the only true surface.
 *
 * See docs/19-screens.md.
 */

import { Package } from "lucide-react";
import { useState } from "react";

import { DataTable, DataToolbar, Pagination, type Column, type Density } from "@/components/data/DataTable";
import { Button, Card, PageHeader, StatusDot, type Tone } from "@/components/ui";
import { AppShell } from "@/components/navigation/AppShell";

type Batch = {
  id: string;
  product: string;
  batch: string;
  expiry: string;
  expiryAt: number;
  quantity: number;
  location: string;
  status: { tone: Tone; label: string };
};

const BATCHES: Batch[] = [
  { id: "1", product: "Cetirizine 10mg", batch: "CTZ-4421", expiry: "Sep 2026", expiryAt: 20260901, quantity: 28, location: "Kigali Main", status: { tone: "bad", label: "Critical" } },
  { id: "2", product: "Amoxicillin 500mg", batch: "AMX-0021", expiry: "Apr 2027", expiryAt: 20270401, quantity: 240, location: "Kigali Main", status: { tone: "warn", label: "Expiring" } },
  { id: "3", product: "Paracetamol 500mg", batch: "PCM-1022", expiry: "Jun 2028", expiryAt: 20280601, quantity: 820, location: "Kigali Main", status: { tone: "ok", label: "Healthy" } },
  { id: "4", product: "Insulin XYZ 100IU", batch: "INS-0084", expiry: "Jan 2027", expiryAt: 20270101, quantity: 42, location: "Cold room", status: { tone: "warn", label: "Expiring" } },
  { id: "5", product: "Surgical gloves", batch: "GLV-2210", expiry: "Dec 2029", expiryAt: 20291201, quantity: 460, location: "Kigali Main", status: { tone: "ok", label: "Healthy" } },
];

const COLUMNS: Column<Batch>[] = [
  { key: "product", header: "Product", sortable: true, render: (r) => r.product, sortValue: (r) => r.product },
  { key: "batch", header: "Batch", mono: true, render: (r) => r.batch },
  { key: "expiry", header: "Expiry", sortable: true, render: (r) => r.expiry, sortValue: (r) => r.expiryAt },
  { key: "quantity", header: "Qty", numeric: true, sortable: true, render: (r) => r.quantity.toLocaleString(), sortValue: (r) => r.quantity },
  { key: "location", header: "Location", render: (r) => r.location },
  { key: "status", header: "Status", render: (r) => <StatusDot tone={r.status.tone}>{r.status.label}</StatusDot> },
];

const METRICS = [
  { label: "Sales today", value: "1.84M", delta: "↑ 12.4%", tone: "text-ok" },
  { label: "Orders", value: "24", delta: "↑ 8.2%", tone: "text-ok" },
  { label: "Low stock", value: "18", delta: "Needs review", tone: "text-warn" },
  { label: "Expiry alerts", value: "32", delta: "12 critical", tone: "text-bad" },
];

export default function App() {
  const [active, setActive] = useState("inventory");
  const [density, setDensity] = useState<Density>("compact");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const rows = BATCHES.filter(
    (b) =>
      b.product.toLowerCase().includes(search.toLowerCase()) ||
      b.batch.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <AppShell active={active} onNavigate={setActive}>
      <PageHeader
        title="Inventory"
        description="Stock, batches, expiry"
        actions={<Button variant="primary">Adjust stock</Button>}
      />

      {/* Borderless KPI strip, one hairline beneath. Never cards. */}
      <div className="mb-4 flex flex-wrap gap-8 border-b border-hair pb-4">
        {METRICS.map((m) => (
          <div key={m.label}>
            <p className="text-help text-text-2">{m.label}</p>
            <p className="text-metric font-semibold tabular">{m.value}</p>
            <p className={`text-help ${m.tone}`}>{m.delta}</p>
          </div>
        ))}
      </div>

      <DataToolbar
        search={search}
        onSearch={(v) => {
          setSearch(v);
          setPage(1);
        }}
        density={density}
        onDensity={setDensity}
        right={<Button>Export</Button>}
      />

      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(r) => r.id}
        density={density}
        caption="Stock batches by expiry"
        onRowClick={() => {}}
        emptyHeading={search ? `No results for "${search}"` : "No batches"}
        emptyAction={search ? undefined : <Button variant="primary">Receive stock</Button>}
      />

      <Pagination page={page} pageSize={50} total={rows.length} onPage={setPage} />

      <section className="mt-8">
        <h2 className="mb-2 text-section font-semibold">Needs attention</h2>
        <Card className="divide-y divide-hair">
          <Row tone="bad" text="18 products expiring within 30 days" />
          <Row tone="warn" text="6 supplier invoices overdue" />
          <Row tone="bad" text="Stock shortage on 7 fast-moving products" />
        </Card>
      </section>
    </AppShell>
  );
}

function Row({ tone, text }: { tone: Tone; text: string }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <StatusDot tone={tone}>
        <span className="text-text">{text}</span>
      </StatusDot>
      <Package size={15} strokeWidth={1.8} className="ml-auto text-text-3" />
    </div>
  );
}
