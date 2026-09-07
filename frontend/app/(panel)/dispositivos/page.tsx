"use client";

import { Cpu, Plus } from "lucide-react";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { CommandTimeline } from "@/components/devices/command-timeline";
import { DevicesTable, type DeviceAction } from "@/components/devices/devices-table";
import { ProvisionDialog } from "@/components/devices/provision-dialog";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { ApiError, apiFetch } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import type { Device, DeviceCommand, Devices } from "@/types/api";

type Selection = { action: DeviceAction | "create"; device?: Device; trigger: HTMLButtonElement };
type CommandContext = { deviceId: string; deviceName: string; command: DeviceCommand };

export default function DevicesPage() {
  const home = useHome();
  const canRead = Boolean(home?.permissions.includes("devices.read"));
  const canManage = Boolean(home?.permissions.includes("devices.manage"));
  const canControl = Boolean(home?.permissions.includes("devices.control"));
  const resource = useResource<Devices>(home && canRead ? `/api/homes/${home.id}/devices` : null, 15000);
  const [selection, setSelection] = useState<Selection>();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirmationError, setConfirmationError] = useState("");
  const [busy, setBusy] = useState(false);
  const [commandContext, setCommandContext] = useState<CommandContext>();

  function choose(action: Selection["action"], device: Device | undefined, trigger: HTMLButtonElement) { setMessage(""); setError(""); setConfirmationError(""); setSelection({ action, device, trigger }); }
  async function lifecycle() {
    if (!home || !selection?.device || (selection.action !== "enable" && selection.action !== "disable")) return;
    setBusy(true); setConfirmationError("");
    try {
      await apiFetch(`/api/homes/${home.id}/devices/${selection.device.id}/${selection.action}`, { method: "POST" });
      setMessage(selection.action === "disable" ? "Dispositivo deshabilitado." : "Dispositivo habilitado."); setSelection(undefined); await resource.refresh();
    } catch (caught) { setConfirmationError(caught instanceof ApiError && caught.status === 403 ? "No tenés permiso para realizar esta acción." : "No pudimos actualizar el dispositivo."); }
    finally { setBusy(false); }
  }
  async function requestStatus(device: Device, trigger: HTMLButtonElement) {
    if (!home) return;
    setError(""); setCommandContext(undefined); setSelection({ action: "status", device, trigger });
    try {
      const command = await apiFetch<DeviceCommand>(`/api/homes/${home.id}/devices/${device.id}/commands`, { method: "POST", body: JSON.stringify({ command: "device.status.request", payload: {} }) });
      setCommandContext({ deviceId: device.id, deviceName: device.name, command });
    }
    catch { setError("No pudimos solicitar el estado del dispositivo."); }
  }

  return <section><div className="dashboard__heading"><div><h1>Dispositivos</h1><p>Conexión, credenciales y control de los equipos del hogar.</p></div>{canRead && canManage && <button className="button button--primary" onClick={(event) => choose("create", undefined, event.currentTarget)}><Plus aria-hidden="true" />Aprovisionar dispositivo</button>}</div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canRead ? <p>Sin permiso para ver dispositivos.</p> : <>
      <p className="muted">El estado se actualiza cada 15 segundos mientras esta pestaña está visible.</p>
      {message && <p className="success-message" role="status">{message}</p>}{error && <p className="form-error" role="alert">{error}</p>}
      <ResourceFeedback {...resource} onRetry={resource.refresh} />
      {resource.data && <DevicesTable devices={resource.data.items} canManage={canManage} canControl={canControl} timeZone={home.timezone} onAction={(action, device, trigger) => action === "status" ? void requestStatus(device, trigger) : choose(action, device, trigger)} />}
      {commandContext && <div className="panel-card command-card"><h2><Cpu aria-hidden="true" />Comando para {commandContext.deviceName}</h2><CommandTimeline homeId={home.id} deviceId={commandContext.deviceId} command={commandContext.command} timeZone={home.timezone} onTerminal={() => void resource.refresh()} /></div>}
      {selection && (selection.action === "create" || selection.action === "rotate") && canManage && <ProvisionDialog homeId={home.id} trigger={selection.trigger} device={selection.action === "rotate" ? selection.device : undefined} onClose={() => setSelection(undefined)} onSaved={(text) => { setMessage(text); void resource.refresh(); }} />}
      {selection?.device && (selection.action === "enable" || selection.action === "disable") && canManage && <ConfirmDialog title={`${selection.action === "disable" ? "Deshabilitar" : "Habilitar"} ${selection.device.name}`} description={selection.action === "disable" ? "El dispositivo dejará de autenticarse hasta que vuelvas a habilitarlo." : "El dispositivo podrá autenticarse nuevamente."} confirmLabel={selection.action === "disable" ? "Confirmar deshabilitación" : "Confirmar habilitación"} busy={busy} error={confirmationError} trigger={selection.trigger} onClose={() => { setConfirmationError(""); setSelection(undefined); }} onConfirm={lifecycle} />}
    </>}
  </section>;
}
