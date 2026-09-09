import type { Metadata } from "next";
import { AuthRedirect } from "@/components/auth-redirect";
import "./globals.css";

export const metadata: Metadata = {
  title: "Home Smart Hub",
  description: "Operación segura de accesos para el hogar.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body><AuthRedirect />{children}</body>
    </html>
  );
}
