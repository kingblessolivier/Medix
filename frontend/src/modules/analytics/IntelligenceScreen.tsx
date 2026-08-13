/* What makes money, and what is quietly costing it.
 *
 * The second half is the one nobody usually has. Stock that is held and
 * not selling does not appear in a sales report — by definition — and an
 * expiry report only catches it once it is nearly too late. So slow
 * movers and stock-outs sit beside the margin tables rather than in some
 * other screen nobody opens.
 *
 * Margin is exact: `SaleLine` holds the batch it came from and that
 * batch's cost, so these are real margins on real goods rather than
 * revenue against a moving average.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, TableTabs, type Column, type TableTab } from "@/components/data/DataTable";
import { Chart } from "@/components/charts/Chart";
import { Banner, Field, PageHeader, Select, Skeleton } from "@/components/ui";
import { api, type IntelligenceReport } from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

function money(amount: number): string {
  return MONEY.format(amount);
}

function margin(bp: number | null): string {
  return bp === null ? "—" : `${(bp / 100).toFixed(1)}%`;
}

const RANGES = [
  { id: "30", label: "Last 30 days", days: 30 },
  { id: "90", label: "Last 90 days", days: 90 },
  { id: "180", label: "Last 6 months", days: 180 },
  { id: "365", label: "Last 12 months", days: 365 },
];

const TABS: TableTab[] = [
  { id: "categories", label: "By category" },
  { id: "products", label: "By product" },
  { id: "movers", label: "Movers" },
  { id: "risk", label: "Not moving" },
];

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export function IntelligenceScreen() {
  const [rangeId, setRangeId] = useState("90");
  const [tab, setTab] = useState("categories");

  const range = RANGES.find((r) => r.id === rangeId) ?? RANGES[1];
  const start = useMemo(() => isoDaysAgo(range.days), [range.days]);
  const end = new Date().toISOString().slice(0, 10);

  const report = useQuery({
    queryKey: ["intelligence", start, end],
    queryFn: () => api.intelligence({ start, end }),
  });

  if (report.isPending) return <Skeleton className="h-[400px]" />;
  const data = report.data as IntelligenceReport;

  return (
    <>
      <PageHeader
        title="Intelligence"
        description="What makes money, and what is not moving."
        actions={
          <Field label="Period">
            {(id) => (
              <Select id={id} value={rangeId} onChange={(e) => setRangeId(e.target.value)}>
                {RANGES.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        }
      />

      <TableTabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "categories" && <Categories data={data} />}
      {tab === "products" && <Products data={data} />}
      {tab === "movers" && <Movers data={data} />}
      {tab === "risk" && <NotMoving data={data} />}
    </>
  );
}

function Categories({ data }: { data: IntelligenceReport }) {
  const columns: Column<IntelligenceReport["by_category"][number]>[] = [
    { key: "label", header: "Category", render: (r) => r.label },
    { key: "revenue", header: "Revenue", numeric: true, render: (r) => money(r.revenue) },
    { key: "cogs", header: "Cost", numeric: true, render: (r) => money(r.cogs) },
    {
      key: "profit",
      header: "Gross profit",
      numeric: true,
      render: (r) => money(r.gross_profit),
    },
    { key: "margin", header: "Margin", numeric: true, render: (r) => margin(r.margin_bp) },
  ];

  return (
    <>
      {/* Horizontal bar: length carries the comparison and colour carries
          nothing, which is what lets this show more than four rows. */}
      <div className="mb-4">
        <Chart.Bar
          title="Gross profit by category"
          data={data.by_category.slice(0, 8).map((r) => ({
            category: r.label,
            profit: r.gross_profit,
          }))}
          categoryKey="category"
          horizontal
          series={[{ key: "profit", label: "Gross profit" }]}
          format={money}
          emptyMessage="No sales in this period."
          height={260}
        />
      </div>
      <DataTable
        columns={columns}
        rows={data.by_category}
        rowKey={(r) => r.key || r.label}
        density="compact"
        caption="Margin by category"
        emptyHeading="No sales"
      />
    </>
  );
}

