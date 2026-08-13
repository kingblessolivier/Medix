/* Ask Medix.
 *
 * Deliberately not a chat. There is no conversation, no typing dots and
 * no prose reply — a question comes back as the same rows the matching
 * screen would show, with a button to go there. A pharmacist asking
 * "which batches expire in 60 days" wants the batches, not a sentence
 * about the batches.
 *
 * Two things it will not do, and they are visible rather than hidden:
 *
 *   It gives no clinical advice. A symptom question comes back refused,
 *   at any confidence, always — see backend/assistant/intents.py.
 *
 *   It never acts. Where an answer implies an action, that comes back as
 *   a proposal with its effect written out and an expiry on it, and
 *   nothing happens until somebody presses the button. The server
 *   enforces this by shape: the function that answers questions has no
 *   path to a function that writes.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CornerDownLeft } from "lucide-react";
import { useState } from "react";

import { ApiFailure, api, type Answer } from "@/lib/api";
import { DataTable, type Column } from "@/components/data/DataTable";
import {
  Banner,
  Button,
  EmptyState,
  Input,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import { Consequence } from "@/components/ui/Guidance";

/* Real questions, in the words somebody would use. Examples teach what
   the thing can do far better than a paragraph explaining it. */
const EXAMPLES = [
  "What expires in 60 days",
  "What is running low",
  "How much amoxicillin do we have",
  "Show unpaid invoices",
  "What is not selling",
  "Any fridge problems",
];

export function AssistantScreen({
  onNavigate,
}: {
  onNavigate: (screen: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [failure, setFailure] = useState("");

  const ask = useMutation({
    mutationFn: (asked: string) => api.ask(asked),
    onSuccess: (result) => {
      setAnswer(result);
      setFailure("");
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Couldn't answer that.",
      ),
  });

  function submit(asked: string) {
    const trimmed = asked.trim();
    if (!trimmed) return;
    setQuestion(trimmed);
    ask.mutate(trimmed);
  }

  return (
    <>
      <PageHeader title="Ask Medix" description="Your own records, in one question" />

      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit(question);
        }}
      >
        <Input
          aria-label="Ask a question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="What expires soon"
          className="flex-1"
        />
        <Button
          variant="primary"
          type="submit"
          loading={ask.isPending}
          icon={<CornerDownLeft size={16} strokeWidth={1.9} aria-hidden />}
        >
          Ask
        </Button>
      </form>

      <div className="mb-6 flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => submit(example)}
            className="rounded-full border border-border px-2.5 py-1 text-help text-text-2 transition-colors hover:bg-hover"
          >
            {example}
          </button>
        ))}
      </div>

      {failure && (
        <Banner tone="bad" className="mb-4">
          {failure}
        </Banner>
      )}

      {ask.isPending ? (
        <Skeleton className="h-[240px]" />
      ) : answer ? (
        <AnswerPanel answer={answer} onNavigate={onNavigate} />
      ) : (
        <EmptyState
          heading="Ask a question"
          body="Stock, expiry, orders, invoices and what sells."
        />
      )}

      <RecentProposals />
    </>
  );
}

function AnswerPanel({
  answer,
  onNavigate,
}: {
  answer: Answer;
  onNavigate: (screen: string) => void;
}) {
  /* A refusal is an answer, and it gets the same weight as one. Dressing
     it as an error would suggest the question was malformed; it was
     understood perfectly and declined. */
  const refused = answer.intent === "clinical";

  const columns: Column<Record<string, string>>[] = answer.columns.map((key) => ({
    key,
    header: key,
    render: (row) => row[key] ?? "",
  }));

  return (
    <section className="mb-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-section font-semibold text-text">{answer.headline}</h2>
        {answer.screen && !refused && (
          <Button variant="secondary" onClick={() => onNavigate(answer.screen)}>
            Open {answer.screen}
          </Button>
        )}
      </div>

      {refused && (
        <div className="mb-4 flex items-start gap-2 border-l-2 border-bad bg-bad-bg px-3 py-2.5">
          <CircleAlert
            size={16}
            strokeWidth={1.8}
            className="mt-0.5 shrink-0 text-bad"
            aria-hidden
          />
          <p className="text-body text-bad-text">{answer.note}</p>
        </div>
      )}

      {!refused && answer.note && (
        <Banner tone="info" className="mb-3">
          {answer.note}
        </Banner>
      )}

      {answer.proposal && <ProposalCard proposal={answer.proposal} />}

      {answer.rows.length > 0 && (
        <DataTable
          columns={columns}
          rows={answer.rows}
          rowKey={(row) => Object.values(row).join("|")}
          density="compact"
          caption={answer.headline}
          emptyHeading="Nothing matched"
        />
      )}
    </section>
  );
}

/* The whole point of the Assistant's design, made visible: it suggests,
   a person decides, and the effect is written out before the decision
   rather than reported after it. */
function ProposalCard({
  proposal,
}: {
  proposal: NonNullable<Answer["proposal"]>;
}) {
  const queryClient = useQueryClient();
  const [outcome, setOutcome] = useState<string | null>(null);
  const [failure, setFailure] = useState("");

  const decide = useMutation({
    mutationFn: (accepted: boolean) => api.decide(proposal.id, accepted),
    onSuccess: (result) => {
      setOutcome(result.status);
      queryClient.invalidateQueries({ queryKey: ["proposals"] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
    onError: (error) =>
      setFailure(
        error instanceof ApiFailure ? error.error.message : "Couldn't record it.",
      ),
  });

  if (outcome) {
    return (
      <Banner tone={outcome === "CONFIRMED" ? "ok" : "info"} className="mb-3">
        {outcome === "CONFIRMED"
          ? "Done. Open orders to see it."
          : outcome === "FAILED"
            ? "Could not be carried out."
            : "Declined. Nothing changed."}
      </Banner>
    );
  }

  return (
    <div className="mb-4 rounded-md border border-info bg-info-bg px-4 py-3">
      <p className="mb-2 text-body font-medium text-text">Medix can do this for you</p>
      <Consequence lines={[proposal.effect, "Nothing happens until you confirm."]} />
      {failure && (
        <Banner tone="bad" className="mt-3">
          {failure}
        </Banner>
      )}
      <div className="mt-3 flex gap-2">
        <Button
          variant="primary"
          loading={decide.isPending}
          onClick={() => decide.mutate(true)}
        >
          Do it
        </Button>
        <Button variant="secondary" onClick={() => decide.mutate(false)}>
          No thanks
        </Button>
      </div>
    </div>
  );
}

/* What was suggested and what was thought of it. A declined proposal is
   as interesting as a taken one — it is the record of what the system
   offered and what the pharmacist made of it. */
function RecentProposals() {
  const proposals = useQuery({
    queryKey: ["proposals"],
    queryFn: () => api.proposals(),
  });

  const rows = proposals.data?.results ?? [];
  if (rows.length === 0) return null;

  return (
    <section>
      <h2 className="mb-2 text-section font-semibold text-text">Earlier suggestions</h2>
      <DataTable
        columns={[
          { key: "question", header: "Asked", render: (p) => p.question },
          { key: "effect", header: "Suggested", render: (p) => p.effect },
          {
            key: "status",
            header: "Decision",
            render: (p) => p.status.toLowerCase(),
          },
        ]}
        rows={rows}
        rowKey={(p) => p.id}
        density="compact"
        caption="Assistant suggestions"
        emptyHeading="No suggestions"
      />
    </section>
  );
}
