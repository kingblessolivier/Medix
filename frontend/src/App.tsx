/* Application root: auth gate, capability-driven shell, module routing. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell, navigationFor } from "@/components/navigation/AppShell";
import { EmptyState } from "@/components/ui";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { api, logout, tokens } from "@/lib/api";
import { LoginScreen } from "@/modules/auth/LoginScreen";
import { InventoryScreen } from "@/modules/inventory/InventoryScreen";
import { MarketplaceScreen } from "@/modules/marketplace/MarketplaceScreen";
import { OrdersScreen } from "@/modules/orders/OrdersScreen";
import { PosScreen } from "@/modules/pos/PosScreen";
import { ReceivingScreen } from "@/modules/receiving/ReceivingScreen";

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

  // Navigation follows held licences: a wholesale pharmacy has no point
  // of sale, a retail pharmacy no fulfilment queue. See ADR-006.
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api.capabilities(),
    enabled: signedIn,
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

  const granted = capabilities.data?.capabilities ?? [];
  const canSell = granted.includes("SELL_RETAIL");
  const canSupply = granted.includes("SELL_WHOLESALE");

  return (
    <AppShell
      groups={navigationFor(granted)}
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
        ) : active === "pos" && canSell ? (
          <PosScreen locationId={mainLocation ?? null} />
        ) : active === "marketplace" ? (
          <MarketplaceScreen locationId={mainLocation ?? null} />
        ) : active === "orders" ? (
          <OrdersScreen canSupply={canSupply} />
        ) : active === "receiving" ? (
          <ReceivingScreen locationId={mainLocation ?? null} />
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