function Products({ data }: { data: IntelligenceReport }) {
  const columns: Column<IntelligenceReport["by_product"][number]>[] = [
    { key: "label", header: "Product", render: (r) => r.label },
    { key: "revenue", header: "Revenue", numeric: true, render: (r) => money(r.revenue) },
    {
      key: "profit",
      header: "Gross profit",
      numeric: true,
      render: (r) => money(r.gross_profit),
    },
    {
      key: "margin",
      header: "Margin",
      numeric: true,
      render: (r) => (
        <span className={r.margin_bp !== null && r.margin_bp < 0 ? "text-bad-text" : undefined}>
          {margin(r.margin_bp)}
        </span>
      ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={data.by_product}
      rowKey={(r) => r.key || r.label}
      density="compact"
      caption="Margin by product"
      emptyHeading="No sales"
      emptyBody="Top twenty by revenue appear here."
    />
  );
}

function Movers({ data }: { data: IntelligenceReport }) {
  const columns: Column<IntelligenceReport["best_sellers"][number]>[] = [
    { key: "name", header: "Product", render: (r) => r.name },
    { key: "units", header: "Units", numeric: true, render: (r) => r.units.toLocaleString() },
    { key: "sales", header: "Sales", numeric: true, render: (r) => r.sales.toLocaleString() },
    { key: "revenue", header: "Revenue", numeric: true, render: (r) => money(r.revenue) },
  ];

  return (
    <>
      {/* Ranked by units, not revenue: this is the stock question, and
          ranking by money would put one expensive item above a fast
          mover and mislead the reorder decision. */}
      <Banner tone="info" className="mb-3">
        Ranked by units leaving the shelf, not by revenue.
      </Banner>
      <DataTable
        columns={columns}
        rows={data.best_sellers}
        rowKey={(r) => r.product}
        density="compact"
        caption="Best sellers"
        emptyHeading="No sales"
      />
    </>
  );
}

function NotMoving({ data }: { data: IntelligenceReport }) {
  const slowColumns: Column<IntelligenceReport["slow_movers"][number]>[] = [
    { key: "name", header: "Product", render: (r) => r.name },
    { key: "held", header: "On hand", numeric: true, render: (r) => r.on_hand.toLocaleString() },
    { key: "sold", header: "Sold", numeric: true, render: (r) => r.sold.toLocaleString() },
    {
      key: "cover",
      header: "Cover",
      numeric: true,
      render: (r) =>
        r.cover_days === null ? (
          /* Never sold in the period. The worst case, and the one an
             average would have hidden. */
          <span className="text-bad-text">Never sold</span>
        ) : (
          `${r.cover_days.toLocaleString()} days`
        ),
    },
    { key: "value", header: "Capital held", numeric: true, render: (r) => money(r.value) },
  ];

  const outColumns: Column<IntelligenceReport["stock_outs"][number]>[] = [
    { key: "name", header: "Product", render: (r) => r.name },
    { key: "sold", header: "Sold in period", numeric: true, render: (r) => r.sold.toLocaleString() },
    { key: "on_hand", header: "On hand", numeric: true, render: () => "0" },
  ];

  return (
    <>
      <h3 className="mb-2 text-section font-semibold">Slow movers</h3>
      <DataTable
        columns={slowColumns}
        rows={data.slow_movers}
        rowKey={(r) => r.product}
        density="compact"
        caption="Slow-moving stock"
        emptyHeading="Nothing sitting still"
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">Stock-outs</h3>
      {/* Lost revenue leaves no trace in any sales figure — the sale
          simply did not happen — so it has to be inferred. */}
      <Banner tone="warn" className="mb-3">
        Sold in this period and now at zero. Lost sales leave no record.
      </Banner>
      <DataTable
        columns={outColumns}
        rows={data.stock_outs}
        rowKey={(r) => r.product}
        density="compact"
        caption="Stock-outs"
        emptyHeading="Nothing out of stock"
      />
    </>
  );
}
