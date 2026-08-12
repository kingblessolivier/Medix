/* Marketplace browse.
 *
 * List is the professional default — a pharmacist doing procurement
 * compares faster in rows. Grid is a toggle, for when visual
 * identification actually matters.
 *
 * Cards stay small. The failure mode is a huge image, a description
 * paragraph and a full-width button: that card fits four products where a
 * proper one fits twelve, and a buyer is comparing, not shopping.
 *
 * See docs/19-screens.md §2 and docs/05-modules.md §2.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, List, Plane, Plus, Snowflake } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiFailure, api, type MarketplaceRow, type VendorRow } from "@/lib/api";
import { DataTable, DataToolbar, type Column, type Density } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  ErrorState,
  Field,
  Input,
  PageHeader,
  StatusDot,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { DetailList, Drawer } from "@/components/ui/Drawer";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });
const MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric" });

const AVAILABILITY: Record<string, { tone: Tone; label: string }> = {
  AVAILABLE_NOW: { tone: "ok", label: "Available" },
  INCOMING: { tone: "warn", label: "Incoming" },
  PRE_ORDER: { tone: "warn", label: "Pre-order" },
  IMPORT_ON_DEMAND: { tone: "neutral", label: "Import only" },
  NOT_IN_COUNTRY: { tone: "neutral", label: "Not in Rwanda" },
};

export function MarketplaceScreen({ locationId }: { locationId: string | null }) {
  const [view, setView] = useState<"list" | "grid">("list");
  const [density, setDensity] = useState<Density>("compact");
  const [search, setSearch] = useState("");
  const [compare, setCompare] = useState<MarketplaceRow | null>(null);

  const listings = useQuery({
    queryKey: ["marketplace", search],
    queryFn: () =>
      api.marketplace(`?exclude_own=true${search ? `&search=${encodeURIComponent(search)}` : ""}`),
  });

  const rows = listings.data?.results ?? [];

  const columns: Column<MarketplaceRow>[] = [
    {
      key: "product",
      header: "Product",
      sortable: true,
      render: (r) => (
        <span className="flex items-center gap-1.5">
          {r.product_name}
          {r.cold_chain && (
            <Snowflake size={13} strokeWidth={1.8} className="text-brand" aria-label="Cold chain" />
          )}
        </span>
      ),
      sortValue: (r) => r.product_name,
    },
    { key: "vendor", header: "Supplier", render: (r) => r.vendor_name },
    {
      key: "stock",
      header: "Stock",
      numeric: true,
      sortable: true,
      render: (r) => (r.is_orderable ? r.stock_base.toLocaleString() : "—"),
      sortValue: (r) => r.stock_base,
    },
    {
      key: "price",
      header: "Price",
      numeric: true,
      sortable: true,
      render: (r) => (r.is_orderable ? CURRENCY.format(r.price) : "—"),
      sortValue: (r) => r.price,
    },
    { key: "moq", header: "MOQ", numeric: true, render: (r) => r.moq.toLocaleString() },
    {
      key: "status",
      header: "Status",
      render: (r) => {
        const state = AVAILABILITY[r.availability] ?? AVAILABILITY.NOT_IN_COUNTRY;
        return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
      },
    },
  ];

  if (listings.isError) {
    return (
      <>
        <PageHeader title="Marketplace" description="Products from wholesale pharmacies" />
        <ErrorState message="Couldn't load listings." onRetry={() => listings.refetch()} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Marketplace"
        description="Products from wholesale pharmacies"
        actions={
          <span className="text-body text-text-2">
            {rows.length.toLocaleString()} listings
          </span>
        }
      />

      <DataToolbar
        search={search}
        onSearch={setSearch}
        density={view === "list" ? density : undefined}
        onDensity={view === "list" ? setDensity : undefined}
        right={<ViewToggle view={view} onChange={setView} />}
      />

      {view === "list" ? (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          density={density}
          loading={listings.isLoading}
          caption="Marketplace listings"
          onRowClick={setCompare}
          emptyHeading={search ? `No results for "${search}"` : "No listings"}
        />
      ) : (
        <CardGrid rows={rows} onSelect={setCompare} />
      )}

      <CompareDrawer
        row={compare}
        locationId={locationId}
        onClose={() => setCompare(null)}
      />
    </>
  );
}

function ViewToggle({
  view,
  onChange,
}: {
  view: "list" | "grid";
  onChange: (v: "list" | "grid") => void;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-sm border border-border">
      {(
        [
          ["list", List, "List"],
          ["grid", LayoutGrid, "Grid"],
        ] as const
      ).map(([value, Icon, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          aria-pressed={view === value}
          className={
            "flex items-center gap-1.5 px-2.5 py-1 text-help transition-colors " +
            (view === value
              ? "bg-selected font-semibold text-brand-text"
              : "text-text-2 hover:bg-hover")
          }
        >
          <Icon size={13} strokeWidth={1.8} />
          {label}
        </button>
      ))}
    </div>
  );
}

/* Small cards. Image strip, name, form and pack, price with stock, one
   compact action. Nothing else — a description paragraph here would cost
   two-thirds of the rows on screen. */
