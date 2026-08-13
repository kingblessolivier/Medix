/* The landing page.
 *
 * It answers one question — what needs me today — and it is composed of
 * things that are already true elsewhere rather than of new figures. A
 * landing page with its own arithmetic is a landing page that disagrees
 * with the screen it summarises.
 *
 * Different for a depot and a retail pharmacy, because the work is
 * different: a depot's day is orders waiting to be picked, a pharmacy's
 * is stock about to expire.
 */

import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ClipboardList,
  PackageCheck,
  ShoppingCart,
  Truck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { api } from "@/lib/api";
import { AlertStack } from "@/components/ui/AlertStack";
import { Button, PageHeader, Skeleton } from "@/components/ui";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export function OverviewScreen({
  canSupply,
  canSell,
  onNavigate,
}: {
  canSupply: boolean;
  canSell: boolean;
  onNavigate: (id: string) => void;
}) {
  const start = useMemo(() => isoDaysAgo(30), []);
  const end = new Date().toISOString().slice(0, 10);

  const orders = useQuery({ queryKey: ["orders", "placed"], queryFn: () => api.orders() });
  const fulfilment = useQuery({
    queryKey: ["orders", "received"],
    queryFn: () => api.fulfilment(),
    enabled: canSupply,
  });
  const alerts = useQuery({
    queryKey: ["alerts", "inventory"],
    queryFn: () => api.alerts("inventory"),
  });
  const dashboard = useQuery({
    queryKey: ["finance-dashboard", start, end, canSupply ? "DEPOT" : "RETAIL"],
    queryFn: () =>
      api.financeDashboard({ start, end, tier: canSupply ? "DEPOT" : "RETAIL" }),
  });

  const placed = orders.data?.results ?? [];
  const incoming = fulfilment.data?.results ?? [];

  /* Every count is a queue somebody has to clear, and every one links to
     the screen that clears it. A number with nowhere to go is a number
     that gets ignored. */
  const queues: {
    id: string;
    label: string;
    count: number;
    icon: LucideIcon;
    screen: string;
    show: boolean;
  }[] = [
    {
      id: "to-confirm",
      label: "Orders to confirm",
      count: incoming.filter((o) => o.status === "SUBMITTED").length,
      icon: ClipboardList,
      screen: "distribution",
      show: canSupply,
    },
    {
      id: "to-pick",
      label: "Orders to pick",
      count: incoming.filter((o) =>
        ["CONFIRMED", "PREPARING", "PARTIALLY_DISPATCHED"].includes(o.status),
      ).length,
      icon: Truck,
      screen: "distribution",
      show: canSupply,
    },
    {
      id: "to-receive",
      label: "Deliveries to receive",
      count: placed.filter((o) =>
        ["CONFIRMED", "PARTIALLY_RECEIVED", "DISPATCHED"].includes(o.status),
      ).length,
      icon: PackageCheck,
      screen: "receiving",
      show: true,
    },
    {
      id: "drafts",
      label: "Draft orders",
      count: placed.filter((o) => o.status === "DRAFT").length,
      icon: ShoppingCart,
      screen: "orders",
      show: true,
    },
  ].filter((queue) => queue.show);

  const report = dashboard.data?.report;

  return (
    <>
      <PageHeader
        title="Overview"
        description={canSupply ? "What needs picking today." : "What needs you today."}
      />

      {/* Above everything: an expiring batch is more urgent than a
          number, and it is the one thing here that costs money by being
          ignored. */}
      <AlertStack alerts={alerts.data?.visible ?? []} className="mb-5" />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {orders.isPending
          ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[92px]" />)
          : queues.map((queue) => (
              <button
                key={queue.id}
                type="button"
                onClick={() => onNavigate(queue.screen)}
                className="group rounded-lg border border-border bg-surface p-4 text-left transition-colors hover:bg-hover"
              >
                <span className="flex items-center gap-2 text-label font-medium text-text-2">
                  <queue.icon size={15} strokeWidth={1.9} aria-hidden />
                  {queue.label}
                </span>
                <span className="mt-1 flex items-baseline justify-between">
                  <span className="text-page font-semibold tabular-nums text-text">
                    {queue.count}
                  </span>
                  <ArrowRight
                    size={15}
                    strokeWidth={1.9}
                    aria-hidden
                    className="text-text-3 opacity-0 transition-opacity group-hover:opacity-100"
                  />
                </span>
              </button>
            ))}
      </div>

      <h2 className="mb-3 text-section font-semibold text-text">Last 30 days</h2>
      {dashboard.isPending ? (
        <Skeleton className="h-[92px]" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Figure label="Revenue" value={MONEY.format(report?.revenue ?? 0)} />
          <Figure label="Gross profit" value={MONEY.format(report?.gross_profit ?? 0)} />
          <Figure
            label="Stock at risk"
            value={MONEY.format(report?.stock_at_risk ?? 0)}
            detail="Expiring within 90 days"
          />
          <Figure
            label="Written off"
            value={MONEY.format(report?.write_offs ?? 0)}
            detail="Expiry and damage"
          />
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <Button onClick={() => onNavigate("analytics")}>Performance</Button>
        {canSell && <Button onClick={() => onNavigate("pos")}>Point of sale</Button>}
        {canSupply && (
          <Button onClick={() => onNavigate("pharmacies")}>Pharmacies</Button>
        )}
      </div>
    </>
  );
}

function Figure({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <p className="text-label font-medium text-text-2">{label}</p>
      <p className="mt-1 text-page font-semibold tabular-nums text-text">{value}</p>
      {detail && <p className="mt-0.5 text-help text-text-3">{detail}</p>}
    </div>
  );
}
