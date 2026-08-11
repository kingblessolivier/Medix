/* Drawer — inspection without losing context.
 *
 * SEARCH → TABLE → DRAWER → FULL TRANSACTION. A drawer never opens
 * another drawer; if that is needed, the flow belongs on a page.
 *
 * See docs/22-components.md.
 */

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  footer,
  children,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    restoreTo.current = document.activeElement as HTMLElement;
    panel.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !panel.current) return;

      // Trap focus: a drawer that lets Tab escape behind the scrim is a
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
      restoreTo.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-drawer bg-[var(--scrim)]"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="fixed right-0 top-0 z-drawer flex h-full w-full max-w-[480px] flex-col bg-surface shadow-e3 outline-none"
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
            <X size={17} strokeWidth={1.8} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && <footer className="border-t border-border px-5 py-3">{footer}</footer>}
      </div>
    </>
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
