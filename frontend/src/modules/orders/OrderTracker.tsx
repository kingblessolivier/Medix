/* Where the order is, and what is still ahead of it.
 *
 * The event log this replaces answered "what has happened". The question
 * a pharmacist actually arrives with is "where is my order" — and that
 * one needs the steps **not** yet reached as much as the ones that are.
 * A list of three completed events looks identical whether the next move
 * is tomorrow's delivery or a depot that has not accepted it.
 *
 * Vertical because the labels are sentences, not words. Laid out
 * horizontally, "Awaiting confirmation" either truncates or forces every
 * column to the width of the longest one, and each step here also
 * carries its time, who did it and the document it produced.
 *
 * On colour: the usual shipment tracker paints the steps ahead in red.
 * Not here — red is reserved for status, and a step that has not
 * happened yet is not a fault. Done is green, the current step is the
 * brand colour, and what is ahead is simply quiet. The one genuinely red
 * state is an order sent back or cancelled, which really did go wrong.
 *
 * See docs/22-components.md and docs/23-ui-copy.md.
 */

import clsx from "clsx";
import {
  Ban,
  Check,
  CircleCheck,
  FileText,
  PackageCheck,
  Pencil,
  Send,
  Truck,
  Undo2,
  type LucideIcon,
} from "lucide-react";

import type { OrderEvent } from "@/lib/api";

const WHEN = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kigali",
});

/* Stored UTC, read in Kigali. A pharmacist reconciling a delivery note
   against a screen is standing in Rwanda, not in UTC. */

type Step = {
  id: string;
  /** Plain language. "Sent to depot", not "SUBMITTED". */
  label: string;
  icon: LucideIcon;
  /** Statuses that count as having reached this step. */
  reached: string[];
};

/* The whole journey, in order. A purchase order's route is fixed, which
   is what makes the steps ahead knowable at all. */
const ROUTE: Step[] = [
  { id: "raised", label: "Raised", icon: Pencil, reached: ["DRAFT"] },
  {
    id: "approval",
    label: "With the owner",
    icon: Send,
    reached: ["PENDING_APPROVAL"],
  },
  { id: "sent", label: "Sent to depot", icon: Send, reached: ["SUBMITTED"] },
  { id: "accepted", label: "Accepted", icon: CircleCheck, reached: ["CONFIRMED"] },
  { id: "picking", label: "Being picked", icon: PackageCheck, reached: ["PREPARING"] },
  {
    id: "shipped",
    label: "Shipped",
    icon: Truck,
    reached: ["PARTIALLY_DISPATCHED", "DISPATCHED"],
  },
  {
    id: "received",
    label: "Received",
    icon: Check,
    reached: ["PARTIALLY_RECEIVED", "RECEIVED"],
  },
];

/* An order that was sent back or cancelled did not carry on down the
   route, and showing the rest greyed out would suggest it might. */
const ENDED: Record<string, { label: string; icon: LucideIcon }> = {
  REJECTED: { label: "Sent back", icon: Undo2 },
  CANCELLED: { label: "Cancelled", icon: Ban },
};

