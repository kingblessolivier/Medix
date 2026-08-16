/* A headline number. Sometimes the answer is not a chart.
 *
 * "Total invested: 145,000,000" is a single fact. Plotting one value is
 * decoration, and decoration between the reader and the number costs
 * them a second every time they look.
 *
 * Numbers are tabular-nums throughout so a column of tiles aligns and
 * two figures can be compared by eye rather than by reading.
 */

import clsx from "clsx";
import type { ReactNode } from "react";

export function StatTile({
  label,
  value,
  detail,
  emphasis = false,
}: {
  label: string;
  value: string;
  /** One line under the number. Context, never an explanation. */
  detail?: string;
  emphasis?: boolean;
}) {
  return (
    <div
      className={clsx(
        "rounded-lg border bg-surface p-4",
        emphasis ? "border-brand" : "border-border",
      )}
    >
      <p className="text-label font-medium text-text-2">{label}</p>
      <p className="mt-1 text-page font-semibold tabular-nums text-text">{value}</p>
      {detail && <p className="mt-0.5 text-help text-text-3">{detail}</p>}
    </div>
  );
}

export function TileRow({ children }: { children: ReactNode }) {
  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{children}</div>
  );
}
