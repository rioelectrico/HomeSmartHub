"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { HomeContext } from "@/components/home-context";
import { ApiError } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import type { CurrentUser } from "@/types/api";

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [failed, setFailed] = useState(false);
  const [homeId, setHomeId] = useState<string>();
  const router = useRouter();

  useEffect(() => {
    getCurrentUser().then(setUser).catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        router.replace("/login");
        return;
      }
      setFailed(true);
    });
  }, [router]);

  if (failed) return <main className="page-loader"><p role="alert">No pudimos verificar tu sesión.</p><Link className="button button--primary" href="/login">Volver a iniciar sesión</Link></main>;
  if (!user) return <main className="page-loader" role="status" aria-live="polite"><LoaderCircle className="spin" aria-hidden="true" /><span>Verificando sesión…</span></main>;
  const home = user.homes.find((item) => item.id === homeId) ?? user.homes[0] ?? null;
  return <HomeContext.Provider value={home}><AppShell user={{ username: user.username, permissions: home?.permissions ?? [] }}>
    {home && <div className="field home-selector"><label htmlFor="active-home">Vivienda activa</label><select id="active-home" value={home.id} onChange={(event) => setHomeId(event.target.value)}>{user.homes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>}
    <div key={home?.id ?? "no-home"}>{children}</div>
  </AppShell></HomeContext.Provider>;
}
