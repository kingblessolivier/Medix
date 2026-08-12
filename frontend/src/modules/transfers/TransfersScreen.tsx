/* Stock between this pharmacy's own locations.
 *
 * Not a sale. Moving stock from the back store to the front counter, or
 * between two branches of one organization, never leaves the ledger and
 * never involves money — which is exactly why it is a separate screen
 * from the marketplace rather than a variant of it.
 *
 * A batch is picked explicitly here rather than by FEFO, because the
 * person moving stock is holding a specific carton and the system should
 * record what they actually moved.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Banner,
  Button,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { ApiFailure, api, type StockRow } from "@/lib/api";

const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" });

function expiryTone(days: number): { tone: Tone; label: string } {
  if (days <= 0) return { tone: "bad", label: "Expired" };
  if (days <= 90) return { tone: "warn", label: `${days}d` };
  return { tone: "ok", label: `${days}d` };
}

export function TransfersScreen() {
  const [moving, setMoving] = useState<StockRow | null>(null);
  const [holding, setHolding] = useState<StockRow | null>(null);

  const stock = useQuery({ queryKey: ["stock"], queryFn: () => api.stock() });
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.locations() });

  const columns: Column<StockRow>[] = [
    { key: "product", header: "Product", render: (r) => r.product_name },
    { key: "batch", header: "Batch", mono: true, render: (r) => r.batch_number },
    { key: "location", header: "Location", render: (r) => r.location_name },
    {
      key: "qty",
      header: "Units",
      numeric: true,
      render: (r) => r.quantity_base.toLocaleString(),
    },
    { key: "expiry", header: "Expires", render: (r) => DAY.format(new Date(r.expiry_date)) },
    {
      key: "state",
      header: "",
      render: (r) => {
        const tone = expiryTone(r.days_to_expiry);
        return <StatusPill tone={tone.tone}>{tone.label}</StatusPill>;
      },
    },
  ];

  if (stock.isPending) return <Skeleton className="h-[400px]" />;

  /* Only available stock can be moved or held. Quarantined and recalled
     rows are deliberately absent: the action on those is release or
     disposal, and offering "transfer" would be offering to move a
     problem somewhere else. */
  const rows = (stock.data?.results ?? []).filter(
    (r) => r.status === "AVAILABLE" && r.quantity_base > 0,
  );

  return (
    <>
      <PageHeader
        title="Transfers"
        description="Stock between your own locations."
      />

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        density="compact"
        caption="Stock available to move"
        emptyHeading="Nothing to move"
        emptyBody="Available stock appears here."
        rowActions={[
          { label: "Transfer", onSelect: setMoving },
          // Holding stock takes it out of every allocation, so it reads
          // as the destructive option it is.
          { label: "Quarantine", onSelect: setHolding, danger: true },
        ]}
      />

      <TransferModal
        row={moving}
        locations={locations.data?.results ?? []}
        onClose={() => setMoving(null)}
      />
      <QuarantineModal row={holding} onClose={() => setHolding(null)} />
    </>
  );
}

function TransferModal({
  row,
  locations,
  onClose,
}: {
  row: StockRow | null;
  locations: { id: string; name: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [to, setTo] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState("");

  const destinations = useMemo(
    () => locations.filter((l) => l.id !== row?.location),
    [locations, row?.location],
  );

  const move = useMutation({
    mutationFn: () =>
      api.transferStock({
        batch: row!.batch,
        from_location: row!.location,
        to_location: to,
        quantity: Number(quantity),
        reason,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      setQuantity("");
      setReason("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not moved."),
  });

  if (!row) return null;
  const count = Number(quantity);
  const ready = to && count > 0 && count <= row.quantity_base;

  return (
    <Modal
      open
      title="Transfer stock"
      subtitle={`${row.product_name} · ${row.batch_number}`}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={move.isPending}
          onClick={() => move.mutate()}
        >
          Move stock
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      <div className="flex flex-col gap-4">
        <Field label="From">
          {(id) => <Input id={id} value={row.location_name} disabled readOnly />}
        </Field>

        <Field label="To" required>
          {(id) => (
            <Select id={id} value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Choose a location</option>
              {destinations.map((location) => (
                <option key={location.id} value={location.id}>
                  {location.name}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field
          label="Units"
          help={`${row.quantity_base.toLocaleString()} available`}
          required
        >
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={row.quantity_base}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>

        <Field label="Reason">
          {(id) => (
            <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Field>
      </div>
    </Modal>
  );
}

function QuarantineModal({
  row,
  onClose,
}: {
  row: StockRow | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState("");

  const hold = useMutation({
    mutationFn: () =>
      api.quarantineStock({
        batch: row!.batch,
        location: row!.location,
        quantity: Number(quantity),
        reason,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stock"] });
      setQuantity("");
      setReason("");
      setFailure("");
      onClose();
    },
    onError: (error) =>
      setFailure(error instanceof ApiFailure ? error.error.message : "Not held."),
  });

  if (!row) return null;
  const count = Number(quantity);
  const ready = count > 0 && count <= row.quantity_base && reason.trim().length > 0;

  return (
    <Modal
      open
      title="Quarantine stock"
      subtitle={`${row.product_name} · ${row.batch_number}`}
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          className="w-full"
          disabled={!ready}
          loading={hold.isPending}
          onClick={() => hold.mutate()}
        >
          Hold stock
        </Button>
      }
    >
      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {/* Says what quarantine does, because "hold" could mean reserve. */}
      <Banner tone="warn" className="mb-4">
        Held stock stays on the premises and cannot be sold or shipped.
      </Banner>

      <div className="flex flex-col gap-4">
        <Field label="Units" help={`${row.quantity_base.toLocaleString()} available`} required>
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={row.quantity_base}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="tabular text-right"
            />
          )}
        </Field>

        <Field label="Reason" help="Recorded against the batch." required>
          {(id) => (
            <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Field>
      </div>
    </Modal>
  );
}
