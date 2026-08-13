/* The bell, wired to the alerts that already exist.
 *
 * It shows what is wrong across the whole organization rather than on
 * the current screen — expiring stock, a lapsing licence, a customer
 * past their terms — because those do not belong to any one screen and
 * would otherwise only be found by visiting all of them.
 *
 * Critical is a count that shows even at zero-adjacent glance distance;
 * warnings are folded in behind it. Nothing here is dismissable: an
 * alert goes away when the thing it is about is dealt with, and a bell
 * you can clear is a bell that lies.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import clsx from "clsx";
import { api, type Alert } from "@/lib/api";

/* Quiet background refresh: these change on a scale of hours, not
   seconds, and a bell that repolls constantly is a bell that costs more
   than it tells anyone. */
const REFRESH = 5 * 60 * 1000;

export function NotificationBell({
  onNavigate,
}: {
  onNavigate?: (screen: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  /* Written out rather than mapped over a list of scopes: hooks must not
     be called in a loop, even one whose length happens to be constant. */
  const stock = useQuery({
    queryKey: ["alerts", "inventory"],
    queryFn: () => api.alerts("inventory"),
    refetchInterval: REFRESH,
  });
  const compliance = useQuery({
    queryKey: ["alerts", "compliance"],
    queryFn: () => api.alerts("compliance"),
    refetchInterval: REFRESH,
  });
  const receivables = useQuery({
    queryKey: ["alerts", "receivables"],
    queryFn: () => api.alerts("receivables"),
    refetchInterval: REFRESH,
  });

  const alerts: Alert[] = [
    ...(stock.data?.visible ?? []),
    ...(compliance.data?.visible ?? []),
    ...(receivables.data?.visible ?? []),
  ];
  const critical = alerts.filter((a) => a.severity === "CRITICAL").length;
  const total = alerts.length;

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const screenFor = (alert: Alert): string => {
    if (alert.code.startsWith("LICENCE") || alert.code.startsWith("PHARMACIST")) {
      return "compliance";
    }
    if (alert.code.startsWith("RECEIVABLE") || alert.code.startsWith("CREDIT")) {
      return "finance";
    }
    return "inventory";
  };

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={total === 0 ? "Notifications" : `Notifications, ${total} open`}
        aria-expanded={open}
        className="relative text-text-2 hover:text-text"
      >
        <Bell size={17} strokeWidth={1.8} aria-hidden />
        {total > 0 && (
          /* The dot carries a count, and the count carries the number —
             colour is never the only signal. */
          <span
            className={clsx(
              "absolute -right-1.5 -top-1 grid h-4 min-w-4 place-items-center rounded-full px-1",
              "text-group font-semibold tabular-nums",
              critical > 0 ? "bg-bad text-white" : "bg-warn text-white",
            )}
          >
            {total}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Open alerts"
          className="absolute right-0 top-8 z-modal w-80 overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
        >
          <p className="border-b border-border px-3 py-2 text-label font-medium text-text-2">
            {total === 0 ? "Nothing needs you" : `${total} open`}
          </p>

          {total === 0 ? (
            <p className="px-3 py-6 text-center text-help text-text-3">
              Stock, licences and receivables are all clear.
            </p>
          ) : (
            <ul className="max-h-[60vh] overflow-y-auto">
              {alerts.map((alert) => (
                <li key={`${alert.code}-${alert.subject_id}`}>
                  <button
                    type="button"
                    onClick={() => {
                      onNavigate?.(screenFor(alert));
                      setOpen(false);
                    }}
                    className="flex w-full items-start gap-2 border-b border-hair px-3 py-2 text-left last:border-0 hover:bg-hover"
                  >
                    <span
                      aria-hidden
                      className={clsx(
                        "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                        alert.severity === "CRITICAL"
                          ? "bg-bad"
                          : alert.severity === "WARNING"
                            ? "bg-warn"
                            : "bg-info",
                      )}
                    />
                    <span className="min-w-0">
                      <span className="block text-body text-text">{alert.title}</span>
                      {alert.detail && (
                        <span className="block text-help text-text-3">
                          {alert.detail}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
