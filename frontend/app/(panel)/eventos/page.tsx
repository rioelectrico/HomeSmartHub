"use client";

import { useMemo, useState } from "react";
import { AuditTable } from "@/components/events/audit-table";
import { EventDetail } from "@/components/events/event-detail";
import { emptyEventFilters, EventFilters, type EventFilterValues } from "@/components/events/event-filters";
import { EventTable } from "@/components/events/event-table";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { useResource } from "@/lib/use-resource";
import { localDateTimeToUtc } from "@/lib/timezone";
import type { Devices, EventsPage, HomeEvent } from "@/types/api";

type Detail = { event: HomeEvent; trigger: HTMLButtonElement };

export default function EventsPage() {
  const home = useHome();
  const canRead = Boolean(home?.permissions.includes("events.read"));
  const canAudit = Boolean(home?.permissions.includes("audit.read"));
  const canReadDevices = Boolean(home?.permissions.includes("devices.read"));
  const [tab, setTab] = useState<"events" | "audit">("events");
  const [filters, setFilters] = useState<EventFilterValues>(emptyEventFilters);
  const [cursor, setCursor] = useState<string>();
  const [history, setHistory] = useState<(string | undefined)[]>([]);
  const [detail, setDetail] = useState<Detail>();
  const eventPath = useMemo(() => {
    if (!home || !canRead) return null;
    const query = new URLSearchParams({ limit: "20" });
    if (filters.eventType.trim()) query.set("event_type", filters.eventType.trim());
    if (filters.deviceId) query.set("device_id", filters.deviceId);
    if (filters.dateFrom) query.set("date_from", localDateTimeToUtc(filters.dateFrom, home.timezone));
    if (filters.dateTo) query.set("date_to", localDateTimeToUtc(filters.dateTo, home.timezone));
    if (cursor) query.set("cursor", cursor);
    return `/api/homes/${home.id}/events?${query}`;
  }, [home, canRead, filters, cursor]);
  const events = useResource<EventsPage>(tab === "events" ? eventPath : null, 15000);
  const devices = useResource<Devices>(home && canRead && canReadDevices ? `/api/homes/${home.id}/devices` : null, 30000);

  return <section><div className="dashboard__heading"><div><h1>Eventos</h1><p>Actividad técnica y auditoría autorizada de la vivienda.</p></div></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canRead ? <p>Sin permiso para ver eventos.</p> : <>
      <div className="tabs" role="tablist" aria-label="Tipo de registro"><button role="tab" aria-selected={tab === "events"} onClick={() => setTab("events")}>Eventos</button>{canAudit && <button role="tab" aria-selected={tab === "audit"} onClick={() => setTab("audit")}>Auditoría</button>}</div>
      {tab === "events" ? <div className="panel-card users-list"><EventFilters devices={devices.data?.items ?? []} timeZone={home.timezone} onApply={(next) => { setFilters(next); setCursor(undefined); setHistory([]); }} /><ResourceFeedback {...events} onRetry={events.refresh} />{events.data && <><EventTable events={events.data.items} timeZone={home.timezone} onDetail={(event, trigger) => setDetail({ event, trigger })} /><nav className="pagination" aria-label="Paginación de eventos"><button className="button button--secondary" disabled={!history.length} onClick={() => { const prior = [...history]; setCursor(prior.pop()); setHistory(prior); }}>Anterior</button><p>Página {history.length + 1}</p><button className="button button--secondary" disabled={!events.data?.next_cursor} onClick={() => { setHistory([...history, cursor]); setCursor(events.data!.next_cursor!); }}>Siguiente</button></nav></>}</div> : canAudit ? <AuditTable homeId={home.id} timeZone={home.timezone} /> : null}
      {detail && <EventDetail homeId={home.id} eventId={detail.event.id} timeZone={home.timezone} trigger={detail.trigger} onClose={() => setDetail(undefined)} />}
    </>}
  </section>;
}
