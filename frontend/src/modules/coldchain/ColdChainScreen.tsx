/* Cold chain — the fridges, and what happened in them.
 *
 * This screen exists because the excursion is the one alert in Medix
 * that **acts**. Everything else warns and lets a person decide; a
 * sustained temperature fault quarantines the cold-chain stock in that
 * location on its own, without asking, because by the time somebody
 * reads a warning the vaccine is already damaged.
 *
 * That makes this the screen a pharmacist most needs and had least of:
 * stock goes unsellable and something has to say why, and let somebody
 * decide about it. Until now the whole capability existed server-side
 * with nothing to show it.
 *
 * Recovery closes an excursion and deliberately does not release the
 * stock. "The fridge came back" is not the same statement as "the
 * insulin is still good", and only one of them is a pharmacist's to
 * make. So resolving is a written decision, and releasing the batch is a
 * separate act on the inventory screen.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiFailure, api, type Excursion, type Sensor } from "@/lib/api";
import { DataTable, TableTabs, type Column, type TableTab } from "@/components/data/DataTable";
import {
  Badge,
  Banner,
  Button,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Skeleton,
  StatusPill,
  type Tone,
} from "@/components/ui";
import { DetailList, Modal } from "@/components/ui/Modal";
import { Consequence, Help, NextAction } from "@/components/ui/Guidance";

const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

const when = (iso: string | null) => (iso ? WHEN.format(new Date(iso)) : "—");

/** Hours, then days. "73 hours ago" is arithmetic the reader shouldn't do. */
function ago(iso: string | null): string {
  if (!iso) return "never";
  const hours = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 === 0 ? `${hours}h` : `${hours}h ${minutes % 60}m`;
}

const VIEWS: { id: string; label: string; open?: boolean }[] = [
  { id: "unresolved", label: "Needs a decision" },
  { id: "all", label: "All" },
];

