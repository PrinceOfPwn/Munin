import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Munin — What was once seen is never forgotten",
  description:
    "Living intelligence terminal for Munin, a multi-agent offensive security AI with persistent soul, episodic memory, and dynamic tool forging.",
  icons: {
    icon: [
      {
        url:
          "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E%3Cpath fill='%237c3aed' d='M60 25 C 67 25 72 30 72 38 C 72 44 68 48 64 49 L 82 33 L 70 42 C 78 38 80 33 80 33 L 68 36 Z M60 25 C 53 25 48 30 48 38 C 48 48 54 56 60 60 C 50 60 42 70 42 80 C 42 92 52 100 60 100 C 68 100 78 92 78 80 C 78 70 70 60 60 60 C 66 56 72 48 72 38 C 72 30 67 25 60 25 Z' /%3E%3C/svg%3E",
        type: "image/svg+xml",
      },
    ],
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
