"use client";

import { useState } from "react";
import { localDateTimeToUtc } from "@/lib/timezone";
import type { Device } from "@/types/api";

export type EventFilterValues = { eventType: string; deviceId: string; dateFrom: string; dateTo: string };
export const emptyEventFilters: EventFilterValues = { eventType: "", deviceId: "", dateFrom: "", dateTo: "" };

export function EventFilters({ devices, timeZone, onApply }: { devices: Device[]; timeZone: string; onApply: (values: EventFilterValues) => void }) {
  const [values, setValues] = useState(emptyEventFilters);
  const [error, setError] = useState("");
  function update(name: keyof EventFilterValues, value: string) { setError(""); setValues((current) => ({ ...current, [name]: value })); }
  function apply() {
    try {
      if (values.dateFrom) localDateTimeToUtc(values.dateFrom, timeZone);
      if (values.dateTo) localDateTimeToUtc(values.dateTo, timeZone);
      setError(""); onApply(values);
    } catch { setError("Esa fecha y hora no existe en la zona horaria de la vivienda. Corregila para aplicar el filtro."); }
  }
  return <form className="event-filters" role="search" aria-label="Filtrar eventos" onSubmit={(event) => { event.preventDefault(); apply(); }}>
    <div className="field"><label htmlFor="event-type">Tipo de evento</label><input id="event-type" value={values.eventType} maxLength={128} onChange={(event) => update("eventType", event.target.value)} /></div>
    <div className="field"><label htmlFor="event-device">Dispositivo</label><select id="event-device" value={values.deviceId} onChange={(event) => update("deviceId", event.target.value)}><option value="">Todos los dispositivos</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.name} · {device.device_id}</option>)}</select></div>
    <div className="field"><label htmlFor="event-from">Desde</label><input id="event-from" type="datetime-local" value={values.dateFrom} onChange={(event) => update("dateFrom", event.target.value)} /></div>
    <div className="field"><label htmlFor="event-to">Hasta</label><input id="event-to" type="datetime-local" value={values.dateTo} onChange={(event) => update("dateTo", event.target.value)} /></div><p className="muted">Zona horaria: {timeZone}</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="actions"><button className="button button--primary" type="submit">Aplicar filtros</button><button className="button button--secondary" type="button" disabled={!Object.values(values).some(Boolean)} onClick={() => { setError(""); setValues(emptyEventFilters); onApply(emptyEventFilters); }}>Limpiar filtros</button></div>
  </form>;
}
