import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Munin unified palette — ALL hex values live here.  Component code
        // must consume via Tailwind utilities (`bg-surface`, `text-accent`,
        // `border-border`, etc.) so a future theme rewire is one file wide.
        bg: "#0a0a0f",
        surface: "#111118",
        raised: "#161623",
        active: "#1c1c2e",
        border: "#1e1e2e",
        borderStrong: "#2a2a3e",

        // Brand — violet stays the single accent.  `soft` is a
        // pre-multiplied translucent surface so nested cards read as
        // "belongs to accent" without stacking opacity layers.
        accent: {
          DEFAULT: "#7c3aed",
          hover: "#9b70ff",
          soft: "rgba(124, 58, 237, 0.10)",
          ring: "rgba(124, 58, 237, 0.35)",
        },

        body: "#e2e8f0",
        secondary: "#a3a9b8",
        muted: "#6b7280",

        // Semantic
        success: "#10b981",
        warning: "#f59e0b",
        danger: "#f43f5e",
        info: "#38bdf8",

        // Explicit named tokens the design system references
        ice: "#38bdf8",
        rose: "#f43f5e",
        amber: "#f59e0b",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "Geist", "system-ui", "sans-serif"],
        mono: [
          "var(--font-geist-mono)",
          "Geist Mono",
          "JetBrains Mono",
          "ui-monospace",
          "monospace",
        ],
      },
      borderRadius: {
        DEFAULT: "6px",
        lg: "10px",
        xl: "14px",
      },
      keyframes: {
        "fade-slide": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "feather-pulse": {
          "0%, 100%": { opacity: "0.4" },
          "50%": { opacity: "1" },
        },
        blink: {
          "0%, 100%": { opacity: "0.2" },
          "50%": { opacity: "1" },
        },
        "spine-flow": {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100%)" },
        },
      },
      animation: {
        "fade-slide": "fade-slide 0.25s ease-out",
        feather: "feather-pulse 1.4s ease-in-out infinite",
        blink: "blink 1.2s ease-in-out infinite",
        "spine-flow": "spine-flow 2.2s ease-in-out infinite",
      },
      // Layered z-index scale.  Floating (draggable) windows sit below modals
      // so an AlertDialog HITL confirm always overlays them.  Toasts sit above
      // everything so notifications survive both.
      zIndex: {
        floating: "40",
        modal: "60",
        toast: "80",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
