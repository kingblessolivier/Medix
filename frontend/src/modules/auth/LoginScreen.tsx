/* Sign in.
 *
 * A single card on a quiet ground — the only screen in Medix that is not
 * a dense workspace, because it is the only one where the user has
 * nothing to scan and one thing to do.
 *
 * Username and password only. There is no third-party sign-in: accounts
 * are provisioned by the pharmacy against a person who holds a council
 * registration, and the backend issues JWTs from Django auth. Social
 * buttons here would be decoration wired to nothing.
 *
 * The ambient wash stays a tint, never a fill. `docs/04-design-system.md`
 * is explicit that green is an accent and there is no giant brand
 * background anywhere in Medix; this is the one place it is allowed to
 * show at all, and only as a soft glow behind the card.
 *
 * Copy stays within docs/23-ui-copy.md — the error is "Username or
 * password is incorrect", not a paragraph.
 */

import { Eye, EyeOff, LogIn, Lock, User } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiFailure, login } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui";

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onSignedIn();
    } catch (err) {
      setError(
        err instanceof ApiFailure ? err.error.message : "Sign in failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-app">
      {/* Two soft brand glows, low opacity, sitting behind everything.
          Pointer-events off so they can never eat a click. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(60rem 32rem at 50% -8rem, var(--brand-weak), transparent 70%)," +
            "radial-gradient(40rem 24rem at 12% 108%, var(--brand-weak), transparent 65%)",
        }}
      />

      <header className="relative flex items-center gap-2 px-6 py-5">
        <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-brand" />
        <span className="text-section font-semibold tracking-tight">Medix</span>
      </header>

      <main className="relative flex flex-1 items-center justify-center px-6 pb-16">
        <form
          onSubmit={submit}
          /* e3 is the modal level. This card is the only thing on screen
             and should read as lifted off the ground, not laid on it. */
          className="w-full max-w-[380px] rounded-xl border border-border bg-surface p-7 shadow-e3"
        >
          <div className="mb-6 flex flex-col items-center text-center">
            <span className="mb-4 grid h-11 w-11 place-items-center rounded-lg bg-brand text-on-brand">
              <LogIn size={18} strokeWidth={1.9} aria-hidden />
            </span>
            <h1 className="text-page font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-body text-text-2">Pharmacy operations and compliance.</p>
          </div>

          <div className="flex flex-col gap-4">
            <Field label="Username">
              {(id) => (
                <Input
                  id={id}
                  icon={User}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="marie"
                  autoComplete="username"
                  autoFocus
                  required
                />
              )}
            </Field>

            <Field label="Password">
              {(id) => (
                <Input
                  id={id}
                  icon={Lock}
                  type={reveal ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                  trailing={
                    <button
                      type="button"
                      // Toggling type resets nothing, so the field keeps
                      // its value and the caret stays put.
                      onClick={() => setReveal((v) => !v)}
                      aria-label={reveal ? "Hide password" : "Show password"}
                      aria-pressed={reveal}
                      className="grid h-7 w-7 place-items-center rounded-sm text-text-3 transition-colors hover:bg-hover hover:text-text"
                    >
                      {reveal ? (
                        <EyeOff size={16} strokeWidth={1.8} aria-hidden />
                      ) : (
                        <Eye size={16} strokeWidth={1.8} aria-hidden />
                      )}
                    </button>
                  }
                />
              )}
            </Field>

            {error && (
              <p role="alert" className="text-help text-bad-text">
                {error}
              </p>
            )}

            <Button type="submit" variant="primary" loading={busy} className="w-full">
              Sign in
            </Button>
          </div>
        </form>
      </main>
    </div>
  );
}
