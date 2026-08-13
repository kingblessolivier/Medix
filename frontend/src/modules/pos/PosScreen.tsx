/* Point of sale.
 *
 * The one screen with different ergonomics: larger targets, fewer
 * decisions, scanner and keyboard first. A pharmacist at eleven at night
 * should not be fighting the software.
 *
 * Three rules the counter depends on:
 *   - a prescription-only line BLOCKS completion, it does not warn;
 *   - mobile money is asynchronous, so pending is shown honestly;
 *   - the batch is visible per line, because FEFO chose it.
 *
 * See docs/19-screens.md §4.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, Loader2, ScanBarcode, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiFailure, api, type ProductRow, type Sale, type SaleLine } from "@/lib/api";
import { Banner, Button, EmptyState, PageHeader, StatusDot } from "@/components/ui";
import { AlertStack } from "@/components/ui/AlertStack";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

function money(minor: number): string {
  return CURRENCY.format(minor);
}

export function PosScreen({ locationId }: { locationId: string | null }) {
  const queryClient = useQueryClient();
  const [saleId, setSaleId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const sale = useQuery({
    queryKey: ["sale", saleId],
    queryFn: () => api.sale(saleId!),
    enabled: Boolean(saleId),
  });

  const products = useQuery({
    queryKey: ["pos-products", query],
    queryFn: () => api.products(`?search=${encodeURIComponent(query)}`),
    enabled: query.trim().length >= 2,
  });

  const start = useMutation({
    mutationFn: () => api.startSale(locationId!),
    onSuccess: (created) => setSaleId(created.id),
  });

  const addLine = useMutation({
    mutationFn: (product: ProductRow) =>
      api.addLine(saleId!, {
        product: product.id,
        quantity: 1,
        uom_code: "UNIT",
        unit_price: 100,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["sale", saleId], updated);
      setQuery("");
      searchRef.current?.focus();
    },
  });

  /* What the pharmacist must see before completing. Refetched as lines
     change, because an allergy or duplicate only appears once the
     product that conflicts is in the basket. */
  const review = useQuery({
    queryKey: ["sale-clinical", saleId, sale.data?.lines.length],
    queryFn: () => api.saleClinical(saleId!),
    enabled: Boolean(saleId) && (sale.data?.lines.length ?? 0) > 0,
  });

  const [accepted, setAccepted] = useState<string[]>([]);

  /* What the scheme covers, so the counter charges the co-pay rather
     than the full amount. Refetched as lines change, because cover is
     computed per line and a new product may be excluded. */
  const cover = useQuery({
    queryKey: ["sale-cover", saleId, sale.data?.lines.length],
    queryFn: () => api.saleCover(saleId!),
    enabled: Boolean(saleId) && (sale.data?.lines.length ?? 0) > 0,
  });

  const complete = useMutation({
    mutationFn: () => api.completeSale(saleId!, { acknowledged: accepted }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["sale", saleId], updated);
      setAccepted([]);
    },
  });

  const pay = useMutation({
    mutationFn: (method: string) =>
      api.takePayment(saleId!, { method, amount: sale.data!.outstanding }),
    onSuccess: (updated) => queryClient.setQueryData(["sale", saleId], updated),
  });

  // Start a sale as soon as the counter is open, so the first scan lands
  // somewhere without an extra click. Guarded by a ref because StrictMode
  // double-invokes effects and would otherwise open two draft sales.
  const starting = useRef(false);
  useEffect(() => {
    if (!locationId || saleId || starting.current) return;
    starting.current = true;
    start.mutate(undefined, { onSettled: () => (starting.current = false) });
  }, [locationId, saleId]);

  if (!locationId) {
    return (
      <>
        <PageHeader title="Point of sale" />
        <EmptyState heading="No location" body="Set up a store location first." />
      </>
    );
  }

  const current = sale.data;
  const lines = current?.lines ?? [];
  const isDraft = current?.status === "DRAFT";
  const blocked = current?.blocked_reason ?? null;

  /* Warnings still to be accepted. The server refuses without them too —
     this only avoids offering a button that will be turned down. */
  const unacknowledged = (review.data?.visible ?? []).filter(
    (a) => a.severity === "WARNING" && !accepted.includes(a.code),
  ).length;
  const failure = [addLine.error, complete.error, pay.error].find(Boolean) as
    | ApiFailure
    | undefined;

  return (
    <>
      <PageHeader
        title="Point of sale"
        actions={
          current?.number ? (
            <span className="font-mono text-body text-text-2">{current.number}</span>
          ) : undefined
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div>
          {/* Scanner-first: this field holds focus, and a GS1 scan lands
              here as keystrokes. */}
          <div className="mb-3 flex items-center gap-2 rounded-md border border-border bg-surface px-3">
            <ScanBarcode size={18} strokeWidth={1.8} className="text-text-2" aria-hidden />
            <input
              ref={searchRef}
              aria-label="Scan or search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Product or barcode…"
              disabled={!isDraft}
              autoFocus
              className="h-11 w-full bg-transparent text-body text-text placeholder:text-text-3 focus:outline-none disabled:cursor-not-allowed"
            />
          </div>

          {query.trim().length >= 2 && (
            <div className="mb-4 overflow-hidden rounded-md border border-border bg-surface">
              {products.data?.results.length === 0 ? (
                <p className="px-3 py-3 text-body text-text-2">No results for "{query}"</p>
              ) : (
                products.data?.results.slice(0, 6).map((product) => (
                  <button
                    key={product.id}
                    type="button"
                    onClick={() => addLine.mutate(product)}
                    className="flex w-full items-center justify-between border-b border-hair px-3 py-2.5 text-left last:border-0 hover:bg-hover"
                  >
                    <span className="text-body">{product.name}</span>
                    {product.requires_prescription && (
                      <StatusDot tone="warn">Prescription</StatusDot>
                    )}
                  </button>
                ))
              )}
            </div>
          )}

          {lines.length === 0 ? (
            <EmptyState heading="Nothing scanned" />
          ) : (
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              {lines.map((line) => (
                <Line key={line.id} line={line} />
              ))}
            </div>
          )}
        </div>

        <aside className="flex flex-col gap-3">
          {blocked && (
            <div className="flex items-start gap-2 border-l-2 border-bad bg-bad-bg px-3 py-2.5">
              <ShieldAlert size={16} strokeWidth={1.8} className="mt-0.5 shrink-0 text-bad" aria-hidden />
              <div>
                <p className="text-body font-medium text-bad-text">{blocked}</p>
                <button type="button" className="mt-1 text-help text-brand underline">
                  Attach prescription
                </button>
              </div>
            </div>
          )}

          {failure && (
            <Banner tone="bad">{failure.error.message}</Banner>
          )}

          {/* Clinical warnings, above the control they block. Each is a
              recorded data match the pharmacist clears — never a refusal,
              because a hard stop gets worked around while an
              acknowledgement gets written to the audit stream. */}
          {/* Says why cover did not apply. "Not a member", "card
              expired" and "not on their panel" are three different
              conversations to have with the patient. */}
          {cover.data && !cover.data.covered && cover.data.reason && (
            <Banner tone="info">{cover.data.reason}</Banner>
          )}

          {review.data && (
            <>
              <AlertStack
                alerts={(review.data.visible ?? []).filter(
                  (a) => !accepted.includes(a.code),
                )}
                onAcknowledge={(alert) =>
                  setAccepted((codes) => [...codes, alert.code])
                }
              />

              {/* An interaction check that did not run is not a clean
                  one. Saying nothing here would let a pharmacist infer
                  the pair was checked and found safe. */}
              {review.data.interaction_notice && (
                <p className="flex items-start gap-1.5 text-help text-text-3">
                  <Info size={13} strokeWidth={1.9} className="mt-0.5 shrink-0" aria-hidden />
                  {review.data.interaction_notice}
                </p>
              )}
            </>
          )}

          <div className="rounded-lg border border-border bg-surface p-4">
            <Row label="Subtotal" value={money(current?.subtotal ?? 0)} />
            <Row label="Tax" value={money(current?.tax_total ?? 0)} />
            <div className="mt-2 flex items-baseline justify-between border-t border-border pt-2">
              <span className="text-section font-semibold">Total</span>
              <span className="text-hero font-semibold tabular">
                {money(current?.total ?? 0)}
              </span>
            </div>

            {/* The number the patient actually hands over. Shown as the
                headline once cover applies, because charging the gross
                to a covered patient is the failure this exists to
                prevent. */}
            {cover.data?.covered && (
              <div className="mt-3 border-t border-border pt-2">
                <Row
                  label={`${cover.data.eligibility.scheme} pays`}
                  value={money(cover.data.scheme_amount)}
                />
                <div className="mt-1 flex items-baseline justify-between">
                  <span className="text-section font-semibold text-brand-text">
                    Patient pays
                  </span>
                  <span className="text-hero font-semibold tabular text-brand-text">
                    {money(cover.data.patient_amount)}
                  </span>
                </div>
                <p className="mt-1 text-help text-text-3">
                  {cover.data.eligibility.member_number}
                  {cover.data.model === "CAPITATION" && " · capitation, no claim"}
                </p>
              </div>
            )}
            {current && current.outstanding !== current.total && (
              <Row label="Outstanding" value={money(current.outstanding)} />
            )}
          </div>

          {/* `current` is undefined until the sale loads. Without this
              guard the payment panel renders against nothing and takes
              the whole screen down. */}
          {!current ? null : isDraft ? (
            <Button
              variant="primary"
              className="h-11 w-full"
              disabled={lines.length === 0 || Boolean(blocked) || unacknowledged > 0}
              loading={complete.isPending}
              onClick={() => complete.mutate()}
            >
              Complete sale
            </Button>
          ) : (
            <PaymentPanel sale={current} onPay={(m) => pay.mutate(m)} pending={pay.isPending} />
          )}

          {current?.status === "COMPLETED" && (
            <Button
              className="h-11 w-full"
              onClick={() => {
                setSaleId(null);
                start.mutate();
              }}
            >
              New sale
            </Button>
          )}
        </aside>
      </div>
    </>
  );
}

