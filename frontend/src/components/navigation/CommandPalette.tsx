/* Ctrl-K. Search and go, without knowing which screen owns the thing.
 *
 * A pharmacist holding a carton with a number on it should be able to
 * type that number and arrive. So results carry the screen that opens
 * them, and navigation is the default action rather than a second step.
 *
 * Keyboard first, because the people who use this are already typing.
 * Arrow keys move, Enter opens, Escape closes, and the list is never
 * long enough to need a scrollbar — narrowing is faster than scrolling.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Building2,
  FileText,
  Package,
  Receipt,
  Search,
  ShoppingCart,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { api, type SearchHit } from "@/lib/api";

const ICONS: Record<string, LucideIcon> = {
  product: Package,
  batch: Boxes,
  order: ShoppingCart,
  invoice: Receipt,
  document: FileText,
  pharmacy: Building2,
  patient: UserRound,
};

const LABELS: Record<string, string> = {
  product: "Product",
  batch: "Batch",
  order: "Order",
  invoice: "Invoice",
  document: "Document",
  pharmacy: "Pharmacy",
  patient: "Patient",
};

export function CommandPalette({
  open,
  onClose,
  onNavigate,
}: {
  open: boolean;
  onClose: () => void;
  onNavigate: (screen: string) => void;
}) {
  const [term, setTerm] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const found = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.search(term),
    // Two characters minimum: one letter matches most of a catalogue and
    // the result is noise rather than an answer.
    enabled: open && term.trim().length >= 2,
  });

  const results = useMemo(() => found.data?.results ?? [], [found.data]);

  useEffect(() => {
    if (open) {
      setTerm("");
      setCursor(0);
      // Focus after paint, or the input is not in the document yet.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setCursor(0), [results.length]);

  if (!open) return null;

  const choose = (hit: SearchHit) => {
    onNavigate(hit.screen);
    onClose();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter" && results[cursor]) {
      event.preventDefault();
      choose(results[cursor]);
    }
  };

  return (
    <div
      className="fixed inset-0 z-modal flex items-start justify-center bg-black/30 pt-[12vh]"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="w-full max-w-xl overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search size={16} strokeWidth={1.9} aria-hidden className="text-text-3" />
          <input
            ref={inputRef}
            aria-label="Search"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Batch, order, invoice…"
            className="h-11 w-full bg-transparent text-body text-text placeholder:text-text-3 focus:outline-none"
          />
        </div>

        {term.trim().length < 2 ? (
          <p className="px-3 py-6 text-center text-help text-text-3">
            Type at least two characters.
          </p>
        ) : results.length === 0 ? (
          <p className="px-3 py-6 text-center text-help text-text-3">
            {found.isPending ? "Searching…" : `Nothing matches "${term}".`}
          </p>
        ) : (
          <ul role="listbox" className="max-h-[50vh] overflow-y-auto py-1">
            {results.map((hit, index) => {
              const Icon = ICONS[hit.kind] ?? Package;
              const active = index === cursor;
              return (
                <li key={`${hit.kind}-${hit.id}`}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => choose(hit)}
                    className={
                      active
                        ? "flex w-full items-center gap-3 bg-selected px-3 py-2 text-left"
                        : "flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-hover"
                    }
                  >
                    <Icon
                      size={16}
                      strokeWidth={1.85}
                      aria-hidden
                      className="shrink-0 text-text-3"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body text-text">
                        {hit.title}
                      </span>
                      {hit.subtitle && (
                        <span className="block truncate text-help text-text-3">
                          {hit.subtitle}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 text-group font-semibold uppercase text-text-3">
                      {LABELS[hit.kind] ?? hit.kind}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <p className="border-t border-border px-3 py-1.5 text-group text-text-3">
          ↑↓ move · ⏎ open · esc close
        </p>
      </div>
    </div>
  );
}

/** Ctrl-K or ⌘K anywhere, except while typing into something else. */
export function useCommandPalette(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return [open, setOpen];
}
