/* Draining the browser journal to the cloud.
 *
 * The same contract the site agent uses — `/api/v1/sync/`, one envelope
 * per operation, a client-generated id, and the server answering a repeat
 * with the original result rather than applying it twice. That is what
 * lets this retry blindly, and a till that has to reason about what got
 * through is a till that will get it wrong during the one outage that
 * matters.
 *
 * The replayed sale is rebuilt through `sales.services` on the server,
 * never inserted. So an offline sale meets the same prescription gate,
 * the same FEFO allocation and the same controlled register as one rung
 * up live: the offline path is not the way around any rule.
 *
 * Backoff is jittered for the same reason the agent's is — every till in
 * the country reconnects when the same network comes back.
 */

import { request, tokens } from "@/lib/api";
import { markFailed, markSent, pending } from "@/lib/offline";

/** Seconds. Caps at five minutes. */
const BACKOFF = [5, 15, 45, 120, 300];

type SyncResult = {
  accepted: number;
  results: {
    client_id: string;
    status: "APPLIED" | "DUPLICATE" | "FAILED";
    result: unknown;
    error: string;
  }[];
};

export type DrainOutcome = {
  sent: number;
  failed: number;
  waiting: number;
  offline?: boolean;
};

/** Send everything pending, in one batch.
 *
 * Batched because the interesting case is a reconnection after hours
 * offline, and one request per journalled sale would turn that into a
 * stampede from every till at once. */
export async function drain(device: string): Promise<DrainOutcome> {
  const entries = await pending();
  if (entries.length === 0) return { sent: 0, failed: 0, waiting: 0 };

  let answer: SyncResult;
  try {
    answer = await request<SyncResult>("/sync/", {
      method: "POST",
      body: {
        device,
        envelopes: entries.map((entry) => ({
          client_id: entry.client_id,
          sequence: entry.sequence,
          kind: entry.kind,
          payload: entry.payload,
          occurred_at: entry.occurred_at,
        })),
      },
    });
  } catch {
    // Offline, or the server refused the batch. Nothing is marked
    // failed: the entries are simply still pending, and counting an
    // outage as a failure would burn the retry budget on the network
    // being down — which is the condition this exists to survive.
    return { sent: 0, failed: 0, waiting: entries.length, offline: true };
  }

  let sent = 0;
  let failed = 0;
  for (const row of answer.results) {
    if (row.status === "APPLIED" || row.status === "DUPLICATE") {
      await markSent(row.client_id, row.result);
      sent += 1;
    } else {
      await markFailed(row.client_id, row.error || "Unknown error");
      failed += 1;
    }
  }
  return { sent, failed, waiting: entries.length - sent - failed };
}

/** Drain whenever there is something to send and somewhere to send it. */
export function startDraining(
  device: string,
  onOutcome?: (outcome: DrainOutcome) => void,
): () => void {
  let attempt = 0;
  let timer: number | undefined;
  let stopped = false;

  async function tick() {
    if (stopped || !tokens.access) {
      timer = window.setTimeout(tick, 30_000);
      return;
    }
    const outcome = await drain(device).catch(
      (): DrainOutcome => ({ sent: 0, failed: 0, waiting: 0, offline: true }),
    );
    onOutcome?.(outcome);

    let delay = 30_000;
    if (outcome.offline) {
      const seconds = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      // Jittered: every till reconnects when the same network comes
      // back, and a fleet retrying in lockstep is a denial of service
      // the operator inflicts on themselves.
      delay = (seconds + Math.random() * seconds * 0.3) * 1000;
      attempt += 1;
    } else {
      attempt = 0;
    }
    if (!stopped) timer = window.setTimeout(tick, delay);
  }

  // The browser tells us when the network returns; take it as a hint to
  // try now rather than waiting out the backoff.
  const onOnline = () => {
    attempt = 0;
    window.clearTimeout(timer);
    void tick();
  };
  window.addEventListener("online", onOnline);
  void tick();

  return () => {
    stopped = true;
    window.clearTimeout(timer);
    window.removeEventListener("online", onOnline);
  };
}
