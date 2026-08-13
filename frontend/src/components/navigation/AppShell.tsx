/* Application shell — fixed for every module.
 *
 * Only the content area changes, and it always follows:
 * page header → primary workspace → secondary information.
 *
 * See docs/04-design-system.md, docs/19-screens.md.
 */

import clsx from "clsx";
import {
  ArrowLeftRight,
  BookOpen,
  Building2,
  ChartNoAxesCombined,
  ClipboardList,
  HeartPulse,
  LayoutDashboard,
  Lightbulb,
  Moon,
  Package,
  PackageCheck,
  PackagePlus,
  Receipt,
  Search,
  Settings,
  ShoppingCart,
  Store,
  ShieldCheck,
  Sun,
  TriangleAlert,
  Truck,
  Undo2,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { NotificationBell } from "./NotificationBell";

export type NavItem = { id: string; label: string; icon: LucideIcon };
export type NavGroup = { label: string; items: NavItem[] };

/* Navigation is derived from held licences, not a role name. A wholesale
   pharmacy has no point of sale; a retail pharmacy no fulfilment queue.
   An organization holding both licences sees both, with no special case.
   See ADR-006. */
export function navigationFor(capabilities: string[]): NavGroup[] {
  const can = (c: string) => capabilities.includes(c);

  const operations: NavItem[] = [
    { id: "inventory", label: "Inventory", icon: Package },
    { id: "catalogue", label: "Catalogue", icon: BookOpen },
  ];
  if (can("SELL_RETAIL")) {
    operations.push({ id: "pos", label: "Point of sale", icon: Receipt });
  }
  operations.push({ id: "transfers", label: "Transfers", icon: ArrowLeftRight });
  operations.push({ id: "returns", label: "Returns", icon: Undo2 });
  // A depot's fulfilment queue. Gated on publishing rather than on
  // DISTRIBUTE: if you can offer stock you have orders to pick.
  if (can("PUBLISH_LISTINGS")) {
    operations.push({ id: "distribution", label: "Orders to fill", icon: Truck });
  }

  const commerce: NavItem[] = [
    { id: "marketplace", label: "Marketplace", icon: Store },
    { id: "orders", label: "Orders", icon: ShoppingCart },
    { id: "receiving", label: "Deliveries", icon: PackageCheck },
  ];
  // Only a depot imports. A retail pharmacy receives against an order it
  // placed; it does not clear a consignment through customs.
  if (can("PUBLISH_LISTINGS")) {
    commerce.push({ id: "import", label: "Imports", icon: PackagePlus });
    // Admission to the network is an act one organization performs on
    // another, so it sits with the depot's other commercial work.
    commerce.push({ id: "pharmacies", label: "Customers", icon: Building2 });
  }

  const groups: NavGroup[] = [
    { label: "Main", items: [{ id: "overview", label: "Overview", icon: LayoutDashboard }] },
    { label: "Operations", items: operations },
    { label: "Commerce", items: commerce },
  ];

  if (can("DISPENSE")) {
    groups.push({
      label: "Patients",
      items: [
        { id: "prescriptions", label: "Prescriptions", icon: ClipboardList },
        { id: "claims", label: "Claims", icon: HeartPulse },
      ],
    });
  }

  // Recall and compliance are one job done by one person, and both
  // answer "what is about to go wrong" rather than "what happened".
  groups.push({
    label: "Compliance",
    items: [
      { id: "compliance", label: "Licences", icon: ShieldCheck },
      { id: "recall", label: "Recall", icon: TriangleAlert },
    ],
  });

  groups.push({
    label: "Configuration",
    items: [{ id: "settings", label: "Settings", icon: Settings }],
  });

  groups.push({
    label: "Reporting",
    items: [
      { id: "analytics", label: "Performance", icon: ChartNoAxesCombined },
      { id: "intelligence", label: "Insights", icon: Lightbulb },
      { id: "finance", label: "Finance", icon: Wallet },
    ],
  });
  return groups;
}

/* Fallback used before capabilities load. */
export const PHARMACY_NAV: NavGroup[] = [
  { label: "Main", items: [{ id: "overview", label: "Overview", icon: LayoutDashboard }] },
  {
    label: "Operations",
    items: [
      { id: "inventory", label: "Inventory", icon: Package },
      { id: "pos", label: "Point of sale", icon: Receipt },
      { id: "transfers", label: "Transfers", icon: ArrowLeftRight },
    ],
  },
  {
    label: "Commerce",
    items: [
      { id: "marketplace", label: "Marketplace", icon: Store },
      { id: "orders", label: "Orders", icon: ShoppingCart },
    ],
  },
  { label: "Reporting", items: [{ id: "analytics", label: "Analytics", icon: ChartNoAxesCombined }] },
];

export function AppShell({
  groups = PHARMACY_NAV,
  active,
  onNavigate,
  organizationName,
  userName,
  onSignOut,
  onSearch,
  children,
}: {
  groups?: NavGroup[];
  active: string;
  onNavigate: (id: string) => void;
  organizationName?: string;
  userName?: string;
  onSignOut?: () => void;
  /** Opens the command palette. The top bar's field is a button for it,
      not an input — searching happens in the palette. */
  onSearch?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-app">
      <Sidebar
        groups={groups}
        active={active}
        onNavigate={onNavigate}
        organizationName={organizationName}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          userName={userName}
          onSignOut={onSignOut}
          onSearch={onSearch}
          onNavigate={onNavigate}
        />
        <main className="mx-auto w-full max-w-content flex-1 p-6">{children}</main>
        <StatusBar />
      </div>
    </div>
  );
}

/* -- Sidebar ----------------------------------------------------------- */

function Sidebar({
  groups,
  active,
  onNavigate,
  organizationName,
}: {
  groups: NavGroup[];
  active: string;
  onNavigate: (id: string) => void;
  organizationName?: string;
}) {
  return (
    /* Tinted, not white — the sidebar is part of the application shell,
     * not another floating card. */
    <nav
      aria-label="Main"
      className="z-nav hidden w-sidebar shrink-0 border-r border-border bg-nav py-4 md:block"
    >
      <div className="px-4 pb-4">
        <div className="flex items-center gap-2">
          <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-brand" />
          <span className="text-section font-semibold tracking-tight">Medix</span>
        </div>
        {organizationName && (
          <p className="mt-0.5 truncate text-help text-text-3">{organizationName}</p>
        )}
      </div>

      {groups.map((group) => (
        <div key={group.label}>
          <p className="px-4 pb-1.5 pt-3 text-group font-semibold uppercase text-text-3">
            {group.label}
          </p>
          {group.items.map((item) => {
            const Icon = item.icon;
            const isActive = item.id === active;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onNavigate(item.id)}
                aria-current={isActive ? "page" : undefined}
                className={clsx(
                  "flex h-9 w-full items-center gap-2 border-l-2 px-3 text-body transition-colors",
                  isActive
                    ? "border-brand bg-selected font-medium text-brand-text"
                    : "border-transparent text-text-2 hover:bg-hover hover:text-text",
                )}
              >
                <Icon size={17} strokeWidth={1.8} className={isActive ? "text-brand" : ""} />
                {item.label}
              </button>
            );
          })}
        </div>
      ))}

    </nav>
  );
}

