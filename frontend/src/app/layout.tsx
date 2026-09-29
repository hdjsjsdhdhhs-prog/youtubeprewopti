import type { Metadata } from "next";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "YT Lead Intelligence", template: "%s · YT Lead Intelligence" },
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru">
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
