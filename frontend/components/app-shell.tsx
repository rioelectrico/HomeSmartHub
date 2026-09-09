"use client";

import { LogOut, Menu } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { Drawer, Sidebar } from "@/components/sidebar";
import { logout } from "@/lib/auth";

type ShellUser = {
  username: string;
  permissions: string[];
};

type AppShellProps = {
  user: ShellUser;
  children: React.ReactNode;
};

export function AppShell({ user, children }: AppShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const router = useRouter();

  function closeDrawer() {
    setDrawerOpen(false);
    triggerRef.current?.focus();
  }

  async function handleLogout() {
    setIsSigningOut(true);
    setLogoutError(undefined);
    try {
      await logout();
      router.replace("/login");
    } catch {
      setLogoutError("No pudimos cerrar sesión. Intentá nuevamente.");
    } finally {
      setIsSigningOut(false);
    }
  }

  return (
    <div className="app-shell">
      <Sidebar permissions={user.permissions} />
      {drawerOpen && <Drawer permissions={user.permissions} onClose={closeDrawer} />}
      <header className="mobile-header">
        <button ref={triggerRef} className="menu-button" type="button" aria-label="Abrir navegación" aria-expanded={drawerOpen} aria-controls={drawerOpen ? "mobile-navigation" : undefined} onClick={() => setDrawerOpen(true)}>
          <Menu aria-hidden="true" />
        </button>
        <span className="mobile-header__title">Home Smart Hub</span>
      </header>
      <main className="app-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Hogar conectado</p>
            <p className="topbar__name">Hola, {user.username}</p>
          </div>
          <button className="sign-out" type="button" aria-label={isSigningOut ? "Cerrando sesión" : "Cerrar sesión"} onClick={handleLogout} disabled={isSigningOut}>
            <LogOut aria-hidden="true" />
            <span>{isSigningOut ? "Cerrando sesión…" : "Cerrar sesión"}</span>
          </button>
        </header>
        {logoutError && <p className="form-error" role="alert">{logoutError}</p>}
        {children}
      </main>
    </div>
  );
}
