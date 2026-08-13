/* Invested against gained, for any period.
 *
 * Four tiles, four charts, one date range. **There is no net-profit
 * tile.** Net profit depends on depreciation, accruals and a tax
 * position this system does not see; a pharmacist who reads one and
 * files a return on it has been misled by us. The estimated operating
 * result is here instead, and it states what it excludes.
 *
 * See docs/28 §12 and docs/21-data-visualization.md.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Chart } from "@/components/charts/Chart";
import { StatTile, TileRow } from "@/components/charts/StatTile";
import { Field, PageHeader, Select, Skeleton, ErrorState } from "@/components/ui";
import { api, type DashboardPayload } from "@/lib/api";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

/** RWF has no minor unit, so the stored integer is already francs. */
function money(amount: number): string {
  return MONEY.format(amount);
}

/** Basis points to a percentage, or an em dash when there is no ratio. */
function ratio(bp: number | null): string {
  return bp === null ? "—" : `${(bp / 100).toFixed(1)}%`;
}

const RANGES = [
  { id: "30", label: "Last 30 days", days: 30 },
  { id: "90", label: "Last 90 days", days: 90 },
  { id: "180", label: "Last 6 months", days: 180 },
  { id: "365", label: "Last 12 months", days: 365 },
];

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export function PerformanceScreen({ canSupply }: { canSupply: boolean }) {
  const [rangeId, setRangeId] = useState("180");
  const [tier, setTier] = useState<"DEPOT" | "RETAIL">(
    canSupply ? "DEPOT" : "RETAIL",
  );

  const range = RANGES.find((r) => r.id === rangeId) ?? RANGES[2];
  const start = useMemo(() => isoDaysAgo(range.days), [range.days]);
  const end = new Date().toISOString().slice(0, 10);

  const dashboard = useQuery({
    queryKey: ["finance-dashboard", start, end, tier],
    queryFn: () => api.financeDashboard({ start, end, tier }),
  });

  if (dashboard.isPending) {
    return (
      <>
        <PageHeader title="Performance" description="Invested against gained." />
        <TileRow>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-[86px]" />
          ))}
        </TileRow>
        <Skeleton className="h-[280px]" />
      </>
    );
  }

  if (dashboard.isError) {
    return (
      <>
        <PageHeader title="Performance" />
        <ErrorState
          message="Could not load the period."
          onRetry={() => dashboard.refetch()}
        />
      </>
    );
  }

  const { report, trend, inventory_health, revenue_by_category, cash } =
    dashboard.data as DashboardPayload;

  return (
    <>
      <PageHeader
        title="Performance"
        description="Invested against gained."
        actions={
          /* Filters sit in one row above the charts, never inside them. */
          <div className="flex items-end gap-2">
            {canSupply && (
              <Field label="Side">
                {(id) => (
                  <Select
                    id={id}
                    value={tier}
                    onChange={(e) => setTier(e.target.value as "DEPOT" | "RETAIL")}
                  >
                    <option value="DEPOT">Wholesale</option>
                    <option value="RETAIL">Retail</option>
                  </Select>
                )}
              </Field>
            )}
            <Field label="Period">
              {(id) => (
                <Select
                  id={id}
                  value={rangeId}
                  onChange={(e) => setRangeId(e.target.value)}
                >
                  {RANGES.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
        }
      />

      <TileRow>
        <StatTile label="Total invested" value={money(report.capital_invested)} detail="RWF, landed cost included" />
        <StatTile label="Revenue" value={money(report.revenue)} detail="RWF, excluding tax" />
        <StatTile
          label="Gross profit"
          value={money(report.gross_profit)}
          detail={`${ratio(report.gross_margin_bp)} margin`}
          emphasis
        />
        <StatTile label="Return on investment" value={ratio(report.roi_bp)} detail="Gross profit over capital" />
      </TileRow>

      {/* Deliberately not a tile. It is an estimate, and it says so. */}
      <div className="mb-5 rounded-lg border border-border bg-content p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-label font-medium text-text-2">
            Estimated operating result
          </p>
          <p className="text-section font-semibold tabular-nums text-text">
            {money(report.estimated_operating_result)} RWF
          </p>
        </div>
        <p className="mt-1 text-help text-text-3">{report.estimated_basis}</p>
      </div>

      <div className="grid gap-4">
        {/* One axis. Both series are RWF, and a second scale would
            invent a difference that is not there. */}
        <Chart.Line
          title="Investment against revenue"
          data={trend}
          categoryKey="period"
          series={[
            { key: "invested", label: "Invested" },
            { key: "revenue", label: "Revenue" },
          ]}
          yUnit="RWF"
          format={money}
          emptyMessage="No trade recorded in this period."
        />

        <div className="grid gap-4 lg:grid-cols-2">
          {/* The one chart where the status ramp is correct rather than
              reserved: safe, slow-moving and expiring are statuses. */}
          <Chart.Bar
            title="Stock health"
            data={inventory_health}
            categoryKey="band"
            stacked
            series={[
              { key: "stable", label: "Stable", status: "ok" },
              { key: "slow", label: "Slow-moving", status: "warn" },
              { key: "expiring", label: "Expiring in 90 days", status: "bad" },
            ]}
            yUnit="RWF"
            format={money}
            emptyMessage="No stock held."
            height={220}
          />

          {/* Top three plus Other. Length carries the comparison. */}
          <Chart.Bar
            title="Revenue by category"
            data={revenue_by_category}
            categoryKey="category"
            horizontal
            series={[{ key: "amount", label: "Revenue" }]}
            format={money}
            emptyMessage="No sales in this period."
            height={220}
          />
        </div>

        {/* Sales can rise while cash falls. A single revenue line
            cannot show a business trading itself out of working capital. */}
        <Chart.Bar
          title="Invoiced against collected"
          data={cash}
          categoryKey="period"
          series={[
            { key: "invoiced", label: "Invoiced" },
            { key: "collected", label: "Collected" },
          ]}
          yUnit="RWF"
          format={money}
          emptyMessage="Nothing invoiced in this period."
        />
      </div>
    </>
  );
}
