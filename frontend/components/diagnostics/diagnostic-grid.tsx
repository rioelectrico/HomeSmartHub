"use client";

import { Bot, Camera, Database, Radio, Server } from "lucide-react";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { CommandTimeline } from "@/components/devices/command-timeline";
import { apiFetch } from "@/lib/api";
import { formatHomeDateTime } from "@/lib/timezone";
import type { DeviceCommand, Diagnostics } from "@/types/api";

const serviceCopy = {
  backend: { label: "Backend", icon: Server },
  postgresql: { label: "PostgreSQL", icon: Database },
  openai: { label: "OpenAI", icon: Bot },
} as const;

const openaiStates: Record<Diagnostics["services"]["openai"], { label: string; healthy: boolean }> = {
  unconfigured: { label: "Sin configurar", healthy: false },
  configured: { label: "Configurado", healthy: true },
  available: { label: "Disponible", healthy: true },
  unavailable: { label: "No disponible", healthy: false },
};
function serviceStatus(service: keyof typeof serviceCopy, services: Diagnostics["services"]) {
  if (service === "openai") return openaiStates[services.openai];
  return { label: services[service] === "up" ? "Disponible" : "No disponible", healthy: services[service] === "up" };
}
function snapshot(value: unknown) { return typeof value === "boolean" ? value ? "Sí" : "No" : typeof value === "string" || typeof value === "number" ? String(value) : "Sin datos"; }

export function DiagnosticGrid({ homeId, data, canControl, timeZone, onTerminal }: { homeId: string; data: Diagnostics; canControl: boolean; timeZone: string; onTerminal: () => void }) {
  const [commands, setCommands] = useState<Record<string, DeviceCommand>>({});
  const [error, setError] = useState("");
  const [confirmationError, setConfirmationError] = useState("");
  const [busyDevice, setBusyDevice] = useState<string>();
  const [confirming, setConfirming] = useState<{ device: Diagnostics["devices"]["items"][number]; trigger: HTMLButtonElement }>();
  async function send(deviceId: string, command: "device.status.request" | "camera.capture") {
    setBusyDevice(deviceId); command === "camera.capture" ? setConfirmationError("") : setError("");
    try {
      const created = await apiFetch<DeviceCommand>(`/api/homes/${homeId}/devices/${deviceId}/commands`, { method: "POST", body: JSON.stringify({ command, payload: {} }) });
      setCommands((current) => ({ ...current, [deviceId]: created })); setConfirming(undefined);
    } catch { command === "camera.capture" ? setConfirmationError("No pudimos enviar el comando de diagnóstico.") : setError("No pudimos enviar el comando de diagnóstico."); }
    finally { setBusyDevice(undefined); }
  }
  return <>
    <div className="service-grid" aria-label="Estado de servicios">{(Object.keys(serviceCopy) as (keyof typeof serviceCopy)[]).map((key) => { const { label, icon: Icon } = serviceCopy[key]; const state = serviceStatus(key, data.services); return <article className="panel-card service-card" aria-label={label} key={key}><Icon aria-hidden="true" /><div><h2>{label}</h2><p role="status" className={`service-state service-state--${state.healthy ? "ok" : "attention"}`}>{state.label}</p></div></article>; })}</div>
    <p className="muted">OpenAI configurado indica presencia de configuración, no una prueba de disponibilidad externa.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="diagnostic-devices">{data.devices.items.map((device) => <article className="panel-card diagnostic-device" key={device.id}><div className="card-heading"><div><p className="eyebrow">{device.device_id}</p><h2>{device.name}</h2></div><span className={`status-badge status-badge--${device.status === "online" && device.connected ? "online" : device.status === "offline" ? "offline" : "attention"}`}>{device.status === "online" && device.connected ? "En línea" : device.status === "disabled" ? "Deshabilitado" : device.status === "provisioning" ? "Pendiente" : "Sin conexión"}</span></div>
      <dl className="detail-list"><div><dt>Última conexión</dt><dd>{device.last_seen_at ? <time dateTime={device.last_seen_at}>{formatHomeDateTime(device.last_seen_at, timeZone)}</time> : "Sin registro"}</dd></div><div><dt>Último diagnóstico</dt><dd>{device.snapshot_at ? <time dateTime={device.snapshot_at}>{formatHomeDateTime(device.snapshot_at, timeZone)}</time> : "Sin registro"}</dd></div>{Object.entries(device.snapshot ?? {}).slice(0, 20).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{snapshot(value)}</dd></div>)}</dl>
      {canControl && <div className="actions"><button className="button button--secondary" disabled={Boolean(busyDevice)} aria-label={`Solicitar estado de ${device.name}`} onClick={() => void send(device.id, "device.status.request")}><Radio aria-hidden="true" />Solicitar estado</button><button className="button button--secondary" disabled={Boolean(busyDevice) || device.status !== "online" || !device.connected} aria-label={`Probar cámara de ${device.name}`} onClick={(event) => setConfirming({ device, trigger: event.currentTarget })}><Camera aria-hidden="true" />Probar cámara</button></div>}
      {commands[device.id] && <CommandTimeline homeId={homeId} deviceId={device.id} command={commands[device.id]} timeZone={timeZone} onTerminal={onTerminal} />}
    </article>)}{data.devices.items.length === 0 && <div className="panel-card empty-state"><p>No hay diagnósticos de dispositivos disponibles.</p></div>}</div>
    {confirming && <ConfirmDialog title={`Probar cámara de ${confirming.device.name}`} description="El dispositivo capturará una imagen para comprobar la cámara." confirmLabel="Confirmar prueba" busy={busyDevice === confirming.device.id} error={confirmationError} trigger={confirming.trigger} onClose={() => { setConfirmationError(""); setConfirming(undefined); }} onConfirm={() => send(confirming.device.id, "camera.capture")} />}
  </>;
}
