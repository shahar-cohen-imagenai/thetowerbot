import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "The Tower bot",
  description: "Live dashboard for the ADB bot",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-background text-foreground antialiased">
        <div className="flex min-h-dvh flex-col md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 p-4">{children}</main>
        </div>
      </body>
    </html>
  );
}