export function OrderTracker({
  events,
  status,
  viewerOrganization,
}: {
  events: OrderEvent[];
  /** Where the order is now. Decides which step is current. */
  status: string;
  /** The reader's own organization id, so their side reads as "You". */
  viewerOrganization?: string;
}) {
  /* The event that took the order to each step, where it happened. A
     step reached twice — a part shipment then the rest — keeps the
     first, because that is when it started. */
  const arrival = new Map<string, OrderEvent>();
  for (const event of events) {
    const step = ROUTE.find((s) => s.reached.includes(event.to_status));
    if (step && !arrival.has(step.id)) arrival.set(step.id, event);
  }

  const ending = ENDED[status];
  const endedAt = ending
    ? events.filter((e) => e.to_status === status).slice(-1)[0]
    : undefined;

  /* Everything up to and including the current status is done. Where the
     order ended early, the route stops at the last step it reached. */
  const currentIndex = ROUTE.findIndex((step) => step.reached.includes(status));
  const reachedIndex = ending
    ? Math.max(...[...arrival.keys()].map((id) => ROUTE.findIndex((s) => s.id === id)), 0)
    : currentIndex;

  const visible = ending ? ROUTE.slice(0, reachedIndex + 1) : ROUTE;

  return (
    <ol className="flex flex-col">
      {visible.map((step, index) => {
        const event = arrival.get(step.id);
        const done = Boolean(event) && index < reachedIndex;
        const current = !ending && index === currentIndex;
        const ahead = !done && !current;
        const last = index === visible.length - 1 && !ending;

        return (
          <Row
            key={step.id}
            icon={step.icon}
            label={step.label}
            tone={done ? "done" : current ? "current" : "ahead"}
            last={last}
            event={ahead ? undefined : event}
            viewerOrganization={viewerOrganization}
          />
        );
      })}

      {ending && (
        <Row
          icon={ending.icon}
          label={ending.label}
          tone="stopped"
          last
          event={endedAt}
          viewerOrganization={viewerOrganization}
        />
      )}
    </ol>
  );
}

const MARK: Record<string, string> = {
  done: "border-ok bg-ok-bg text-ok-text",
  current: "border-brand bg-brand-weak text-brand-text",
  ahead: "border-border bg-surface text-text-3",
  stopped: "border-bad bg-bad-bg text-bad-text",
};

const LINE: Record<string, string> = {
  done: "bg-ok",
  current: "bg-border",
  ahead: "bg-border",
  stopped: "bg-border",
};

const LABEL: Record<string, string> = {
  done: "text-text",
  current: "text-text font-medium",
  ahead: "text-text-3",
  stopped: "text-bad-text font-medium",
};

function Row({
  icon: Icon,
  label,
  tone,
  last,
  event,
  viewerOrganization,
}: {
  icon: LucideIcon;
  label: string;
  tone: "done" | "current" | "ahead" | "stopped";
  last: boolean;
  event?: OrderEvent;
  viewerOrganization?: string;
}) {
  const mine =
    event && viewerOrganization != null && event.actor_organization === viewerOrganization;
  const who = event ? (mine ? "You" : event.actor_organization_name || "") : "";

  return (
    <li className="flex gap-3">
      {/* The rail. The line above a done step is green, so the completed
          run reads as one length rather than seven separate marks. */}
      <div className="flex flex-col items-center">
        <span
          aria-hidden
          className={clsx(
            "flex size-7 shrink-0 items-center justify-center rounded-full border",
            MARK[tone],
          )}
        >
          <Icon size={14} strokeWidth={2} />
        </span>
        {!last && (
          <span aria-hidden className={clsx("w-0.5 flex-1 rounded-full", LINE[tone])} />
        )}
      </div>

      <div className={clsx("min-w-0 flex-1", last ? "pb-0" : "pb-5")}>
        <p className={clsx("text-body", LABEL[tone])}>
          {label}
          {tone === "current" && (
            <span className="ml-2 text-help font-normal text-text-2">now</span>
          )}
        </p>

        {event ? (
          <>
            <p className="text-help text-text-3">
              {WHEN.format(new Date(event.occurred_at))}
              {who && ` · ${who}`}
              {event.actor_name && ` · ${event.actor_name}`}
            </p>
            {event.note && <p className="mt-1 text-help text-text-2">{event.note}</p>}
            {event.document_number && (
              <p className="mt-1 inline-flex items-center gap-1 text-help text-text-2">
                <FileText size={12} strokeWidth={2} aria-hidden />
                {event.document_number}
              </p>
            )}
          </>
        ) : (
          /* Says nothing rather than "pending". The quiet mark already
             says it has not happened, and a word repeating that is a
             word to read on every step that has not happened. */
          <p className="text-help text-text-3">&nbsp;</p>
        )}
      </div>
    </li>
  );
}