/* -- Top bar ----------------------------------------------------------- */

function TopBar({
  userName,
  onSignOut,
  onSearch,
  onNavigate,
}: {
  userName?: string;
  onSignOut?: () => void;
  onSearch?: () => void;
  onNavigate?: (id: string) => void;
}) {
  return (
    /* Disappears into the environment — same neutral family as the
     * workspace, separated by a 1px divider. */
    <header className="sticky top-0 z-nav flex h-topbar items-center gap-3 border-b border-border bg-topbar px-6">
      <span className="text-section font-semibold tracking-tight md:hidden">Medix</span>

      {/* Quiet until focused. A search field that shouts competes with
       * the content. */}
      <button
        type="button"
        onClick={onSearch}
        className="flex h-8 w-full max-w-[420px] items-center gap-2 rounded-md bg-hover px-3 text-left text-body text-text-3 transition-colors hover:ring-1 hover:ring-border"
      >
        <Search size={15} strokeWidth={1.8} aria-hidden />
        <span>Search products, orders, batches…</span>
        <kbd className="ml-auto font-sans text-help">⌘K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-3">
        <ThemeToggle />
        <NotificationBell onNavigate={onNavigate} />
        <button
          type="button"
          onClick={onSignOut}
          title={userName ? `${userName} — sign out` : "Sign out"}
          aria-label="Sign out"
          className="grid h-7 w-7 place-items-center rounded-full bg-brand-weak text-help font-semibold text-brand-text hover:opacity-80"
        >
          {initials(userName)}
        </button>
      </div>
    </header>
  );
}

function initials(name?: string): string {
  if (!name) return "—";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function ThemeToggle() {
  const [dark, setDark] = useState(
    () =>
      document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme &&
        window.matchMedia("(prefers-color-scheme: dark)").matches),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);

  return (
    <button
      type="button"
      onClick={() => setDark((d) => !d)}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="text-text-2 hover:text-text"
    >
      {dark ? <Sun size={17} strokeWidth={1.8} aria-hidden /> : <Moon size={17} strokeWidth={1.8} aria-hidden />}
    </button>
  );
}

/* -- Status bar -------------------------------------------------------- */

function StatusBar() {
  return (
    <footer className="flex items-center gap-4 border-t border-border px-6 py-2 text-help text-text-3">
      <span className="flex items-center gap-1.5">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-ok" />
        All systems operational
      </span>
      <span className="ml-auto">v0.1.0</span>
    </footer>
  );
}
