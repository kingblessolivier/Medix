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
import {
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  ListFilter,
  MoreHorizontal,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Checkbox, EmptyState, Skeleton } from "@/components/ui";

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

/** One entry in a row's overflow menu. */
export type RowAction<T> = {
  label: string;
  onSelect: (row: T) => void;
  /** Destructive entries are separated and coloured. */
  danger?: boolean;
  disabled?: (row: T) => boolean;
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
  /** Row selection. Selected ids are owned by the screen, not the table. */
  selectable?: boolean;
  selected?: ReadonlySet<string>;
  onSelectionChange?: (next: Set<string>) => void;
  /** Label each row for a screen reader: "Select order #SO-00004". */
  rowLabel?: (row: T) => string;
  /** Rendered above the table while anything is selected. */
  bulkActions?: (selectedRows: T[]) => ReactNode;
  rowActions?: RowAction<T>[];
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
  selectable = false,
  selected,
  onSelectionChange,
  rowLabel,
  bulkActions,
  rowActions,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const chosen = selected ?? EMPTY;

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

  const allKeys = sorted.map(rowKey);
  const chosenHere = allKeys.filter((k) => chosen.has(k));
  const allChosen = allKeys.length > 0 && chosenHere.length === allKeys.length;
  const someChosen = chosenHere.length > 0 && !allChosen;

  function toggleRow(key: string, on: boolean) {
    if (!onSelectionChange) return;
    const next = new Set(chosen);
    if (on) next.add(key);
    else next.delete(key);
    onSelectionChange(next);
  }

  function toggleAll(on: boolean) {
    if (!onSelectionChange) return;
    // Only rows currently in view are affected; a filtered-out row keeps
    // whatever the user already decided about it.
    const next = new Set(chosen);
    for (const k of allKeys) {
      if (on) next.add(k);
      else next.delete(k);
    }
    onSelectionChange(next);
  }

  const selectedRows = sorted.filter((r) => chosen.has(rowKey(r)));

  if (!loading && rows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface">
        <EmptyState heading={emptyHeading} body={emptyBody} action={emptyAction} />
      </div>
    );
  }

  return (
    <>
      {/* The bulk bar replaces nothing and covers nothing: it appears
          above the table, so the rows it acts on stay visible. */}
      {selectable && bulkActions && chosenHere.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-3 rounded-md border border-brand bg-brand-weak px-3 py-2">
          <span className="text-body font-semibold text-brand-text">
            {chosenHere.length} selected
          </span>
          <div className="flex flex-wrap items-center gap-2">{bulkActions(selectedRows)}</div>
          <button
            type="button"
            onClick={() => toggleAll(false)}
            className="ml-auto text-help text-text-2 underline hover:text-text"
          >
            Clear
          </button>
        </div>
      )}

      {/* Wide content scrolls inside its own container; the page body never
       * scrolls sideways. */}
      <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="sticky top-0 z-sticky">
          <tr className="bg-content">
            {selectable && (
              <th scope="col" className="border-b border-border px-3 py-2" style={{ width: "2.75rem" }}>
                <Checkbox
                  label="Select all rows"
                  checked={allChosen}
                  indeterminate={someChosen}
                  onChange={toggleAll}
                />
              </th>
            )}
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
                    "whitespace-nowrap border-b border-border px-3 py-2",
                    "text-label font-semibold text-text-2",
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
                          <ChevronUp size={14} strokeWidth={2} aria-hidden />
                        ) : (
                          <ChevronDown size={14} strokeWidth={2} aria-hidden />
                        )
                      ) : (
                        <ChevronsUpDown
                          size={14}
                          strokeWidth={1.8}
                          className="opacity-0 transition-opacity group-hover:opacity-60"
                        aria-hidden />
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
            {rowActions && rowActions.length > 0 && (
              <th scope="col" className="border-b border-border px-3 py-2" style={{ width: "3rem" }}>
                <span className="sr-only">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} className={clsx("border-t border-hair", ROW_HEIGHT[density])}>
                  {selectable && <td className="px-3" />}
                  {columns.map((c) => (
                    <td key={c.key} className="px-3">
                      <Skeleton className="h-3.5 w-full max-w-[140px]" />
                    </td>
                  ))}
                  {rowActions && rowActions.length > 0 && <td className="px-3" />}
                </tr>
              ))
            : sorted.map((row) => {
                const key = rowKey(row);
                const isChosen = chosen.has(key);
                return (
                  <tr
                    key={key}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    tabIndex={onRowClick ? 0 : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === "Enter") onRowClick(row);
                          }
                        : undefined
                    }
                    aria-selected={selectable ? isChosen : undefined}
                    className={clsx(
                      "border-t border-hair transition-colors",
                      ROW_HEIGHT[density],
                      onRowClick && "cursor-pointer",
                      // Selection is a tint plus an edge marker, never a
                      // saturated row fill — the data has to stay readable.
                      isChosen ? "bg-selected" : onRowClick && "hover:bg-hover",
                    )}
                  >
                    {selectable && (
                      <td
                        className={clsx(
                          "px-3",
                          // The accent bar. Drawn on the first cell so it
                          // spans the row's full height without a wrapper.
                          isChosen ? "border-l-2 border-brand" : "border-l-2 border-transparent",
                        )}
                      >
                        <Checkbox
                          label={rowLabel ? rowLabel(row) : `Select row ${key}`}
                          checked={isChosen}
                          onChange={(on) => toggleRow(key, on)}
                        />
                      </td>
                    )}
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={clsx(
                          // One line per row. A name too long for its
                          // column truncates; it never reflows the row.
                          "max-w-[22rem] truncate whitespace-nowrap px-3 text-body text-text",
                          c.numeric && "text-right",
                          c.mono && "font-mono text-label",
                        )}
                      >
                        {c.render(row)}
                      </td>
                    ))}
                    {rowActions && rowActions.length > 0 && (
                      <td className="px-3 text-right">
                        <RowMenu row={row} actions={rowActions} />
                      </td>
                    )}
                  </tr>
                );
              })}
        </tbody>
      </table>
      </div>
    </>
  );
}

