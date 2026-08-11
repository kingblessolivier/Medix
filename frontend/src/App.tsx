/* Application root: auth gate, shell, module routing. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/navigation/AppShell";
import { EmptyState } from "@/components/ui";
import { api, logout, tokens } from "@/lib/api";
import { LoginScreen } from "@/modules/auth/LoginScreen";
import { InventoryScreen } from "@/modules/inventory/InventoryScreen";

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
      {active === "inventory" ? (
        <InventoryScreen />
      ) : (
        <EmptyState
          heading="Not built yet"
          body="This module arrives in a later phase."
        />
      )}
    </AppShell>
  );
}
