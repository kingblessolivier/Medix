/* Error boundary.
 *
 * A render error must not blank the screen. At a counter a white page is
 * indistinguishable from a dead machine, and the pharmacist has a queue.
 * The shell stays up; only the failing region is replaced.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui";

type Props = { children: ReactNode; label?: string };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Left as console until an error reporter is wired in Phase 8.
    console.error("Render failed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <p className="text-section font-semibold text-text">
          {this.props.label ?? "This screen failed"}
        </p>
        <p className="max-w-[40ch] text-body text-text-2">Your work is saved.</p>
        <Button onClick={() => this.setState({ error: null })}>Try again</Button>
      </div>
    );
  }
}
