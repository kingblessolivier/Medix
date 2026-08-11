/* Sign in.
 *
 * Copy stays within the limits in docs/23-ui-copy.md — the error is
 * "Username or password is incorrect", not a paragraph.
 */

import { useState, type FormEvent } from "react";

import { ApiFailure, login } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui";

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
    <div className="flex min-h-screen items-center justify-center bg-app p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-[360px] rounded-lg border border-border bg-surface p-6"
      >
        <div className="mb-5 flex items-center gap-2">
          <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-brand" />
          <span className="text-section font-semibold tracking-tight">Medix</span>
        </div>

        <div className="flex flex-col gap-4">
          <Field label="Username">
            {(id) => (
              <Input
                id={id}
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
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            )}
          </Field>

          {error && (
            <p role="alert" className="text-help text-bad">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" loading={busy} className="w-full">
            Sign in
          </Button>
        </div>
      </form>
    </div>
  );
}