const EMPTY: ReadonlySet<string> = new Set();

/* -- Row overflow menu -------------------------------------------------- */

/* Secondary per-row actions. The primary action is always the row click;
 * this is for the two or three things that are not it. */
function RowMenu<T>({ row, actions }: { row: T; actions: RowAction<T>[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div ref={box} className="relative inline-block">
      <button
        type="button"
        aria-label="Row actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={clsx(
          "inline-flex h-7 w-7 items-center justify-center rounded-sm transition-colors",
          open ? "bg-hover text-text" : "text-text-3 hover:bg-hover hover:text-text",
        )}
      >
        <MoreHorizontal size={16} strokeWidth={2} aria-hidden />
      </button>

      {open && (
        <div
          role="menu"
          className={clsx(
            "absolute right-0 z-dropdown mt-1 min-w-[10rem] overflow-hidden rounded-md",
            "border border-border bg-surface py-1 shadow-e2",
          )}
        >
          {actions.map((a) => {
            const off = a.disabled?.(row) ?? false;
            return (
              <button
                key={a.label}
                type="button"
                role="menuitem"
                disabled={off}
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(false);
                  a.onSelect(row);
                }}
                className={clsx(
                  "block w-full px-3 py-1.5 text-left text-body transition-colors",
                  off
                    ? "cursor-not-allowed text-text-3"
                    : a.danger
                      ? "text-bad-text hover:bg-bad-bg"
                      : "text-text hover:bg-hover",
                )}
              >
                {a.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* -- Tabs -------------------------------------------------------------- */

/* Saved views over one list — "All orders", "To ship". Each carries its
 * count, because the count is usually the reason to click.
 *
 * These are filters, not navigation: the URL and the page title do not
 * change. A tab that loads a different resource is a nav item. */

export type TableTab = {
  id: string;
  label: string;
  count?: number;
  icon?: LucideIcon;
};

export function TableTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TableTab[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div role="tablist" className="mb-3 flex flex-wrap items-center gap-1 border-b border-border">
      {tabs.map((t) => {
        const on = t.id === active;
        const Icon = t.icon;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.id)}
            className={clsx(
              "-mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-body transition-colors",
              on
                ? "border-brand font-semibold text-brand-text"
                : "border-transparent text-text-2 hover:text-text",
            )}
          >
            {Icon && <Icon size={15} strokeWidth={1.8} aria-hidden />}
            {t.label}
            {t.count !== undefined && (
              <span
                className={clsx(
                  "tabular rounded-full px-1.5 py-0.5 text-group font-semibold",
                  on ? "bg-brand-weak text-brand-text" : "bg-hover text-text-2",
                )}
              >
                {t.count.toLocaleString()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* -- Toolbar ----------------------------------------------------------- */

export function DataToolbar({
  search,
  onSearch,
  searchPlaceholder = "Filter results",
  density,
  onDensity,
  right,
}: {
  search?: string;
  onSearch?: (v: string) => void;
  searchPlaceholder?: string;
  density?: Density;
  onDensity?: (d: Density) => void;
  right?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {onSearch && (
        <div className="relative w-full max-w-xs">
          <ListFilter
            size={15}
            strokeWidth={1.8}
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-3"
          />
          <input
            value={search ?? ""}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className={
              "h-8 w-full rounded-md border border-control bg-surface pl-8 pr-3 " +
              "text-body text-text placeholder:text-text-3 focus:border-brand"
            }
          />
        </div>
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
