import { StatusBadge } from "@/components/status-badge";
import type { DeviceDiagnostic } from "@/types/api";

function snapshotValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "Disponible" : "No disponible";
  return typeof value === "string" || typeof value === "number" ? String(value) : "Sin datos";
}
export function DeviceSummary({ device }: { device: DeviceDiagnostic }) {
  const status = device.status === "online" && device.connected ? "online" : device.status === "disabled" || device.status === "provisioning" ? "attention" : "offline";
  return <article className="panel-card"><div className="card-heading"><div><p className="eyebrow">{device.device_id}</p><h2>{device.name}</h2></div><StatusBadge status={status} /></div>
    {status === "attention" && <p>{device.status === "disabled" ? "Deshabilitado" : "Pendiente de aprovisionamiento"}</p>}
    <dl className="detail-list">{([ ["Firmware", "firmware_version"], ["Hardware", "hardware_model"], ["Ethernet", "ethernet"], ["Cámara", "camera"], ["Micrófono", "microphone"], ["Altavoz", "speaker"] ] as const).map(([label, key]) => <div key={key}><dt>{label}</dt><dd>{snapshotValue(device.snapshot?.[key])}</dd></div>)}</dl>
    <p className="muted">Última conexión: {device.last_seen_at ? new Date(device.last_seen_at).toLocaleString("es-AR") : "Sin registro"}</p>
    <p className="muted">Diagnóstico del dispositivo: {device.snapshot_at ? new Date(device.snapshot_at).toLocaleString("es-AR") : "Sin fecha disponible"}</p>
  </article>;
}
