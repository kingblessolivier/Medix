/* DataTable — the most important component in the product.
 *
 * In Medix the table is the interface, not filler between cards. Every
 * data list uses this; never hand-roll a <table>.
 *
 * Compact is the default density: a pharmacist scanning 300 batches sees
 * roughly twice as many rows at 40px as at comfortable.
 *
 * See docs/22-components.md.
 */

import clsx from "clsx";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { EmptyState, Skeleton } from "@/components/ui";

export type Density = "compact" | "comfortable" | "spacious";

const ROW_HEIGHT: Record<Density, string> = {
  compact: "h-10",
  comfortable: "h-12",
  spacious: "h-14",
};

export type Column<T> = {
  key: string;
  header: string;
  /** Right-align and use tabular figures. Money, quantity, percentages. */
  numeric?: boolean;
  /** Render in mono. Batch numbers, document numbers, codes. */
  mono?: boolean;
  sortable?: boolean;
  width?: string;
  render: (row: T) => ReactNode;
  /** Value used for sorting when render() returns a node. */
  sortValue?: (row: T) => string | number;
};

export type DataTableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  density?: Density;
  loading?: boolean;
  onRowClick?: (row: T) => void;
  emptyHeading?: string;
  emptyBody?: string;
  emptyAction?: ReactNode;
  caption?: string;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  density = "compact",
  loading = false,
  onRowClick,
  emptyHeading = "No results",
  emptyBody,
  emptyAction,
  caption,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.sortValue) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = column.sortValue!(a);
      const vb = column.sortValue!(b);
      if (va === vb) return 0;
      return (va < vb ? -1 : 1) * factor;
    });
  }, [rows, sort, columns]);

  function toggleSort(key: string) {
    setSort((prev) =>
      prev?.key === key
        ? prev.dir === "asc"
          ? { key, dir: "desc" }
          : null
        : { key, dir: "asc" },
    );
  }

  if (!loading && rows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface">
        <EmptyState heading={emptyHeading} body={emptyBody} action={emptyAction} />
      </div>
    );
  }

  return (
    /* Wide content scrolls inside its own container; the page body never
     * scrolls sideways. */
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="sticky top-0 z-sticky">
          <tr className="bg-content">
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none";
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={c.sortable ? ariaSort : undefined}
                  style={c.width ? { width: c.width } : undefined}
                  className={clsx(
                    "border-b border-border px-3 py-2 text-label font-semibold text-text-2",
                    c.numeric ? "text-right" : "text-left",
                  )}
                >
                  {c.sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(c.key)}
                      className={clsx(
                        "group inline-flex items-center gap-1 rounded-sm",
                        c.numeric && "flex-row-reverse",
                        active ? "text-text" : "hover:text-text",
                      )}
                    >
                      {c.header}
                      {/* The affordance stays quiet until interacted with. */}
                      {active ? (
                        sort!.dir === "asc" ? (
                          <ChevronUp size={14} strokeWidth={2} />
                        ) : (
                          <ChevronDown size={14} strokeWidth={2} />
                        )
                      ) : (
                        <ChevronsUpDown
                          size={14}
                          strokeWidth={1.8}
                          className="opacity-0 transition-opacity group-hover:opacity-60"
                        />
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} className={clsx("border-t border-hair", ROW_HEIGHT[density])}>
                  {columns.map((c) => (
                    <td key={c.key} className="px-3">
                      <Skeleton className="h-3.5 w-full max-w-[140px]" />
                    </td>
                  ))}
                </tr>
              ))
            : sorted.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.key === "Enter") onRowClick(row);
                        }
                      : undefined
                  }
                  className={clsx(
                    "border-t border-hair transition-colors",
                    ROW_HEIGHT[density],
                    onRowClick && "cursor-pointer hover:bg-hover",
                  )}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={clsx(
                        "px-3 text-body text-text",
                        c.numeric && "text-right",
                        c.mono && "font-mono text-label",
                      )}
                    >
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
        </tbody>
      </table>
    </div>
  );
}

/* -- Toolbar ----------------------------------------------------------- */

export function DataToolbar({
  search,
  onSearch,
  density,
  onDensity,
  right,
}: {
  search?: string;
  onSearch?: (v: string) => void;
  density?: Density;
  onDensity?: (d: Density) => void;
  right?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {onSearch && (
        <input
          value={search ?? ""}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search products, batches…"
          className={
            "h-8 w-full max-w-xs rounded-md border border-border bg-surface px-3 " +
            "text-body text-text placeholder:text-text-3 focus:border-brand"
          }
        />
      )}
      <div className="ml-auto flex items-center gap-2">
        {onDensity && density && (
          <div className="inline-flex overflow-hidden rounded-sm border border-border">
            {(["compact", "comfortable"] as const).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => onDensity(d)}
                className={clsx(
                  "px-2.5 py-1 text-help capitalize transition-colors",
                  density === d ? "bg-selected font-semibold text-brand-text" : "text-text-2 hover:bg-hover",
                )}
              >
                {d}
              </button>
            ))}
          </div>
        )}
        {right}
      </div>
    </div>
  );
}

/* -- Pagination -------------------------------------------------------- */

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="mt-3 flex items-center justify-between gap-4">
      <p className="text-body text-text-2">
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </p>
      <div className="flex items-center gap-1">
        {/* Disabled arrows stay in place — the layout must not shift. */}
        <PageButton disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </PageButton>
        <span className="px-2 text-body text-text-2">
          {page} / {pages}
        </span>
        <PageButton disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </PageButton>
      </div>
    </div>
  );
}

function PageButton({
  disabled,
  onClick,
  children,
}: {
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "rounded-sm px-2.5 py-1 text-body transition-colors",
        disabled ? "cursor-not-allowed text-text-3" : "text-text-2 hover:bg-hover hover:text-text",
      )}
    >
      {children}
    </button>
  );
}
