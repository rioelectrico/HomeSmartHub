"use client";

import Image from "next/image";
import { Camera, ImageOff } from "lucide-react";
import { useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { CommandTimeline, isTerminalCommand } from "@/components/devices/command-timeline";
import { ResourceFeedback } from "@/components/resource-feedback";
import { apiFetch, resolveApiUrl } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { formatHomeDateTime } from "@/lib/timezone";
import type { Device, DeviceCommand, Devices, EventsPage, HomeEvent } from "@/types/api";

function mediaId(event?: HomeEvent) {
  return typeof event?.payload.media_id === "string" ? event.payload.media_id : undefined;
}

export function CameraPanel({ homeId, canControl, timeZone }: { homeId: string; canControl: boolean; timeZone: string }) {
  const devices = useResource<Devices>(`/api/homes/${homeId}/devices`, 15000);
  const [deviceId, setDeviceId] = useState("");
  const [command, setCommand] = useState<DeviceCommand>();
  const [confirming, setConfirming] = useState<HTMLButtonElement>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [fastPolling, setFastPolling] = useState(false);
  const effectiveDeviceId = devices.data?.items.some((device) => device.id === deviceId) ? deviceId : devices.data?.items[0]?.id ?? "";
  const selected = devices.data?.items.find((device) => device.id === effectiveDeviceId);
  const eventPath = effectiveDeviceId ? `/api/homes/${homeId}/events?event_type=camera_capture&device_id=${encodeURIComponent(effectiveDeviceId)}&limit=1` : null;
  const capturePollingInterval = command && command.status !== "completed" ? 0 : fastPolling ? 2000 : 15000;
  const captures = useResource<EventsPage>(eventPath, capturePollingInterval);
  const latest = captures.data?.items[0];
  const latestMediaId = mediaId(latest);
  const hasCurrentCapture = Boolean(command && latest?.payload.command_id === command.command_id && latestMediaId);
  useEffect(() => {
    if (!fastPolling) return;
    const timeout = setTimeout(() => setFastPolling(false), 30000);
    return () => clearTimeout(timeout);
  }, [fastPolling]);

  async function capture() {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const created = await apiFetch<DeviceCommand>(`/api/homes/${homeId}/devices/${selected.id}/commands`, { method: "POST", body: JSON.stringify({ command: "camera.capture", payload: {} }) });
      setCommand(created); setFastPolling(false); setConfirming(undefined);
    } catch { setError("No pudimos iniciar la captura. Verificá que el dispositivo esté conectado."); }
    finally { setBusy(false); }
  }

  return <>
    <div className="panel-card camera-panel">
      <div className="field camera-device"><label htmlFor="camera-device">Dispositivo</label><select id="camera-device" value={effectiveDeviceId} onChange={(event) => { setDeviceId(event.target.value); setCommand(undefined); setFastPolling(false); setError(""); }}>{devices.data?.items.map((device) => <option key={device.id} value={device.id}>{device.name} · {device.device_id}</option>)}</select></div>
      <ResourceFeedback {...devices} onRetry={devices.refresh} />
      {devices.data?.items.length === 0 && <div className="empty-state"><ImageOff aria-hidden="true" /><p>No hay dispositivos disponibles para la cámara.</p></div>}
      {selected && <>
        <div className="camera-heading"><div><p className="eyebrow">{selected.device_id}</p><h2>{selected.name}</h2></div>{canControl && <button className="button button--primary" type="button" disabled={selected.status !== "online"} onClick={(event) => setConfirming(event.currentTarget)}><Camera aria-hidden="true" />Capturar imagen</button>}</div>
        <ResourceFeedback {...captures} onRetry={captures.refresh} />
        {latestMediaId ? <figure className="capture-figure"><Image className="capture-image" src={resolveApiUrl(`/api/homes/${homeId}/media/${latestMediaId}`)} width={960} height={720} unoptimized alt={`Última captura de ${selected.name}`} /><figcaption>Capturada el <time dateTime={latest!.created_at}>{formatHomeDateTime(latest!.created_at, timeZone)}</time>.</figcaption></figure> : captures.data && <div className="empty-state"><ImageOff aria-hidden="true" /><p>Aún no hay capturas disponibles para este dispositivo.</p></div>}
        {!canControl && <p className="muted">Tenés acceso de visualización, pero no permiso para solicitar nuevas capturas.</p>}
      </>}
    </div>
    {command && selected && <div className="panel-card command-card"><h2>Progreso de la captura</h2><CommandTimeline homeId={homeId} deviceId={selected.id} command={command} timeZone={timeZone} onTerminal={(completed) => { setCommand(completed); if (completed.status === "completed") { setFastPolling(true); void captures.refresh(); } else setFastPolling(false); }} />{command.status === "completed" && !hasCurrentCapture && <p className="muted">Esperando que la imagen quede disponible. La búsqueda rápida se detiene después de 30 segundos.</p>}</div>}
    {confirming && selected && <ConfirmDialog title={`Capturar imagen con ${selected.name}`} description="El dispositivo capturará una imagen del acceso. Confirmá que esta acción es apropiada." confirmLabel="Confirmar captura" busy={busy} error={error} trigger={confirming} onClose={() => { setError(""); setConfirming(undefined); }} onConfirm={capture} />}
  </>;
}
