/* The counter keeps selling when the connection does not.
 *
 * F12, and half of it already existed: the site agent journals to SQLite
 * and replays through `/sync/`, with 76 tests behind it. What was missing
 * is the half a pharmacy actually touches — the browser. A till that
 * stops taking money the moment the network blinks is a till nobody
 * trusts, and "we have an offline agent" is no answer to the person
 * holding the cash.
 *
 * So the browser journals the same shape the agent does, into IndexedDB,
 * and replays it through the same endpoint. One envelope format, one
 * server contract, one set of rules — the offline path must not become
 * the way around the prescription gate, and it cannot be, because the
 * server rebuilds every replayed sale through `sales.services`.
 *
 * IndexedDB rather than localStorage: localStorage is synchronous, capped
 * around five megabytes, and a shared string namespace. A day of trading
 * is thousands of rows and losing them to a quota error is the exact
 * failure this exists to prevent.
 *
 * **Nothing is deleted on send.** An entry moves to `sent` with the
 * server's answer beside it, for the same reason the agent keeps its
 * journal: a pharmacy asked to prove what its till recorded during a
 * four-hour outage needs the record, and a queue that erases on success
 * cannot answer.
 */

const DB_NAME = "medix.journal";
const STORE = "envelopes";
const VERSION = 1;

export type JournalState = "pending" | "sent" | "failed";

export type Envelope = {
  /** Generated here. The idempotency key for the whole system. */
  client_id: string;
  /** Per-device counter, so a gap is visible. */
  sequence: number;
  kind: "sale" | "temperature" | "stock_count";
  payload: Record<string, unknown>;
  /** When it happened, not when it sent. */
  occurred_at: string;
  state: JournalState;
  attempts: number;
  result?: unknown;
  error?: string;
  sent_at?: string;
};

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "client_id" });
        store.createIndex("state", "state");
        store.createIndex("sequence", "sequence");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await open();
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = work(transaction.objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => db.close();
  });
}

/** Available at all? A private window or an old browser may say no. */
export const journalWorks = typeof indexedDB !== "undefined";

let nextSequence = 0;

/** Journal one operation. Returns immediately; sending is separate. */
export async function record(
  kind: Envelope["kind"],
  payload: Record<string, unknown>,
  occurredAt?: Date,
): Promise<Envelope> {
  if (nextSequence === 0) {
    const existing = await all();
    nextSequence = existing.reduce((top, e) => Math.max(top, e.sequence), 0);
  }
  const envelope: Envelope = {
    // Generated here, not by the server — the whole point is that this
    // works with no server reachable.
    client_id: crypto.randomUUID().replace(/-/g, ""),
    sequence: ++nextSequence,
    kind,
    payload,
    occurred_at: (occurredAt ?? new Date()).toISOString(),
    state: "pending",
    attempts: 0,
  };
  await withStore("readwrite", (store) => store.put(envelope));
  return envelope;
}

export async function all(): Promise<Envelope[]> {
  return withStore("readonly", (store) => store.getAll() as IDBRequest<Envelope[]>);
}

/** The next batch to send, oldest first.
 *
 * Failed entries are included so a transient error retries, but they sort
 * by sequence like everything else — a poisoned payload must not jump the
 * queue on every pass. */
export async function pending(limit = 100): Promise<Envelope[]> {
  const rows = await all();
  return rows
    .filter((e) => e.state === "pending" || e.state === "failed")
    .sort((a, b) => a.sequence - b.sequence)
    .slice(0, limit);
}

export async function counts(): Promise<Record<JournalState, number>> {
  const rows = await all();
  return {
    pending: rows.filter((e) => e.state === "pending").length,
    sent: rows.filter((e) => e.state === "sent").length,
    failed: rows.filter((e) => e.state === "failed").length,
  };
}

/** Kept, not deleted. The journal is the pharmacy's own record. */
export async function markSent(clientId: string, result: unknown): Promise<void> {
  const rows = await all();
  const found = rows.find((e) => e.client_id === clientId);
  if (!found) return;
  await withStore("readwrite", (store) =>
    store.put({
      ...found,
      state: "sent" as const,
      result,
      error: "",
      sent_at: new Date().toISOString(),
    }),
  );
}

export async function markFailed(clientId: string, error: string): Promise<void> {
  const rows = await all();
  const found = rows.find((e) => e.client_id === clientId);
  if (!found) return;
  await withStore("readwrite", (store) =>
    store.put({
      ...found,
      state: "failed" as const,
      attempts: found.attempts + 1,
      error: error.slice(0, 1000),
    }),
  );
}
