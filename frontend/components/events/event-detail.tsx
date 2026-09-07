"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { ResourceFeedback } from "@/components/resource-feedback";
import { useResource } from "@/lib/use-resource";
import { formatHomeDateTime } from "@/lib/timezone";
import type { HomeEvent } from "@/types/api";

function display(value: unknown) {
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "Valor no disponible";
}

export function EventDetail({ homeId, eventId, timeZone, trigger, onClose }: { homeId: string; eventId: string; timeZone: string; trigger: HTMLButtonElement; onClose: () => void }) {
  const resource = useResource<HomeEvent>(`/api/homes/${homeId}/events/${eventId}`, 0);
  const entries = Object.entries(resource.data?.payload ?? {}).slice(0, 50);
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content" onCloseAutoFocus={(event) => { event.preventDefault(); trigger.focus(); }}>
    <Dialog.Title>Detalle del evento</Dialog.Title><Dialog.Description>Metadatos públicos asociados al evento. No se muestran secretos ni datos anidados.</Dialog.Description>
    <ResourceFeedback {...resource} onRetry={resource.refresh} />
    {resource.data && <><dl className="detail-list"><div><dt>Tipo</dt><dd>{resource.data.event_type}</dd></div><div><dt>Fecha</dt><dd><time dateTime={resource.data.created_at}>{formatHomeDateTime(resource.data.created_at, timeZone)}</time></dd></div><div><dt>Dispositivo</dt><dd>{resource.data.device_id ?? "Sin dispositivo"}</dd></div>{entries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{display(value)}</dd></div>)}</dl>{Object.keys(resource.data.payload).length > 50 && <p className="muted">Se muestran los primeros 50 valores permitidos.</p>}</>}
    <button className="button button--secondary" type="button" onClick={onClose}>Cerrar</button>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
