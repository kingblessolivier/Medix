/* Application root: auth gate, shell, module routing. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/navigation/AppShell";
import { EmptyState } from "@/components/ui";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { api, logout, tokens } from "@/lib/api";
import { LoginScreen } from "@/modules/auth/LoginScreen";
import { InventoryScreen } from "@/modules/inventory/InventoryScreen";
import { PosScreen } from "@/modules/pos/PosScreen";

export default function App() {
  const queryClient = useQueryClient();
  const [signedIn, setSignedIn] = useState(() => Boolean(tokens.access));
  const [active, setActive] = useState("inventory");

  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
    enabled: signedIn,
    retry: false,
  });

  const locations = useQuery({
    queryKey: ["locations"],
    queryFn: () => api.locations(),
    enabled: signedIn,
  });
  const mainLocation = locations.data?.results.find((l) => l.code === "MAIN")?.id;

  if (!signedIn) {
    return (
      <LoginScreen
        onSignedIn={() => {
          setSignedIn(true);
          queryClient.invalidateQueries();
        }}
      />
    );
  }

  // A dead token that cannot refresh returns the user to sign-in rather
  // than leaving them on an empty shell.
  if (me.isError) {
    logout();
    return <LoginScreen onSignedIn={() => setSignedIn(true)} />;
  }

  return (
    <AppShell
      active={active}
      onNavigate={setActive}
      organizationName={me.data?.organization?.name}
      userName={me.data?.name}
      onSignOut={() => {
        logout();
        setSignedIn(false);
        queryClient.clear();
      }}
    >
      {/* Keyed so a failing screen resets when the user navigates away,
          rather than staying broken until reload. */}
      <ErrorBoundary key={active}>
        {active === "inventory" ? (
          <InventoryScreen />
        ) : active === "pos" ? (
          <PosScreen locationId={mainLocation ?? null} />
        ) : (
          <EmptyState
            heading="Not built yet"
            body="This module arrives in a later phase."
          />
        )}
      </ErrorBoundary>
    </AppShell>
  );
}
