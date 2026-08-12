/* Where an order has been, as both sides see it.
 *
 * A vertical rail rather than a table: the sequence is the information,
 * and a table invites sorting a sequence out of order.
 *
 * "You" against your own organization and the counterparty by name —
 * a timeline that says "ABC Wholesale confirmed" to ABC Wholesale reads
 * as though someone else did it.
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
import type { Tone } from "@/components/ui";

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

const STEP: Record<string, { icon: LucideIcon; tone: Tone }> = {
  DRAFT: { icon: Pencil, tone: "neutral" },
  PENDING_APPROVAL: { icon: Send, tone: "warn" },
  REJECTED: { icon: Undo2, tone: "bad" },
  SUBMITTED: { icon: Send, tone: "warn" },
  CONFIRMED: { icon: CircleCheck, tone: "info" },
  PREPARING: { icon: PackageCheck, tone: "info" },
  PARTIALLY_DISPATCHED: { icon: Truck, tone: "warn" },
  DISPATCHED: { icon: Truck, tone: "info" },
  PARTIALLY_RECEIVED: { icon: Check, tone: "warn" },
  RECEIVED: { icon: Check, tone: "ok" },
  CANCELLED: { icon: Ban, tone: "neutral" },
};

const MARK: Record<Tone, string> = {
  ok: "border-ok text-ok",
  warn: "border-warn text-warn",
  bad: "border-bad text-bad",
  info: "border-info text-info",
  brand: "border-brand text-brand",
  neutral: "border-border text-text-3",
};

export function OrderTimeline({
  events,
  viewerOrganization,
}: {
  events: OrderEvent[];
  /** The reader's own organization id, so their side reads as "You". */
  viewerOrganization?: string;
}) {
  if (events.length === 0) {
    return <p className="text-body text-text-3">No history.</p>;
  }

  return (
    <ol className="flex flex-col">
      {events.map((event, index) => {
        const step = STEP[event.to_status] ?? STEP.DRAFT;
        const Icon = step.icon;
        const last = index === events.length - 1;
        const mine =
          viewerOrganization != null &&
          event.actor_organization === viewerOrganization;
        const who = mine ? "You" : event.actor_organization_name || "";

        return (
          <li key={event.id} className="flex gap-3">
            {/* Rail: the mark, and the line to the next step. The line is
                hidden on the last row so the sequence has an end. */}
            <div className="flex flex-col items-center">
              <span
                aria-hidden
                className={clsx(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border bg-surface",
                  MARK[step.tone],
                )}
              >
                <Icon size={13} strokeWidth={2} />
              </span>
              {!last && <span aria-hidden className="w-px flex-1 bg-border" />}
            </div>

            <div className={clsx("min-w-0 flex-1", last ? "pb-0" : "pb-4")}>
              <p className="text-body text-text">{event.to_status_label}</p>
              <p className="text-help text-text-3">
                {WHEN.format(new Date(event.occurred_at))}
                {who && ` · ${who}`}
                {event.actor_name && ` · ${event.actor_name}`}
              </p>
              {event.note && (
                <p className="mt-1 text-help text-text-2">{event.note}</p>
              )}
              {event.document_number && (
                <p className="mt-1 inline-flex items-center gap-1 text-help text-text-2">
                  <FileText size={12} strokeWidth={2} aria-hidden />
                  {event.document_number}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
