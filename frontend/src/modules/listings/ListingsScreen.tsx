/* What this depot offers, and how much of it.
 *
 * The whole depot side of the platform passed through here and there was
 * no screen: a wholesaler could receive a container, cost it, hold it —
 * and had no way to offer a single item for sale. The marketplace only
 * had rows in it because the seed command put them there.
 *
 * The distinction the screen exists to make is **offered against held**.
 * A depot's allocation is not its stock. Holding 500 packs and offering
 * 200 is normal — the rest is spoken for by its own branches or a
 * standing contract — and publishing the true balance would tell every
 * customer, and every competitor with an account, exactly what this
 * depot is sitting on.
 *
 * So the table shows both, and offering more than is held is refused by
 * the server rather than accepted and discovered by a buyer whose order
 * cannot ship.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ApiFailure,
  api,
  type MarketplaceRow,
  type ProductRow,
  type ProductDetail,
} from "@/lib/api";
import { DataTable, DataToolbar, type Column, type RowAction } from "@/components/data/DataTable";
import { ProductImage } from "@/components/data/ProductImage";
import {
  Banner,
  Button,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const MONEY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

const AVAILABILITY: Record<string, { tone: Tone; label: string }> = {
  AVAILABLE_NOW: { tone: "ok", label: "Available" },
  INCOMING: { tone: "warn", label: "Incoming" },
  PRE_ORDER: { tone: "warn", label: "Pre-order" },
  IMPORT_ON_DEMAND: { tone: "neutral", label: "Import only" },
  NOT_IN_COUNTRY: { tone: "neutral", label: "Not in Rwanda" },
};

export function ListingsScreen() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [failure, setFailure] = useState("");

  const listings = useQuery({
    queryKey: ["my-listings"],
    queryFn: () => api.myListings(),
  });

  const withdraw = useMutation({
    mutationFn: (id: string) => api.withdrawListing(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["my-listings"] }),
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not withdrawn."),
  });

  if (listings.isPending) return <Skeleton className="h-[400px]" />;
  if (listings.isError) {
    return (
      <>
        <PageHeader title="On offer" />
        <ErrorState
          message="Couldn't load listings."
          onRetry={() => listings.refetch()}
        />
      </>
    );
  }

  const all = listings.data.results;
  const term = search.trim().toLowerCase();
  const rows = term
    ? all.filter((r) => r.product_name.toLowerCase().includes(term))
    : all;

  /* An offer with nothing behind it is the failure this screen is meant
     to prevent: the buyer sees "Available", chooses a quantity, and the
     server refuses. Counted at the top so it is not hidden in a column. */
  const empty = all.filter((r) => r.available_base <= 0);

  const columns: Column<MarketplaceRow>[] = [
    {
      key: "product",
      header: "Product",
      sortable: true,
      render: (r) => (
        <span className="flex items-center gap-2">
          <ProductImage src={r.image} alt={r.image_alt} coldChain={r.cold_chain} />
          <span className="min-w-0">
            <span className="block truncate">{r.product_name}</span>
            <span className="block truncate text-help text-text-3">{r.pack_size}</span>
          </span>
        </span>
      ),
      sortValue: (r) => r.product_name,
    },
    {
      key: "price",
      header: "Price",
      numeric: true,
      sortable: true,
      render: (r) => MONEY.format(r.price),
      sortValue: (r) => r.price,
    },
    { key: "moq", header: "Minimum", numeric: true, render: (r) => r.moq.toLocaleString() },
    {
      key: "offered",
      header: "Offered",
      numeric: true,
      sortable: true,
      render: (r) =>
        r.available_base > 0 ? (
          r.available_base.toLocaleString()
        ) : (
          <span className="text-warn-text">None left</span>
        ),
      sortValue: (r) => r.available_base,
    },
    {
      key: "tiers",
      header: "Volume price",
      render: (r) =>
        r.tiers.length > 0 ? `${r.tiers.length} breaks` : <span className="text-text-3">—</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (r) => {
        const state = AVAILABILITY[r.availability] ?? AVAILABILITY.NOT_IN_COUNTRY;
        return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
      },
    },
  ];

  const rowActions: RowAction<MarketplaceRow>[] = [
    {
      label: "Withdraw",
      onSelect: (r) => withdraw.mutate(r.id),
      danger: true,
    },
  ];

  return (
    <>
      <PageHeader
        title="On offer"
        description="Your own listings on the marketplace"
        actions={
          <Button
            variant="primary"
            icon={<Plus size={15} strokeWidth={2} aria-hidden />}
            onClick={() => setPublishing(true)}
          >
            Offer a product
          </Button>
        }
      />

      {all.length === 0 ? (
        <NextAction
          heading="Offer something"
          detail="Pharmacies can only order what you have listed."
          action={
            <Button variant="primary" onClick={() => setPublishing(true)}>
              Offer a product
            </Button>
          }
        />
      ) : empty.length > 0 ? (
        <NextAction
          heading={`${empty.length} with nothing behind them`}
          detail="Buyers see these as available and cannot order them."
        />
      ) : null}

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <DataToolbar
        search={search}
        onSearch={setSearch}
        searchPlaceholder="Filter listings"
      />

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        density="compact"
        caption="Published listings"
        rowActions={rowActions}
        emptyHeading={search ? `No results for "${search}"` : "Nothing offered"}
        emptyBody="A pharmacy can only order what is listed."
      />

      {publishing && <PublishModal onClose={() => setPublishing(false)} />}
    </>
  );
}

function PublishModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [product, setProduct] = useState<ProductRow | null>(null);
  const [search, setSearch] = useState("");
  const [uomCode, setUomCode] = useState("");
  const [price, setPrice] = useState("");
  const [offered, setOffered] = useState("");
  const [moq, setMoq] = useState("1");
  const [leadTime, setLeadTime] = useState("1");
  const [srp, setSrp] = useState("");
  const [failure, setFailure] = useState("");

  const products = useQuery({
    queryKey: ["publishable", search],
    queryFn: () => api.products(`?search=${encodeURIComponent(search)}`),
    enabled: search.trim().length >= 2,
  });

  const detail = useQuery({
    queryKey: ["product", product?.id],
    queryFn: () => api.product(product!.id),
    enabled: Boolean(product),
  });

  useEffect(() => {
    const units = (detail.data as ProductDetail | undefined)?.units ?? [];
    const preferred = units.find((u) => u.is_purchase_default) ?? units[0];
    if (preferred && !uomCode) setUomCode(preferred.code);
  }, [detail.data, uomCode]);

  const publish = useMutation({
    mutationFn: () =>
      api.publishListing({
        product: product!.id,
        price: Number(price),
        uom_code: uomCode,
        offered: offered === "" ? undefined : Number(offered),
        moq: Number(moq) || 1,
        lead_time_days: Number(leadTime) || 1,
        srp: srp === "" ? null : Number(srp),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-listings"] });
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
      onClose();
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Not published.",
      ),
  });

  const units = (detail.data as ProductDetail | undefined)?.units ?? [];
  const chosen = units.find((u) => u.code === uomCode);
  /* Stated in the unit being offered, so the depot can see whether its
     allocation is even possible before the server refuses it. */
  const heldInUnit =
    product && chosen ? Math.floor(product.on_hand_base / chosen.factor_to_base) : 0;
  const overAllocated = offered !== "" && Number(offered) > heldInUnit;
  const ready = product && uomCode && price !== "" && !overAllocated;

  return (
    <Modal
      open
      title="Offer a product"
      subtitle={product?.name}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={publish.isPending}
          onClick={() => publish.mutate()}
        >
          Publish
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {!product ? (
        <div className="flex flex-col gap-3">
          <Field label="Product" required>
            {(id) => (
              <Input
                id={id}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search your catalogue"
              />
            )}
          </Field>
          <ul className="flex flex-col overflow-hidden rounded-md border border-border">
            {(products.data?.results ?? []).slice(0, 8).map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setProduct(row)}
                  className="flex w-full items-center justify-between gap-3 border-b border-hair px-3 py-2 text-left last:border-0 hover:bg-hover"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-body">{row.name}</span>
                    <span className="block text-help text-text-3">
                      {row.on_hand_base > 0
                        ? `${row.on_hand_base.toLocaleString()} ${row.base_uom_name.toLowerCase()} held`
                        : "None held"}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <Consequence
            lines={[
              "Puts this on the marketplace for every pharmacy you supply.",
              "The allocation comes out of your own stock, not on top of it.",
            ]}
          />

          <Field label="Sold by" required>
            {(id) => (
              <Select id={id} value={uomCode} onChange={(e) => setUomCode(e.target.value)}>
                {units
                  .filter((u) => u.is_sellable)
                  .map((unit) => (
                    <option key={unit.code} value={unit.code}>
                      {unit.name}
                    </option>
                  ))}
              </Select>
            )}
          </Field>

          <Field label="Price" help="Per the unit above, in RWF." required>
            {(id) => (
              <Input
                id={id}
                type="number"
                min={0}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                className="tabular text-right"
              />
            )}
          </Field>

          <Field
            label="Offered"
            help={`You hold ${heldInUnit.toLocaleString()} at this level.`}
          >
            {(id) => (
              <Input
                id={id}
                type="number"
                min={0}
                value={offered}
                onChange={(e) => setOffered(e.target.value)}
                invalid={overAllocated}
                className="tabular text-right"
              />
            )}
          </Field>

          {overAllocated && (
            <Banner tone="bad">
              {`You hold ${heldInUnit.toLocaleString()}. Offering more cannot ship.`}
            </Banner>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field label="Minimum order">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={1}
                  value={moq}
                  onChange={(e) => setMoq(e.target.value)}
                  className="tabular text-right"
                />
              )}
            </Field>
            <Field label="Lead time">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  value={leadTime}
                  onChange={(e) => setLeadTime(e.target.value)}
                  className="tabular text-right"
                />
              )}
            </Field>
          </div>

          <Field
            label={
              (
                <Help term="Suggested retail">
                  What you think the pharmacy should sell it for. A starting point
                  they can change — you cannot set their price.
                </Help>
              ) as unknown as string
            }
          >
            {(id) => (
              <Input
                id={id}
                type="number"
                min={0}
                value={srp}
                onChange={(e) => setSrp(e.target.value)}
                className="tabular text-right"
              />
            )}
          </Field>
        </div>
      )}
    </Modal>
  );
}