function CardGrid({
  rows,
  onSelect,
}: {
  rows: MarketplaceRow[];
  onSelect: (row: MarketplaceRow) => void;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface px-6 py-14 text-center">
        <p className="text-section font-semibold">No listings</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3">
      {rows.map((row) => {
        const state = AVAILABILITY[row.availability] ?? AVAILABILITY.NOT_IN_COUNTRY;
        return (
          <button
            key={row.id}
            type="button"
            onClick={() => onSelect(row)}
            className="overflow-hidden rounded-md border border-border bg-surface text-left transition-colors hover:border-brand"
          >
            <div className="flex h-[74px] items-center justify-center border-b border-hair bg-content text-help tracking-wide text-text-3">
              {row.cold_chain ? (
                <Snowflake size={20} strokeWidth={1.5} className="text-brand" />
              ) : (
                "PRODUCT IMAGE"
              )}
            </div>

            <div className="px-2.5 pb-2.5 pt-2">
              <p className="truncate text-body font-semibold leading-tight">{row.product_name}</p>
              <p className="truncate text-help text-text-2">
                {row.uom_name} · {row.vendor_name}
              </p>

              <div className="mt-2 flex items-baseline justify-between">
                <span className="tabular text-body font-semibold">
                  {row.is_orderable ? CURRENCY.format(row.price) : "—"}
                </span>
                <span className="text-help text-text-2">
                  {row.is_orderable ? `${row.stock_base.toLocaleString()} left` : state.label}
                </span>
              </div>

              {row.requires_prescription && (
                <p className="mt-1 text-help text-warn-text">Prescription only</p>
              )}

              <span className="mt-2 block w-full rounded-sm border border-border py-1 text-center text-help font-semibold">
                {row.is_orderable ? "Compare" : "Request import"}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/* Vendor comparison: price against expiry, MOQ and lead time. The system
   makes the tradeoff visible; it does not choose. */
function CompareDrawer({
  row,
  locationId,
  onClose,
}: {
  row: MarketplaceRow | null;
  locationId: string | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState("");
  const [added, setAdded] = useState<{ number: string | null; lines: number } | null>(null);
  const [failure, setFailure] = useState("");

  const vendors = useQuery({
    queryKey: ["compare", row?.product],
    queryFn: () => api.compareVendors(row!.product),
    enabled: Boolean(row),
  });

  // Each product opens on its own minimum, and last product's outcome
  // must not linger on the next one.
  useEffect(() => {
    setQuantity(row ? String(row.moq) : "");
    setAdded(null);
    setFailure("");
  }, [row?.id, row?.moq]);

  const add = useMutation({
    mutationFn: async () => {
      const draft = await api.openDraft({
        supplier: row!.vendor,
        deliver_to: locationId!,
      });
      return api.addOrderLine(draft.id, { listing: row!.id, quantity: Number(quantity) });
    },
    onSuccess: (order) => {
      // An order is numbered when it is submitted, not while it is built.
      setAdded({ number: order.number || null, lines: order.lines.length });
      setFailure("");
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
    // The supplier's rules — minimum, availability — are stated by the
    // server. Show what it said rather than a generic failure.
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Couldn't add to order.",
      ),
  });

  if (!row) return null;
  const state = AVAILABILITY[row.availability] ?? AVAILABILITY.NOT_IN_COUNTRY;
  const count = Number(quantity);
  const valid = Number.isInteger(count) && count > 0 && Boolean(locationId);

  return (
    <Drawer
      open
      title={row.product_name}
      subtitle={row.generic_name || row.uom_name}
      onClose={onClose}
      footer={
        row.is_orderable ? (
          <div className="flex items-end gap-2">
            <div className="w-24">
              <Field label="Quantity">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    min={1}
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                    className="tabular text-right"
                  />
                )}
              </Field>
            </div>
            <Button
              variant="primary"
              className="flex-1"
              icon={<Plus size={16} strokeWidth={2} />}
              loading={add.isPending}
              disabled={!valid}
              onClick={() => add.mutate()}
            >
              Add to order
            </Button>
          </div>
        ) : (
          <Button
            variant="secondary"
            className="w-full"
            icon={<Plane size={16} strokeWidth={1.8} />}
          >
            Request import
          </Button>
        )
      }
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <StatusDot tone={state.tone}>{state.label}</StatusDot>
        {row.requires_prescription && <Badge tone="warn">Prescription only</Badge>}
        {row.cold_chain && <Badge tone="brand">Cold chain</Badge>}
      </div>

      {added && (
        <Banner tone="ok" className="mb-4">
          {added.number ? `On order ${added.number}` : "On draft order"} · {added.lines} line
          {added.lines === 1 ? "" : "s"}
        </Banner>
      )}
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <DetailList
        rows={[
          ["Supplier", row.vendor_name],
          ["Price", `${CURRENCY.format(row.price)} / ${row.uom_code.toLowerCase()}`],
          ["Minimum order", row.moq.toLocaleString()],
          ["Lead time", `${row.lead_time_days} day${row.lead_time_days === 1 ? "" : "s"}`],
          ["Order value", CURRENCY.format(row.price * (valid ? count : 0))],
        ]}
      />

      <h3 className="mb-2 mt-6 text-section font-semibold">All suppliers</h3>
      {vendors.isLoading ? (
        <p className="text-body text-text-2">Loading…</p>
      ) : (
        <VendorTable rows={vendors.data ?? []} />
      )}
    </Drawer>
  );
}

const VENDOR_COLUMNS: Column<VendorRow>[] = [
  { key: "vendor", header: "Supplier", render: (v) => v.vendor_name },
  {
    key: "price",
    header: "Price",
    numeric: true,
    sortable: true,
    render: (v) => CURRENCY.format(v.price),
    sortValue: (v) => v.price,
  },
  {
    key: "stock",
    header: "Stock",
    numeric: true,
    sortable: true,
    render: (v) => v.stock_base.toLocaleString(),
    sortValue: (v) => v.stock_base,
  },
  {
    key: "expiry",
    header: "Expiry",
    sortable: true,
    // Cheapest with three months left is rarely the right buy, so expiry
    // sits beside price rather than behind a click.
    render: (v) => (
      <span className="text-text-2">
        {v.earliest_expiry ? MONTH.format(new Date(v.earliest_expiry)) : "—"}
      </span>
    ),
    sortValue: (v) => v.earliest_expiry ?? "",
  },
  { key: "moq", header: "MOQ", numeric: true, render: (v) => v.moq },
  { key: "lead", header: "Days", numeric: true, render: (v) => v.lead_time_days },
];

function VendorTable({ rows }: { rows: VendorRow[] }) {
  return (
    <DataTable
      columns={VENDOR_COLUMNS}
      rows={rows}
      rowKey={(v) => v.listing_id}
      density="compact"
      caption="Suppliers offering this product"
      emptyHeading="No suppliers"
    />
  );
}
