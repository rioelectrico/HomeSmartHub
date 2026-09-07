"use client";

import { useMemo, useState } from "react";
import { ResourceFeedback } from "@/components/resource-feedback";
import { useResource } from "@/lib/use-resource";
import { formatHomeDateTime, localDateTimeToUtc } from "@/lib/timezone";
import type { AuditPage } from "@/types/api";

function display(value: unknown) { return typeof value === "boolean" ? value ? "Sí" : "No" : typeof value === "string" || typeof value === "number" ? String(value) : "No disponible"; }

export function AuditTable({ homeId, timeZone }: { homeId: string; timeZone: string }) {
  const [draft, setDraft] = useState({ action: "", dateFrom: "", dateTo: "" });
  const [filters, setFilters] = useState(draft);
  const [filterError, setFilterError] = useState("");
  const [cursor, setCursor] = useState<string>();
  const [history, setHistory] = useState<(string | undefined)[]>([]);
  const path = useMemo(() => {
    const query = new URLSearchParams({ limit: "20" });
    if (filters.action.trim()) query.set("action", filters.action.trim());
    if (filters.dateFrom) query.set("date_from", localDateTimeToUtc(filters.dateFrom, timeZone));
    if (filters.dateTo) query.set("date_to", localDateTimeToUtc(filters.dateTo, timeZone));
    if (cursor) query.set("cursor", cursor);
    return `/api/homes/${homeId}/audit?${query}`;
  }, [homeId, timeZone, filters, cursor]);
  const resource = useResource<AuditPage>(path, 15000);
  function applyFilters() {
    try {
      if (draft.dateFrom) localDateTimeToUtc(draft.dateFrom, timeZone);
      if (draft.dateTo) localDateTimeToUtc(draft.dateTo, timeZone);
      setFilterError(""); setFilters(draft); setCursor(undefined); setHistory([]);
    } catch { setFilterError("Esa fecha y hora no existe en la zona horaria de la vivienda. Corregila para aplicar el filtro."); }
  }
  return <section className="panel-card users-list" aria-label="Registro de auditoría">
    <form className="event-filters audit-filters" role="search" aria-label="Filtrar auditoría" onSubmit={(event) => { event.preventDefault(); applyFilters(); }}>
      <div className="field"><label htmlFor="audit-action">Acción</label><input id="audit-action" value={draft.action} maxLength={128} onChange={(event) => setDraft({ ...draft, action: event.target.value })} /></div>
      <div className="field"><label htmlFor="audit-from">Desde</label><input id="audit-from" type="datetime-local" value={draft.dateFrom} onChange={(event) => setDraft({ ...draft, dateFrom: event.target.value })} /></div>
      <div className="field"><label htmlFor="audit-to">Hasta</label><input id="audit-to" type="datetime-local" value={draft.dateTo} onChange={(event) => setDraft({ ...draft, dateTo: event.target.value })} /></div><p className="muted">Zona horaria: {timeZone}</p>
      {filterError && <p className="form-error" role="alert">{filterError}</p>}<button className="button button--primary" type="submit">Aplicar filtros de auditoría</button>
    </form>
    <ResourceFeedback {...resource} onRetry={resource.refresh} />
    {resource.data && <>{resource.data.items.length ? <table className="users-table audit-table"><caption>Auditoría administrativa · máximo 20 registros por página</caption><thead><tr><th scope="col">Acción</th><th scope="col">Usuario</th><th scope="col">Fecha</th><th scope="col">Detalle público</th></tr></thead><tbody>{resource.data.items.map((entry) => <tr key={entry.id}><th scope="row">{entry.action}</th><td data-label="Usuario">{entry.user_id ?? "Sistema"}</td><td data-label="Fecha"><time dateTime={entry.created_at}>{formatHomeDateTime(entry.created_at, timeZone)}</time></td><td data-label="Detalle"><span>{entry.detail ?? "Sin detalle"}</span>{Object.entries(entry.context).slice(0, 20).map(([key, value]) => <span className="audit-context" key={key}>{key}: {display(value)}</span>)}</td></tr>)}</tbody></table> : <div className="empty-state"><p>No hay registros de auditoría que coincidan.</p></div>}
      <nav className="pagination" aria-label="Paginación de auditoría"><button className="button button--secondary" disabled={!history.length} onClick={() => { const prior = [...history]; setCursor(prior.pop()); setHistory(prior); }}>Anterior</button><p>Página {history.length + 1}</p><button className="button button--secondary" disabled={!resource.data.next_cursor} onClick={() => { setHistory([...history, cursor]); setCursor(resource.data!.next_cursor!); }}>Siguiente</button></nav></>}
  </section>;
}
