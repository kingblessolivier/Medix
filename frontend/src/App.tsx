/* Application root: auth gate, capability-driven shell, module routing. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell, navigationFor } from "@/components/navigation/AppShell";
import {
  CommandPalette,
  useCommandPalette,
} from "@/components/navigation/CommandPalette";
import { EmptyState } from "@/components/ui";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { api, logout, tokens } from "@/lib/api";
import { IntelligenceScreen } from "@/modules/analytics/IntelligenceScreen";
import { PerformanceScreen } from "@/modules/analytics/PerformanceScreen";
import { ProductEditor } from "@/modules/catalogue/ProductEditor";
import { ComplianceScreen } from "@/modules/compliance/ComplianceScreen";
import { DistributionScreen } from "@/modules/distribution/DistributionScreen";
import { LoginScreen } from "@/modules/auth/LoginScreen";
import { DocumentsScreen } from "@/modules/documents/DocumentsScreen";
import { FinanceScreen } from "@/modules/finance/FinanceScreen";
import { InventoryScreen } from "@/modules/inventory/InventoryScreen";
import { OverviewScreen } from "@/modules/overview/OverviewScreen";
import { PharmaciesScreen } from "@/modules/pharmacies/PharmaciesScreen";
import { RecallScreen } from "@/modules/recall/RecallScreen";
import { ReturnsScreen } from "@/modules/returns/ReturnsScreen";
import { PrescriptionsScreen } from "@/modules/prescriptions/PrescriptionsScreen";
import { SettingsScreen } from "@/modules/settings/SettingsScreen";
import { TransfersScreen } from "@/modules/transfers/TransfersScreen";
import { MarketplaceScreen } from "@/modules/marketplace/MarketplaceScreen";
import { OrdersScreen } from "@/modules/orders/OrdersScreen";
import { PosScreen } from "@/modules/pos/PosScreen";
import { ImportReceiptScreen } from "@/modules/receiving/ImportReceiptScreen";
import { ReceivingScreen } from "@/modules/receiving/ReceivingScreen";

export default function App() {
  const queryClient = useQueryClient();
  const [signedIn, setSignedIn] = useState(() => Boolean(tokens.access));
  const [active, setActive] = useState("overview");
  const [paletteOpen, setPaletteOpen] = useCommandPalette();

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
  /* The working location. "MAIN" is the retail convention; a wholesale
     pharmacy's is "DEPOT", and nothing guarantees either exists — so fall
     back to whatever this organization actually has. Matching on the code
     alone left every wholesale action sending no location at all. */
  const sites = locations.data?.results ?? [];
  const mainLocation = (sites.find((l) => l.code === "MAIN") ?? sites[0])?.id;

  function signIn() {
    setSignedIn(true);
    // clear(), not invalidate(): a failed `me` from the previous session
    // stays in error state through an invalidation, and the guard below
    // then signs the user straight back out on a successful login. Only
    // clearing drops the error with the rest of the old session.
    queryClient.clear();
  }

  if (!signedIn) {
    return <LoginScreen onSignedIn={signIn} />;
  }

  // A dead token that cannot refresh returns the user to sign-in rather
  // than leaving them on an empty shell. Waiting for the fetch to settle
  // matters: acting while it is still in flight logs out someone who has
  // just successfully authenticated.
  if (me.isError && !me.isFetching) {
    logout();
    return <LoginScreen onSignedIn={signIn} />;
  }

  const granted = capabilities.data?.capabilities ?? [];
  const canSell = granted.includes("SELL_RETAIL");
  const canSupply = granted.includes("SELL_WHOLESALE");

  return (
    <>
    <CommandPalette
      open={paletteOpen}
      onClose={() => setPaletteOpen(false)}
      onNavigate={setActive}
    />
    <AppShell
      groups={navigationFor(granted)}
      onSearch={() => setPaletteOpen(true)}
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
        {active === "overview" ? (
          <OverviewScreen
            canSupply={canSupply}
            canSell={canSell}
            onNavigate={setActive}
          />
        ) : active === "inventory" ? (
          <InventoryScreen />
        ) : active === "pos" && canSell ? (
          <PosScreen locationId={mainLocation ?? null} />
        ) : active === "marketplace" ? (
          <MarketplaceScreen locationId={mainLocation ?? null} />
        ) : active === "orders" ? (
          <OrdersScreen
            canSupply={canSupply}
            locationId={mainLocation ?? null}
            organizationId={me.data?.organization?.id}
          />
        ) : active === "receiving" ? (
          <ReceivingScreen locationId={mainLocation ?? null} />
        ) : active === "import" ? (
          <ImportReceiptScreen locationId={mainLocation ?? null} />
        ) : active === "analytics" ? (
          <PerformanceScreen canSupply={canSupply} />
        ) : active === "finance" ? (
          <FinanceScreen />
        ) : active === "documents" ? (
          <DocumentsScreen />
        ) : active === "distribution" && canSupply ? (
          <DistributionScreen
            locationId={mainLocation ?? null}
            organizationId={me.data?.organization?.id}
          />
        ) : active === "transfers" ? (
          <TransfersScreen />
        ) : active === "prescriptions" && canSell ? (
          <PrescriptionsScreen />
        ) : active === "pharmacies" && canSupply ? (
          <PharmaciesScreen />
        ) : active === "settings" ? (
          <SettingsScreen />
        ) : active === "recall" ? (
          <RecallScreen />
        ) : active === "returns" ? (
          <ReturnsScreen />
        ) : active === "compliance" ? (
          <ComplianceScreen />
        ) : active === "intelligence" ? (
          <IntelligenceScreen />
        ) : active === "catalogue" ? (
          <ProductEditor />
        ) : (
          <EmptyState
            heading="Not built yet"
            body="This module arrives in a later phase."
          />
        )}
      </ErrorBoundary>
    </AppShell>
    </>
  );
}
