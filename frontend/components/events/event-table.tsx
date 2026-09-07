import { Eye } from "lucide-react";
import { formatHomeDateTime } from "@/lib/timezone";
import type { HomeEvent } from "@/types/api";

export function EventTable({ events, timeZone, onDetail }: { events: HomeEvent[]; timeZone: string; onDetail: (event: HomeEvent, trigger: HTMLButtonElement) => void }) {
  if (!events.length) return <div className="empty-state"><p>No hay eventos que coincidan con los filtros.</p></div>;
  return <table className="users-table events-table"><caption>Eventos de la página actual · máximo 20</caption><thead><tr><th scope="col">Tipo</th><th scope="col">Dispositivo</th><th scope="col">Fecha</th><th scope="col">Detalle</th></tr></thead><tbody>{events.map((event) => <tr key={event.id}><th scope="row">{event.event_type}</th><td data-label="Dispositivo">{event.device_id ?? "Sistema"}</td><td data-label="Fecha"><time dateTime={event.created_at}>{formatHomeDateTime(event.created_at, timeZone)}</time></td><td><button className="button button--secondary" aria-label={`Ver detalle de ${event.event_type}`} onClick={(click) => onDetail(event, click.currentTarget)}><Eye aria-hidden="true" />Ver detalle</button></td></tr>)}</tbody></table>;
}