export function ColdChainScreen() {
  const [view, setView] = useState("unresolved");
  const [selected, setSelected] = useState<Excursion | null>(null);

  const excursions = useQuery({
    queryKey: ["excursions"],
    queryFn: () => api.excursions(),
  });
  const sensors = useQuery({ queryKey: ["sensors"], queryFn: () => api.sensors() });

  if (excursions.isPending) return <Skeleton className="h-[400px]" />;
  if (excursions.isError) {
    return (
      <>
        <PageHeader title="Cold chain" />
        <ErrorState
          message="Couldn't load excursions."
          onRetry={() => excursions.refetch()}
        />
      </>
    );
  }

  const all = excursions.data.results;
  const undecided = all.filter((e) => !e.resolved_at);
  const rows = view === "unresolved" ? undecided : all;
  const stillOpen = all.filter((e) => e.is_open);
  const held = undecided.reduce((total, e) => total + e.quarantined_base, 0);

  const tabs: TableTab[] = VIEWS.map((v) => ({
    id: v.id,
    label: v.label,
    count: v.id === "unresolved" ? undecided.length : all.length,
  }));

  const columns: Column<Excursion>[] = [
    { key: "where", header: "Fridge", render: (e) => e.location_name },
    { key: "sensor", header: "Probe", render: (e) => e.sensor_name },
    { key: "started", header: "Started", render: (e) => when(e.started_at) },
    {
      key: "duration",
      header: "Out of range",
      render: (e) => duration(e.duration_minutes),
    },
    {
      key: "peak",
      header: "Peak",
      numeric: true,
      render: (e) => `${e.peak_celsius}°C`,
    },
    {
      key: "held",
      header: "Held",
      numeric: true,
      render: (e) =>
        e.quarantined_base > 0
          ? `${e.quarantined_base.toLocaleString()} · ${e.batches_affected} batch${
              e.batches_affected === 1 ? "" : "es"
            }`
          : "—",
    },
    {
      key: "state",
      header: "State",
      render: (e) => {
        const state: { tone: Tone; label: string } = e.is_open
          ? { tone: "bad", label: "Still out of range" }
          : e.resolved_at
            ? { tone: "ok", label: "Decided" }
            : { tone: "warn", label: "Recovered" };
        return <StatusPill tone={state.tone}>{state.label}</StatusPill>;
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Cold chain"
        description="Fridges, faults and the stock they held"
      />

      {stillOpen.length > 0 ? (
        <NextAction
          heading={`Check ${stillOpen.length} fridge${stillOpen.length === 1 ? "" : "s"}`}
          detail="Still out of range. The stock inside is already held."
        />
      ) : undecided.length > 0 ? (
        <NextAction
          heading={`Decide on ${undecided.length} excursion${undecided.length === 1 ? "" : "s"}`}
          detail={`${held.toLocaleString()} base units are held until you do.`}
          action={
            <Button variant="primary" onClick={() => setSelected(undecided[0])}>
              Open oldest
            </Button>
          }
        />
      ) : null}

      <Probes sensors={sensors.data?.results ?? []} />

      <TableTabs tabs={tabs} active={view} onChange={setView} />
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(e) => e.id}
        density="compact"
        onRowClick={setSelected}
        caption="Temperature excursions"
        emptyHeading="No excursions"
        emptyBody="Fridges have stayed in range."
      />

      <ExcursionModal excursion={selected} onClose={() => setSelected(null)} />
    </>
  );
}

/* A probe that stopped reporting is a fridge nobody is watching, which
   looks exactly like a fridge with no problems. Silence gets its own
   line for that reason. */
function Probes({ sensors }: { sensors: Sensor[] }) {
  if (sensors.length === 0) return null;
  return (
    <div className="mb-5 flex flex-wrap gap-2">
      {sensors.map((sensor) => {
        const hours = sensor.last_seen_at
          ? (Date.now() - new Date(sensor.last_seen_at).getTime()) / 3_600_000
          : Infinity;
        const quiet = hours > 2;
        return (
          <span
            key={sensor.id}
            className={
              "flex items-center gap-2 rounded-md border px-3 py-1.5 text-help " +
              (quiet ? "border-warn bg-warn-bg text-warn-text" : "border-border text-text-2")
            }
          >
            <span className="font-medium text-text">{sensor.location_name}</span>
            <span>
              {sensor.minimum_c}–{sensor.maximum_c}°C
            </span>
            <span>{quiet ? `Silent ${ago(sensor.last_seen_at)}` : ago(sensor.last_seen_at)}</span>
          </span>
        );
      })}
    </div>
  );
}

function ExcursionModal({
  excursion,
  onClose,
}: {
  excursion: Excursion | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [resolution, setResolution] = useState("");
  const [failure, setFailure] = useState("");

  useEffect(() => {
    setResolution("");
    setFailure("");
  }, [excursion?.id]);

  const resolve = useMutation({
    mutationFn: () => api.resolveExcursion(excursion!.id, resolution),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["excursions"] });
      onClose();
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Couldn't record it.",
      ),
  });

  if (!excursion) return null;
  const decided = Boolean(excursion.resolved_at);

  return (
    <Modal
      open
      title={excursion.location_name}
      subtitle={`Out of range from ${when(excursion.started_at)}`}
      onClose={onClose}
      footer={
        decided ? undefined : (
          <Button
            variant="primary"
            className="w-full"
            disabled={!resolution.trim()}
            loading={resolve.isPending}
            onClick={() => resolve.mutate()}
          >
            Record decision
          </Button>
        )
      }
    >
      {excursion.is_open && (
        <Banner tone="bad" className="mb-4">
          Still out of range. Check the fridge before deciding.
        </Banner>
      )}

      <DetailList
        rows={[
          ["Probe", excursion.sensor_name],
          ["Started", when(excursion.started_at)],
          ["Recovered", when(excursion.ended_at)],
          ["Out of range", duration(excursion.duration_minutes)],
          ["Peak", `${excursion.peak_celsius}°C`],
          ["Lowest", `${excursion.minimum_celsius}°C`],
          ["Readings", excursion.reading_count.toLocaleString()],
          [
            (
              <Help term="Held">
                Cold-chain stock in this fridge, quarantined automatically when the
                fault passed the grace window.
              </Help>
            ) as unknown as string,
            `${excursion.quarantined_base.toLocaleString()} · ${excursion.batches_affected} batch${
              excursion.batches_affected === 1 ? "" : "es"
            }`,
          ],
        ]}
      />

      {decided ? (
        <div className="mt-5">
          <h3 className="mb-1 text-section font-semibold">Decision</h3>
          <p className="text-body text-text-2">{excursion.resolution}</p>
          <p className="mt-1 text-help text-text-3">{when(excursion.resolved_at)}</p>
        </div>
      ) : (
        <div className="mt-5">
          {/* Recovery closed the excursion and left the stock held on
              purpose: "the fridge came back" and "the insulin is still
              good" are different statements, and only one of them is a
              pharmacist's to make. */}
          <Consequence
            lines={[
              "Records what was decided about the batches.",
              "Stock stays held. Release it on the inventory screen.",
            ]}
          />
          <div className="mt-3">
            <Field
              label="Decision"
              help="What the manufacturer's tolerance says, and what you concluded."
              required
            >
              {(id) => (
                <Input
                  id={id}
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  placeholder="Within tolerance"
                />
              )}
            </Field>
          </div>
          {failure && (
            <Banner tone="bad" className="mt-3">
              {failure}
            </Banner>
          )}
        </div>
      )}

      {!decided && excursion.quarantined_base > 0 && (
        <Badge tone="warn">Held until decided</Badge>
      )}
    </Modal>
  );
}
