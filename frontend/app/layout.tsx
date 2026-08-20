import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SLC Document Tools",
  description: "Document formatting, linked image retrieval and Word/PDF utilities for South London College.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
