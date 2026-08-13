/** Tailwind reads the design tokens; it never defines colour itself.
 *  See docs/04-design-system.md. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      app: "var(--app)",
      nav: "var(--nav)",
      topbar: "var(--topbar)",
      content: "var(--content)",
      surface: "var(--surface)",
      hover: "var(--hover)",
      selected: "var(--selected)",
      border: "var(--border)",
      hair: "var(--border-hair)",
      control: "var(--border-control)",
      text: "var(--text)",
      "text-2": "var(--text-2)",
      "text-3": "var(--text-3)",
      brand: "var(--brand)",
      "brand-weak": "var(--brand-weak)",
      "brand-text": "var(--brand-text)",
      "on-brand": "var(--on-brand)",
      info: "var(--info)",
      "info-bg": "var(--info-bg)",
      "info-text": "var(--info-text)",
      ok: "var(--ok)",
      warn: "var(--warn)",
      bad: "var(--bad)",
      "ok-bg": "var(--ok-bg)",
      "warn-bg": "var(--warn-bg)",
      "bad-bg": "var(--bad-bg)",
      // The word ramps. Omitting these once made every `text-ok-text`
      // silently resolve to nothing and inherit --text instead; the
      // palette validator now fails when a token here drifts.
      "ok-text": "var(--ok-text)",
      "warn-text": "var(--warn-text)",
      "bad-text": "var(--bad-text)",
    },
    borderRadius: {
      none: "0",
      sm: "var(--r-sm)",
      md: "var(--r-md)",
      lg: "var(--r-lg)",
      xl: "var(--r-xl)",
      full: "9999px",
    },
    fontFamily: {
      sans: "var(--font-sans)",
      mono: "var(--font-mono)",
    },
    fontSize: {
      // The fixed scale. Adding a size is a design-system change.
      group: ["10px", { lineHeight: "14px", letterSpacing: "0.07em" }],
      help: ["11px", { lineHeight: "16px", letterSpacing: "0.01em" }],
      label: ["12px", { lineHeight: "16px" }],
      body: ["13px", { lineHeight: "20px" }],
      section: ["14px", { lineHeight: "20px", letterSpacing: "-0.01em" }],
      metric: ["19px", { lineHeight: "26px", letterSpacing: "-0.02em" }],
      page: ["20px", { lineHeight: "28px", letterSpacing: "-0.02em" }],
      hero: ["28px", { lineHeight: "34px", letterSpacing: "-0.02em" }],
    },
    fontWeight: { normal: "400", medium: "500", semibold: "600" },
    extend: {
      spacing: {
        sidebar: "var(--sidebar-w)",
        "sidebar-collapsed": "var(--sidebar-collapsed-w)",
        topbar: "var(--topbar-h)",
      },
      maxWidth: { content: "var(--content-max)" },
      boxShadow: {
        none: "var(--elev-0)",
        e1: "var(--elev-1)",
        e2: "var(--elev-2)",
        e3: "var(--elev-3)",
      },
      zIndex: {
        sticky: "10", nav: "20", dropdown: "30",
        drawer: "40", modal: "50", toast: "60", palette: "70",
      },
    },
  },
  plugins: [],
};