function Line({ line }: { line: SaleLine }) {
  return (
    <div className="flex items-center gap-3 border-b border-hair px-3 py-2.5 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="truncate text-body">{line.product_name}</p>
        <p className="text-help text-text-2">
          {line.quantity} {line.uom_code.toLowerCase()} ·{" "}
          {/* The batch is shown because FEFO chose it, not the operator. */}
          <span className="font-mono">{line.batch_number}</span>
          {line.requires_prescription && (
            <span className="ml-2 text-warn-text">Prescription only</span>
          )}
        </p>
      </div>
      <span className="tabular text-body font-medium">{money(line.line_total)}</span>
      <button type="button" aria-label="Remove" className="text-text-3 hover:text-bad">
        <Trash2 size={15} strokeWidth={1.8} aria-hidden />
      </button>
    </div>
  );
}

function PaymentPanel({
  sale,
  onPay,
  pending,
}: {
  sale: Sale;
  onPay: (method: string) => void;
  pending: boolean;
}) {
  const awaiting = sale.payments.filter((p) => p.status === "PENDING");

  if (sale.status === "COMPLETED") {
    return (
      <div className="rounded-lg border border-border bg-surface p-4">
        <StatusDot tone="ok">Paid</StatusDot>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {awaiting.length > 0 && (
        /* Honest about the state: the customer is confirming on their
           handset and the money is not in the drawer yet. */
        <div className="flex items-center gap-2 border-l-2 border-warn bg-warn-bg px-3 py-2.5">
          <Loader2 size={15} strokeWidth={1.8} className="animate-spin text-warn" aria-hidden />
          <span className="text-body text-warn-text">Awaiting confirmation</span>
        </div>
      )}
      {(["CASH", "MOBILE_MONEY", "INSURANCE"] as const).map((method) => (
        <Button
          key={method}
          className="h-11 w-full justify-start"
          loading={pending}
          onClick={() => onPay(method)}
        >
          {method === "CASH" ? "Cash" : method === "MOBILE_MONEY" ? "Mobile money" : "Insurance"}
        </Button>
      ))}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-body text-text-2">{label}</span>
      <span className="tabular text-body">{value}</span>
    </div>
  );
}
