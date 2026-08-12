/* UI primitives.
 *
 * Every screen composes from these. A screen that styles a bare <input>
 * or introduces a colour is a review failure.
 * See docs/22-components.md.
 */

import clsx from "clsx";
import {
  Check,
  CircleAlert,
  CircleCheck,
  Info,
  Minus,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useEffect, useId, useRef } from "react";

/* -- Button ------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "tertiary" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand text-on-brand border border-transparent hover:opacity-90",
  secondary: "bg-surface text-text border border-border hover:bg-hover",
  tertiary: "bg-transparent text-text-2 border border-transparent hover:bg-hover",
  danger: "bg-bad text-white border border-transparent hover:opacity-90",
};

export function Button({
  variant = "secondary",
  loading = false,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      className={clsx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md px-4",
        "text-body font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {/* Width is held while loading so the layout does not jump. */}
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}

/* -- Field ------------------------------------------------------------- */

/* A placeholder is an example, never an instruction, and never a label.
 * Every field carries a persistent visible label. */

export function Field({
  label,
  help,
  error,
  required,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  required?: boolean;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-label font-medium text-text-2">
        {label}
        {required && <span className="ml-0.5 text-bad-text">*</span>}
      </label>
      {children(id)}
      {error ? (
        <p className="text-help text-bad-text">{error}</p>
      ) : help ? (
        <p className="text-help text-text-3">{help}</p>
      ) : null}
    </div>
  );
}

/* The control edge uses --border-control, not the divider colour: it is
 * the only thing telling you where the field is, so it owes 3:1. */
const CONTROL =
  "h-9 w-full rounded-md border bg-surface px-3 text-body text-text " +
  "placeholder:text-text-3 transition-colors hover:border-text-2 " +
  "focus:border-brand disabled:cursor-not-allowed disabled:bg-content disabled:text-text-3";

export function Input({
  invalid,
  className,
  icon: Icon,
  trailing,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean;
  /** Leading affordance — what kind of value this is. */
  icon?: LucideIcon;
  /** Trailing control, e.g. a reveal toggle. Must be focusable. */
  trailing?: ReactNode;
}) {
  const field = (
    <input
      className={clsx(
        CONTROL,
        invalid ? "border-bad" : "border-control",
        Icon && "pl-9",
        trailing && "pr-10",
        className,
      )}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );

  if (!Icon && !trailing) return field;

  return (
    <div className="relative">
      {Icon && (
        <Icon
          size={16}
          strokeWidth={1.8}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-3"
        />
      )}
      {field}
      {trailing && (
        <span className="absolute right-1.5 top-1/2 -translate-y-1/2">{trailing}</span>
      )}
    </div>
  );
}

export function Select({
  invalid,
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { invalid?: boolean }) {
  return (
    <select
      className={clsx(
        CONTROL,
        "cursor-pointer",
        invalid ? "border-bad" : "border-control",
        className,
      )}
      aria-invalid={invalid || undefined}
      {...rest}
    >
      {children}
    </select>
  );
}

/* -- Checkbox ---------------------------------------------------------- */

/* Row selection needs a real <input type=checkbox> for keyboard and
 * screen readers; `indeterminate` is a DOM property, not an attribute,
 * so it has to be set through a ref. */

export function Checkbox({
  checked = false,
  indeterminate = false,
  onChange,
  label,
  className,
}: {
  checked?: boolean;
  indeterminate?: boolean;
  onChange: (checked: boolean) => void;
  /** Always required — invisible, but the row must be nameable. */
  label: string;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [indeterminate, checked]);

  const marked = checked || indeterminate;

  return (
    <span className={clsx("relative inline-flex h-4 w-4 shrink-0", className)}>
      <input
        ref={ref}
        type="checkbox"
        aria-label={label}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        onClick={(e) => e.stopPropagation()}
        className={clsx(
          "peer h-4 w-4 cursor-pointer appearance-none rounded-sm border transition-colors",
          marked ? "border-brand bg-brand" : "border-control bg-surface hover:border-text-2",
        )}
      />
      {/* Drawn in --on-brand, not white: the dark theme's brand is a pale
          green where a white tick would disappear. */}
      {marked && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-on-brand">
          {checked ? (
            <Check size={12} strokeWidth={3} aria-hidden />
          ) : (
            <Minus size={12} strokeWidth={3} aria-hidden />
          )}
        </span>
      )}
    </span>
  );
}

