/* Alerts, with the fatigue rules enforced in the component.
 *
 * Staff who meet six warnings per sale stop reading warnings, and then
 * the system is worse than one with none — everybody, including the
 * pharmacist, believes the checks are working. So the limit is not a
 * guideline a screen may exceed: it lives here, and a screen that hands
 * this twelve alerts still shows three.
 *
 * Nothing floats. A toast disappears, cannot be re-read, and does not
 * survive a page change, so anything requiring action is inline and
 * attached to the thing it is about.
 *
 * See docs/29-alerts.md.
 */

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Banner, Button, type Tone } from "@/components/ui";
import type { Alert } from "@/lib/api";

const MAX_VISIBLE = 3;

const TONE: Record<string, Tone> = {
  CRITICAL: "bad",
  WARNING: "warn",
  INFO: "info",
};

const RANK = ["CRITICAL", "WARNING", "INFO"];

export function AlertStack({
  alerts,
  onAcknowledge,
  className,
}: {
  alerts: Alert[];
  /** Present only where the screen can actually accept a warning. */
  onAcknowledge?: (alert: Alert) => void;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (alerts.length === 0) return null;

  const ranked = [...alerts].sort(
    (a, b) => RANK.indexOf(a.severity) - RANK.indexOf(b.severity),
  );
  const shown = expanded ? ranked : ranked.slice(0, MAX_VISIBLE);
  const hidden = ranked.length - shown.length;

  return (
    <div className={className}>
      <div className="flex flex-col gap-2">
        {shown.map((alert) => (
          <Banner
            key={`${alert.code}-${alert.subject_id}`}
            tone={TONE[alert.severity] ?? "neutral"}
            action={
              /* Only a warning can be accepted. Critical has no
                 "proceed anyway" — a case that needs one is a warning
                 with an approver, not a critical. */
              alert.severity === "WARNING" && onAcknowledge ? (
                <Button variant="tertiary" onClick={() => onAcknowledge(alert)}>
                  Accept
                </Button>
              ) : undefined
            }
          >
            {alert.title}
            {alert.detail && (
              <span className="ml-1 text-text-2">{alert.detail}</span>
            )}
          </Banner>
        ))}
      </div>

      {hidden > 0 && (
        <Button
          variant="tertiary"
          className="mt-2"
          icon={<ChevronDown size={16} strokeWidth={1.9} aria-hidden />}
          onClick={() => setExpanded(true)}
        >
          {`${hidden} more`}
        </Button>
      )}
    </div>
  );
}
