/* Guidance — for the pharmacist who has never used an ERP.
 *
 * Medix is bought by pharmacies, not by software teams. The person at the
 * counter did not choose it, was not trained on it, and does not know
 * what a GRN, an ASN or a reservation is. A system that assumes otherwise
 * gets used wrongly and then gets blamed.
 *
 * Three pieces, and each has a strict job:
 *
 *   Help      one sentence, on demand, next to the term that needs it.
 *   NextAction  what to do now, and the one button that does it.
 *   Timeline  where this transaction has got to.
 *
 * None of them explain the system in general. docs/23 still holds — the
 * interface states, it does not teach. What these do is name the thing in
 * front of the user in words they already have, which is different from
 * a manual bolted onto a screen.
 */

import clsx from "clsx";
import { Check, CircleHelp, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui";

/* -- Help -------------------------------------------------------------- */

/* A term the user may not know, and one sentence about it.
 *
 * On demand rather than always visible: a permanent paragraph beside
 * every label is the thing people learn to stop reading. Click, not
 * hover — a pharmacist on a tablet has no hover, and the answer must not
 * vanish while they read it. */

export function Help({ term, children }: { term: string; children: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function away(event: MouseEvent) {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <span ref={wrap} className="relative inline-flex items-center gap-1">
      {term}
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-label={`What is ${term}`}
        aria-expanded={open}
        aria-controls={id}
        className="rounded-sm text-text-3 hover:text-text-2"
      >
        <CircleHelp size={13} strokeWidth={1.9} aria-hidden />
      </button>

      {open && (
        <span
          id={id}
          role="note"
          className={clsx(
            "absolute left-0 top-full z-dropdown mt-1 w-64 rounded-md border border-border",
            "bg-surface p-2.5 text-help font-normal normal-case text-text-2 shadow-e2",
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}

/* -- Next action ------------------------------------------------------- */

/* What happens next, and the button that does it.
 *
 * A status tells the user where the transaction is. It does not tell
 * them what is now expected of them, and "Approved" is not an
 * instruction. This is: one sentence, one button, no choices. */

export function NextAction({
  heading,
  detail,
  action,
  tone = "info",
}: {
  /** The instruction. Verb first, three words. */
  heading: string;
  /** Why, or what it will do. Eight words. */
  detail?: string;
  action?: ReactNode;
  /** `waiting` when the next move belongs to somebody else. */
  tone?: "info" | "waiting";
}) {
  return (
    <div
      className={clsx(
        "mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3",
        tone === "waiting"
          ? "border-border bg-app"
          : "border-info bg-info-bg",
      )}
    >
      <div className="min-w-0">
        <p className="text-body font-medium text-text">{heading}</p>
        {detail && <p className="text-help text-text-2">{detail}</p>}
      </div>
      {action}
    </div>
  );
}

/* -- Timeline ---------------------------------------------------------- */

export type Step = {
  id: string;
  /** Plain language. "Sent to depot", not "SUBMITTED". */
  label: string;
  /** When it happened. Absent while the step is still ahead. */
  at?: string | null;
};

/* Where the transaction has got to.
 *
 * A purchase order passes through seven states and a status chip shows
 * one of them. The user's real question is not "what state is this" but
 * "what has happened and what has not", and that needs the whole line. */

export function Timeline({
  steps,
  current,
}: {
  steps: Step[];
  /** Index of the step in progress. Everything before it is done. */
  current: number;
}) {
  return (
    <ol className="mb-5 flex flex-wrap items-center gap-x-1 gap-y-2">
      {steps.map((step, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={step.id} className="flex items-center gap-1">
            <span
              className={clsx(
                "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-label",
                done && "bg-ok-bg text-ok-text",
                active && "bg-info-bg text-info-text font-medium",
                !done && !active && "text-text-3",
              )}
            >
              <StepMark done={done} active={active} />
              {step.label}
              {step.at && <span className="text-text-3">· {step.at}</span>}
            </span>
            {index < steps.length - 1 && (
              <span className="h-px w-3 bg-border" aria-hidden />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepMark({ done, active }: { done: boolean; active: boolean }) {
  if (done) return <Check size={11} strokeWidth={2.4} aria-hidden />;
  return (
    <span
      aria-hidden
      className={clsx(
        "size-1.5 rounded-full",
        active ? "bg-info" : "border border-current",
      )}
    />
  );
}

/* -- Confirmation ------------------------------------------------------ */

/* "Are you sure" is banned by docs/23 because it asks a question the user
 * cannot answer — sure about what? This states the consequence instead,
 * and the confirming button repeats the verb rather than saying "OK". */

export function Consequence({
  icon: Icon,
  lines,
}: {
  icon?: LucideIcon;
  /** One line per effect. "Reverses 3 stock movements." */
  lines: string[];
}) {
  return (
    <ul className="flex flex-col gap-1.5">
      {lines.map((line) => (
        <li key={line} className="flex items-start gap-2 text-body text-text-2">
          {Icon && (
            <Icon size={14} strokeWidth={1.9} className="mt-0.5 shrink-0" aria-hidden />
          )}
          {line}
        </li>
      ))}
    </ul>
  );
}

/* -- Success ----------------------------------------------------------- */

/* Not "Success!" — what was created, and what can be done with it now. */

export function Created({
  what,
  number,
  detail,
  actions,
}: {
  /** "Purchase order", "Delivery note". */
  what: string;
  number: string;
  detail?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-6 text-center">
      <span className="flex size-9 items-center justify-center rounded-full bg-ok-bg text-ok-text">
        <Check size={18} strokeWidth={2.2} aria-hidden />
      </span>
      <div>
        <p className="text-section font-semibold text-text">{what} created</p>
        <p className="font-mono text-body text-text-2">{number}</p>
        {detail && <p className="mt-1 text-help text-text-2">{detail}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

/* Re-exported so a screen can build a whole confirmation without
 * importing from two places. */
export { Button };