/* -- Status ------------------------------------------------------------ */

/* Status is never colour alone. The dot always carries its label. */

export type Tone = "ok" | "warn" | "bad" | "neutral" | "brand" | "info";

const DOT: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  neutral: "bg-text-3",
  brand: "bg-brand",
  info: "bg-info",
};

/* The dot is a mark and may sit at 3:1; the word beside it is text and
 * owes 4.5:1. Same status, two ramps — see tokens.css. */
const TEXT: Record<Tone, string> = {
  ok: "text-ok-text",
  warn: "text-warn-text",
  bad: "text-bad-text",
  neutral: "text-text-2",
  brand: "text-brand-text",
  info: "text-info-text",
};

export function StatusDot({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-body", TEXT[tone])}>
      <span aria-hidden className={clsx("h-2 w-2 shrink-0 rounded-full", DOT[tone])} />
      {children}
    </span>
  );
}

/* The table form of a status: tinted pill, leading dot, small caps.
 * Reads as one object at a glance down a column, where a bare dot and
 * sentence-case label blur together. StatusDot stays for drawers and
 * prose, where a pill would shout. */
export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full py-0.5 pl-1.5 pr-2",
        "text-group font-semibold uppercase",
        BADGE[tone],
      )}
    >
      <span aria-hidden className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[tone])} />
      {children}
    </span>
  );
}

const BADGE: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok-text",
  warn: "bg-warn-bg text-warn-text",
  bad: "bg-bad-bg text-bad-text",
  neutral: "bg-hover text-text-2",
  brand: "bg-brand-weak text-brand-text",
  info: "bg-info-bg text-info-text",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-help font-semibold",
        BADGE[tone],
      )}
    >
      {children}
    </span>
  );
}

/* -- Banner ------------------------------------------------------------ */

/* An inline result, tied to the thing it is about. Never a floating toast
 * for anything the user must act on.
 *
 * Colour is never the only signal — each tone carries its own icon, so
 * the message survives a monochrome screen and colour-vision deficiency.
 * One line of copy, 12 words. See docs/23-ui-copy.md. */

const BANNER: Record<Tone, { box: string; mark: string; icon: LucideIcon }> = {
  ok: { box: "border-ok bg-ok-bg", mark: "text-ok", icon: CircleCheck },
  warn: { box: "border-warn bg-warn-bg", mark: "text-warn", icon: TriangleAlert },
  bad: { box: "border-bad bg-bad-bg", mark: "text-bad", icon: CircleAlert },
  brand: { box: "border-brand bg-brand-weak", mark: "text-brand-text", icon: Info },
  info: { box: "border-info bg-info-bg", mark: "text-info-text", icon: Info },
  neutral: { box: "border-border bg-content", mark: "text-text-3", icon: Info },
};

/* Outer spacing belongs to whoever places it, not to the banner. */
export function Banner({
  tone = "neutral",
  children,
  action,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const { box, mark, icon: Icon } = BANNER[tone];
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={clsx(
        "flex items-center gap-2.5 rounded-md border px-3 py-2",
        box,
        className,
      )}
    >
      <Icon size={16} strokeWidth={1.9} className={clsx("shrink-0", mark)} aria-hidden />
      <p className="min-w-0 flex-1 text-body text-text">{children}</p>
      {action}
    </div>
  );
}

/* -- Surfaces ---------------------------------------------------------- */

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={clsx("rounded-lg border border-border bg-surface", className)}>{children}</div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-page font-semibold text-text">{title}</h1>
        {description && <p className="mt-0.5 text-body text-text-2">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* -- States ------------------------------------------------------------ */

/* The interface states and acts. It does not explain.
 * Heading 5 words, body 10, one action. See docs/23-ui-copy.md. */

export function EmptyState({
  icon,
  heading,
  body,
  action,
}: {
  icon?: ReactNode;
  heading: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      {icon && <div className="text-text-3">{icon}</div>}
      <p className="text-section font-semibold text-text">{heading}</p>
      {body && <p className="max-w-[40ch] text-body text-text-2">{body}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <CircleAlert size={20} strokeWidth={1.75} className="text-bad" aria-hidden />
      <p className="text-body text-text">{message}</p>
      {onRetry && (
        <Button onClick={onRetry} variant="secondary">
          Retry
        </Button>
      )}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-sm bg-content", className)} />;
}
