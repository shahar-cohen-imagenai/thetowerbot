import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

// Self-hosted at build time, so the exported site has no runtime dependency on
// a font CDN - the dashboard has to come up on a laptop with no internet.
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

// Every number and every machine-emitted string in this app is set in mono
// (screen names, run ids, scores, coordinates, tracebacks). JetBrains Mono has
// the disambiguated glyphs that matters for - 0/O and 1/l/I read apart at 11px.
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "The Tower bot",
  description: "Live dashboard for the ADB bot",
};

// Applies the stored theme before first paint. This is a static export with no
// server render, so without it every reload flashes the default (dark) before
// React mounts and corrects it. Kept to one expression, and silent on failure:
// a browser that refuses localStorage should still get a themed page.
const APPLY_THEME = `try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: APPLY_THEME }} />
      </head>
      <body className="bg-background text-foreground antialiased">
        <div className="flex min-h-dvh flex-col md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 p-4">{children}</main>
        </div>
      </body>
    </html>
  );
}
