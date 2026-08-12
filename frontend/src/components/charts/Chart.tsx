/* The chart wrapper. No screen touches Recharts directly.
 *
 * This owns tokens, the palette by slot, tooltips, the legend, the table
 * view and the empty state. **A screen never passes a colour in** — that
 * is what guarantees colour follows the entity rather than its rank, so
 * filtering a series out does not repaint the survivors.
 *
 * Every chart carries a table view. It is the accessibility fallback and
 * the answer to any contrast complaint, and it is not optional.
 *
 * Banned by construction: there is no pie, no radar, and no second
 * Y axis. Two measures of different scale are two charts.
 *
 * See docs/21-data-visualization.md.
 */

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Table2, ChartNoAxesColumn } from "lucide-react";
import { Button, EmptyState } from "@/components/ui";
import { DataTable, type Column } from "@/components/data/DataTable";
import { chartTokens, slotBudget } from "./palette";

export type Series = {
  /** Key into each datum. */
  key: string;
  label: string;
  /** Status role, for the one chart where the ramp is the meaning. */
  status?: "ok" | "warn" | "bad" | "neutral";
};

type Datum = Record<string, string | number>;

type Common = {
  data: Datum[];
  series: Series[];
  /** Category key on the x axis. */
  categoryKey: string;
  title: string;
  /** Axis labels state the unit: "RWF", "packs", "days to expiry". */
  yUnit?: string;
  /** Required. A chart with no data must say something specific. */
  emptyMessage: string;
  /** Formats values in tooltips, the table and axis ticks. */
  format?: (value: number) => string;
  height?: number;
};

const DEFAULT_FORMAT = new Intl.NumberFormat("en-RW", {
  maximumFractionDigits: 0,
});

/* -- shell ------------------------------------------------------------- */

function Shell({
  title,
  data,
  series,
  categoryKey,
  emptyMessage,
  format,
  children,
}: Common & { children: React.ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  const fmt = format ?? ((value: number) => DEFAULT_FORMAT.format(value));

  const columns = useMemo<Column<Datum>[]>(
    () => [
      {
        key: categoryKey,
        header: "Period",
        render: (row) => String(row[categoryKey]),
      },
      ...series.map((s) => ({
        key: s.key,
        header: s.label,
        numeric: true,
        render: (row: Datum) => fmt(Number(row[s.key] ?? 0)),
      })),
    ],
    [categoryKey, series, format],
  );

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <header className="mb-3 flex items-start justify-between gap-3">
        <h3 className="text-section font-semibold text-text">{title}</h3>
        <Button
          variant="tertiary"
          onClick={() => setAsTable((on) => !on)}
          icon={
            asTable ? (
              <ChartNoAxesColumn size={16} strokeWidth={1.9} />
            ) : (
              <Table2 size={16} strokeWidth={1.9} />
            )
          }
        >
          {asTable ? "Chart" : "Table"}
        </Button>
      </header>

      {data.length === 0 ? (
        <EmptyState heading="No data" body={emptyMessage} />
      ) : asTable ? (
        <DataTable
          columns={columns}
          rows={data}
          rowKey={(row) => String(row[categoryKey])}
          density="compact"
          caption={title}
        />
      ) : (
        children
      )}
    </section>
  );
}

/* Tooltip and axis text wear text tokens, never the series colour. A
   coloured swatch beside a label carries identity; the label itself
   stays legible ink. */
function ChartTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: string;
  format: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 shadow-sm">
      <p className="mb-1 text-help font-semibold text-text">{label}</p>
      {payload.map((entry) => (
        <p key={entry.name} className="flex items-center gap-2 text-help text-text-2">
          <span
            aria-hidden
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: entry.color }}
          />
          <span className="flex-1">{entry.name}</span>
          <span className="tabular-nums text-text">{format(entry.value)}</span>
        </p>
      ))}
    </div>
  );
}

function axisProps(tokens: ReturnType<typeof chartTokens>) {
  return {
    stroke: tokens.axis,
    tick: { fill: tokens.muted, fontSize: 11 },
    tickLine: false,
  };
}

/** Series colour by slot, or the status ramp where state is the meaning. */
function colourFor(index: number, s: Series, tokens: ReturnType<typeof chartTokens>) {
  if (s.status) return tokens.status[s.status];
  return tokens.series[index % tokens.series.length];
}

