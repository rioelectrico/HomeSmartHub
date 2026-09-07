"use client";

import { Activity, Bot, Camera, Cpu, House, ListTree, Settings, Stethoscope, Users, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

type DrawerProps = {
  onClose: () => void;
  permissions?: string[];
};

const navigation = [
  { href: "/dashboard", label: "Dashboard", icon: House, permission: undefined },
  { href: "/agente", label: "Agente", icon: Bot, permission: "agent.view" },
  { href: "/camara", label: "Cámara", icon: Camera, permission: "camera.view" },
  { href: "/eventos", label: "Eventos", icon: ListTree, permission: "events.read" },
  { href: "/estadisticas", label: "Estadísticas", icon: Activity, permission: "events.read" },
  { href: "/usuarios", label: "Usuarios", icon: Users, permission: "users.read" },
  { href: "/dispositivos", label: "Dispositivos", icon: Cpu, permission: "devices.read" },
  { href: "/diagnostico", label: "Diagnóstico", icon: Stethoscope, permission: "devices.read" },
  { href: "/configuracion", label: "Configuración", icon: Settings, permission: "homes.read" },
];

function NavigationLinks({ onNavigate, permissions = [] }: { onNavigate?: () => void; permissions?: string[] }) {
  return (
    <nav>
      {navigation.filter(({ permission }) => !permission || permissions.includes(permission)).map(({ href, label, icon: Icon }) => (
        <Link href={href} key={href} onClick={onNavigate} className="nav-link">
          <Icon aria-hidden="true" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

export function Sidebar({ permissions = [] }: { permissions?: string[] }) {
  return (
    <aside className="sidebar" aria-label="Navegación principal">
      <div className="sidebar__brand">
        <span className="brand-mark" aria-hidden="true">P</span>
        <span>Portero inteligente</span>
      </div>
      <NavigationLinks permissions={permissions} />
    </aside>
  );
}

export function Drawer({ onClose, permissions = [] }: DrawerProps) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = drawerRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])');
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <>
      <button className="drawer-scrim" type="button" tabIndex={-1} aria-hidden="true" onClick={onClose} />
      <aside ref={drawerRef} id="mobile-navigation" className="drawer" role="dialog" aria-modal="true" aria-label="Navegación principal" onKeyDown={onKeyDown}>
        <div className="sidebar__brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span>Portero inteligente</span>
          <button ref={closeButtonRef} className="sidebar__close" type="button" aria-label="Cerrar navegación" onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </div>
        <NavigationLinks permissions={permissions} onNavigate={onClose} />
      </aside>
    </>
  );
}
