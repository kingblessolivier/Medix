/* UI primitives.
 *
 * Every screen composes from these. A screen that styles a bare <input>
 * or introduces a colour is a review failure.
 * See docs/22-components.md.
 */

import clsx from "clsx";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useId } from "react";

/* -- Button ------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "tertiary" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white border border-transparent hover:opacity-90",
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
        {required && <span className="ml-0.5 text-bad">*</span>}
      </label>
      {children(id)}
      {error ? (
        <p className="text-help text-bad">{error}</p>
      ) : help ? (
        <p className="text-help text-text-3">{help}</p>
      ) : null}
    </div>
  );
}

const CONTROL =
  "h-9 w-full rounded-md border bg-surface px-3 text-body text-text " +
  "placeholder:text-text-3 transition-colors hover:border-text-3 " +
  "focus:border-brand disabled:cursor-not-allowed disabled:bg-content disabled:text-text-3";

export function Input({
  invalid,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      className={clsx(CONTROL, invalid ? "border-bad" : "border-border", className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
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
      className={clsx(CONTROL, "cursor-pointer", invalid ? "border-bad" : "border-border", className)}
      aria-invalid={invalid || undefined}
      {...rest}
    >
      {children}
    </select>
  );
}

/* -- Status ------------------------------------------------------------ */

/* Status is never colour alone. The dot always carries its label. */

export type Tone = "ok" | "warn" | "bad" | "neutral" | "brand";

const DOT: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  neutral: "bg-text-3",
  brand: "bg-brand",
};

const TEXT: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  neutral: "text-text-2",
  brand: "text-brand",
};

export function StatusDot({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-body", TEXT[tone])}>
      <span aria-hidden className={clsx("h-2 w-2 shrink-0 rounded-full", DOT[tone])} />
      {children}
    </span>
  );
}

const BADGE: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  neutral: "bg-hover text-text-2",
  brand: "bg-brand-weak text-brand-text",
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