function assertBudget(series: Series[], title: string) {
  const categorical = series.filter((s) => !s.status);
  if (categorical.length > slotBudget() && import.meta.env.DEV) {
    // Louder than a silent recycle. A fifth series means change the
    // form — fold into Other, facet, or use small multiples.
    console.warn(
      `[chart] "${title}" has ${categorical.length} categorical series; ` +
        `${slotBudget()} slots are validated for this theme. Fold the tail into "Other".`,
    );
  }
}

/* -- line -------------------------------------------------------------- */

/** One axis. Two measures of different scale are two charts. */
export function ChartLine(props: Common) {
  const tokens = chartTokens();
  const fmt = props.format ?? ((v: number) => DEFAULT_FORMAT.format(v));
  assertBudget(props.series, props.title);

  return (
    <Shell {...props}>
      <ResponsiveContainer width="100%" height={props.height ?? 240}>
        <LineChart data={props.data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          {/* Horizontal only, behind the marks. */}
          <CartesianGrid stroke={tokens.grid} vertical={false} />
          <XAxis dataKey={props.categoryKey} {...axisProps(tokens)} />
          <YAxis
            {...axisProps(tokens)}
            width={64}
            tickFormatter={(v) => fmt(Number(v))}
            label={
              props.yUnit
                ? {
                    value: props.yUnit,
                    angle: -90,
                    position: "insideLeft",
                    style: { fill: tokens.muted, fontSize: 11 },
                  }
                : undefined
            }
          />
          <Tooltip
            cursor={{ stroke: tokens.axis }}
            content={<ChartTooltip format={fmt} />}
          />
          {props.series.length > 1 && (
            <Legend
              iconType="circle"
              wrapperStyle={{ fontSize: 12, color: tokens.muted }}
            />
          )}
          {props.series.map((s, i) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={colourFor(i, s, tokens)}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: tokens.surface }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Shell>
  );
}

/* -- bars -------------------------------------------------------------- */

export function ChartBar(props: Common & { stacked?: boolean; horizontal?: boolean }) {
  const tokens = chartTokens();
  const fmt = props.format ?? ((v: number) => DEFAULT_FORMAT.format(v));
  assertBudget(props.series, props.title);

  return (
    <Shell {...props}>
      <ResponsiveContainer width="100%" height={props.height ?? 240}>
        <BarChart
          data={props.data}
          layout={props.horizontal ? "vertical" : "horizontal"}
          margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
        >
          <CartesianGrid stroke={tokens.grid} vertical={props.horizontal} horizontal={!props.horizontal} />
          {props.horizontal ? (
            <>
              <XAxis type="number" {...axisProps(tokens)} tickFormatter={(v) => fmt(Number(v))} />
              <YAxis type="category" dataKey={props.categoryKey} width={120} {...axisProps(tokens)} />
            </>
          ) : (
            <>
              <XAxis dataKey={props.categoryKey} {...axisProps(tokens)} />
              <YAxis
                {...axisProps(tokens)}
                width={64}
                tickFormatter={(v) => fmt(Number(v))}
                label={
                  props.yUnit
                    ? {
                        value: props.yUnit,
                        angle: -90,
                        position: "insideLeft",
                        style: { fill: tokens.muted, fontSize: 11 },
                      }
                    : undefined
                }
              />
            </>
          )}
          <Tooltip
            cursor={{ fill: tokens.grid, fillOpacity: 0.4 }}
            content={<ChartTooltip format={fmt} />}
          />
          {props.series.length > 1 && (
            <Legend iconType="circle" wrapperStyle={{ fontSize: 12, color: tokens.muted }} />
          )}
          {props.series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stackId={props.stacked ? "one" : undefined}
              fill={colourFor(i, s, tokens)}
              // 4px rounded on the data end only, square at the baseline.
              radius={props.stacked ? 0 : props.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
              // A 2px surface gap between adjacent and stacked marks, so
              // two segments never read as one.
              stroke={tokens.surface}
              strokeWidth={props.stacked ? 2 : 0}
            >
              {props.stacked &&
                props.data.map((_, index) => (
                  <Cell key={index} fill={colourFor(i, s, tokens)} />
                ))}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </Shell>
  );
}

export const Chart = { Line: ChartLine, Bar: ChartBar };
