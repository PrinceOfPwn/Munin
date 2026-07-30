import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Munin — Autonomous Threat Intelligence & AI Security Agent",
  description:
    "Munin is an autonomous threat intelligence and offensive security AI agent featuring ReAct orchestration, durable Turso persistence, episodic memory, dynamic tool forging, and multi-agent coordination via MCP.",
  icons: {
    icon: "/raven-mark.png",
    shortcut: "/raven-mark.png",
    apple: "/raven-mark.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-bg text-body min-h-screen">{children}</body>
    </html>
  );
}
