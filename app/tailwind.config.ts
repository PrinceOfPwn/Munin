import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Munin palette
        bg: "#0a0a0f",
        surface: "#111118",
        border: "#1e1e2e",
        accent: "#7c3aed",       // violet — Munin
        ice: "#38bdf8",          // ice blue — LDAP/data
        rose: "#f43f5e",         // rose — active tool calls / alerts
        muted: "#6b7280",
        body: "#e2e8f0",
        success: "#10b981",     // emerald
        amber: "#f59e0b",
      },
      fontFamily: {
        sans: ["Inter", "Geist", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "JetBrains Mono", "ui-monospace", "monospace"],
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
        "blink": {
          "0%, 100%": { opacity: "0.2" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-slide": "fade-slide 0.25s ease-out",
        "feather": "feather-pulse 1.4s ease-in-out infinite",
        "blink": "blink 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
