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
import * as offline from "@/lib/offline";
import { startDraining } from "@/lib/sync";
import { Banner, Button, EmptyState, PageHeader, StatusDot } from "@/components/ui";
import { AlertStack } from "@/components/ui/AlertStack";

const CURRENCY = new Intl.NumberFormat("en-RW", { maximumFractionDigits: 0 });

function money(minor: number): string {
  return CURRENCY.format(minor);
}

/** A line rung up with no connection, in the shape the server replays. */
type OfflineLine = {
  product: string;
  product_name: string;
  quantity: number;
  uom_code: string;
  unit_price: number;
};

export function PosScreen({ locationId }: { locationId: string | null }) {
  const queryClient = useQueryClient();
  const [saleId, setSaleId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  /* Lines rung up with no connection. Held here rather than on the
     server for the obvious reason. */
  const [offlineLines, setOfflineLines] = useState<OfflineLine[]>([]);
  const [waiting, setWaiting] = useState(0);
  const [online, setOnline] = useState(() => navigator.onLine);
  const searchRef = useRef<HTMLInputElement>(null);

  const sale = useQuery({
    queryKey: ["sale", saleId],
    queryFn: () => api.sale(saleId!),
    enabled: Boolean(saleId),
  });

  const products = useQuery({
    queryKey: ["pos-products", query],
    /* Scoped to this till's own location. The figure has to be the
       shelf the sale will allocate from — a front counter told there
       are twenty-four when all twenty-four are in the cold room is back
       where it started. */
    queryFn: () =>
      api.products(
        `?search=${encodeURIComponent(query)}&location=${locationId ?? ""}`,
      ),
    enabled: query.trim().length >= 2,
  });

  /* Which till this browser is standing at, and the shift open on it.
     A sale started without a till belongs to no day: the server resolves
     the shift from the till, and day end reads sales through the shift. */
  const shifts = useQuery({ queryKey: ["shifts"], queryFn: () => api.shifts() });
  const openShift = (shifts.data?.results ?? []).find((s) => s.status === "OPEN");

  const start = useMutation({
    mutationFn: () => api.startSale(locationId!, openShift?.till ?? null),
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

  /* The counter when there is no counter to talk to.
   *
   * Journalled with what this screen already knows and replayed through
   * the ordinary service path on reconnection, so the offline route is
   * not the way around the prescription gate. See lib/offline.ts. */
  const offlineSale = useMutation({
    mutationFn: async ({ method }: { method: string }) => {
      const entry = await offline.record("sale", {
        location: locationId!,
        lines: offlineLines.map((line) => ({
          product: line.product,
          quantity: line.quantity,
          uom_code: line.uom_code,
          unit_price: line.unit_price,
        })),
        payments: [{ method, amount: offlineTotal }],
      });
      return entry;
    },
    onSuccess: () => {
      setOfflineLines([]);
      void refreshJournal();
    },
  });

  // Start a sale as soon as the counter is open, so the first scan lands
  // somewhere without an extra click. Guarded by a ref because StrictMode
  // double-invokes effects and would otherwise open two draft sales.
  const starting = useRef(false);
  useEffect(() => {
    // Waits for the shift lookup. Starting before the till is known
    // would put the first sale of the day outside the day.
    if (!locationId || saleId || starting.current || shifts.isPending) return;
    starting.current = true;
    start.mutate(undefined, { onSettled: () => (starting.current = false) });
  }, [locationId, saleId, shifts.isPending]);

  const refreshJournal = async () => {
    if (!offline.journalWorks) return;
    const counted = await offline.counts();
    setWaiting(counted.pending + counted.failed);
  };

  useEffect(() => {
    void refreshJournal();
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    // One drainer for the tab. It backs off when there is nobody
    // listening and tries immediately when the network returns.
    const stop = startDraining("BROWSER-TILL", () => void refreshJournal());
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      stop();
    };
  }, []);

  const offlineTotal = offlineLines.reduce(
    (total, line) => total + line.unit_price * line.quantity,
    0,
  );

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
                products.data?.results.slice(0, 6).map((product) => {
                  /* Two products whose names differ by one word are the
                     normal case at a counter. What separates them is
                     whether either can actually be sold, so that is on
                     the row rather than behind a tap and a red banner. */
                  const none = product.on_hand_base <= 0;
                  return (
                    <button
                      key={product.id}
                      type="button"
                      disabled={none || (!online && product.requires_prescription)}
                      onClick={() =>
                        online
                          ? addLine.mutate(product)
                          : setOfflineLines((current) => [
                              ...current,
                              {
                                product: product.id,
                                product_name: product.name,
                                quantity: 1,
                                uom_code: "UNIT",
                                unit_price: 100,
                              },
                            ])
                      }
                      className="flex w-full items-center justify-between gap-3 border-b border-hair px-3 py-2.5 text-left last:border-0 hover:bg-hover disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-body">{product.name}</span>
                        <span className="block text-help text-text-3">
                          {none
                            ? "None on the shelf"
                            : !online && product.requires_prescription
                              ? "Needs a connection to dispense"
                              : `${product.on_hand_base.toLocaleString()} ${product.base_uom_name.toLowerCase()}`}
                        </span>
                      </span>
                      {product.requires_prescription && (
                        <StatusDot tone="warn">Prescription</StatusDot>
                      )}
                    </button>
                  );
                })
              )}
            </div>
          )}

          {!online ? (
            offlineLines.length === 0 ? (
              <EmptyState
                heading="Nothing scanned"
                body="Sales are held here and sent when the connection returns."
              />
            ) : (
              <div className="overflow-hidden rounded-lg border border-border bg-surface">
                {offlineLines.map((line, index) => (
                  <div
                    key={`${line.product}-${index}`}
                    className="flex items-baseline justify-between gap-3 border-b border-hair px-3 py-2.5 last:border-0"
                  >
                    <span className="min-w-0 truncate text-body">
                      {line.product_name}
                      <span className="ml-2 text-help text-text-3">
                        {line.quantity} × {money(line.unit_price)}
                      </span>
                    </span>
                    <span className="flex items-center gap-3">
                      <span className="tabular text-body">
                        {money(line.unit_price * line.quantity)}
                      </span>
                      <button
                        type="button"
                        aria-label={`Remove ${line.product_name}`}
                        onClick={() =>
                          setOfflineLines((current) =>
                            current.filter((_, i) => i !== index),
                          )
                        }
                        className="text-text-3 hover:text-bad"
                      >
                        <Trash2 size={15} strokeWidth={1.9} aria-hidden />
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            )
          ) : lines.length === 0 ? (
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

          {!online && (
            <Banner tone="warn">
              {`Offline. Selling continues${waiting ? ` · ${waiting} waiting` : ""}.`}
            </Banner>
          )}

          {online && waiting > 0 && (
            <Banner tone="info">{`${waiting} sales still to send.`}</Banner>
          )}

          {!shifts.isPending && !openShift && (
            <Banner tone="warn">
              No shift open. Sales will not appear at day end.
            </Banner>
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

          {/* No connection: the totals and the tender are worked out
              here, and the whole sale is journalled on payment. */}
          {!online ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-baseline justify-between border-t border-border pt-2">
                <span className="text-body font-medium">Total</span>
                <span className="tabular text-metric font-semibold">
                  {money(offlineTotal)}
                </span>
              </div>
              {(["CASH", "MOBILE_MONEY"] as const).map((method) => (
                <Button
                  key={method}
                  variant={method === "CASH" ? "primary" : "secondary"}
                  className="h-11 w-full"
                  disabled={offlineLines.length === 0}
                  loading={offlineSale.isPending}
                  onClick={() => offlineSale.mutate({ method })}
                >
                  {method === "CASH" ? "Take cash" : "Mobile money"}
                </Button>
              ))}
            </div>
          ) : !current ? null : isDraft ? (
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
