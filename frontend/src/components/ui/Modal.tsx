/* Modal — inspection without losing context.
 *
 * SEARCH → TABLE → MODAL → FULL TRANSACTION. A modal never opens another
 * modal; if that is needed, the flow belongs on a page.
 *
 * This was a right-hand drawer. It is centred now because a panel pinned
 * to one edge reads as a sidebar the eye has to travel to, while the row
 * that opened it sits abandoned on the far side of the screen. Centred,
 * the detail arrives where the user is already looking.
 *
 * Height is capped rather than full-bleed: the body scrolls inside the
 * panel, so the header and the action never leave the screen no matter
 * how long the content is.
 *
 * See docs/22-components.md.
 */

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  footer,
  children,
  /** Wider for anything carrying a table. */
  size = "md",
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
  size?: "md" | "lg";
}) {
  const panel = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    restoreTo.current = document.activeElement as HTMLElement;
    panel.current?.focus();

    // The page behind must not scroll under the scrim.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !panel.current) return;

      // Trap focus: a modal that lets Tab escape behind the scrim is a
      // keyboard dead end.
      const focusable = panel.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      restoreTo.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-modal flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-[var(--scrim)]" onClick={onClose} aria-hidden />

      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={
          "relative flex max-h-[85vh] w-full flex-col overflow-hidden rounded-xl " +
          "border border-border bg-surface shadow-e3 outline-none " +
          (size === "lg" ? "max-w-[720px]" : "max-w-[520px]")
        }
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-section font-semibold text-text">{title}</h2>
            {subtitle && <p className="truncate text-body text-text-2">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-sm p-1 text-text-2 hover:bg-hover hover:text-text"
          >
            <X size={17} strokeWidth={1.8} aria-hidden />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && <footer className="border-t border-border px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

/** Two-column key/value list. Not cards — this is reference data. */
export function DetailList({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-body text-text-2">{label}</dt>
          <dd className="m-0 text-right text-body text-text">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
