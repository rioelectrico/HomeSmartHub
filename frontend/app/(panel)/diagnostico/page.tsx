"use client";

import { useMemo, useState } from "react";
import { DiagnosticGrid } from "@/components/diagnostics/diagnostic-grid";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { useResource } from "@/lib/use-resource";
import type { Diagnostics } from "@/types/api";

export default function DiagnosticsPage() {
  const home = useHome();
  const canRead = Boolean(home?.permissions.includes("devices.read"));
  const canControl = Boolean(home?.permissions.includes("devices.control"));
  const [cursor, setCursor] = useState<string>();
  const [history, setHistory] = useState<(string | undefined)[]>([]);
  const path = useMemo(() => home && canRead ? `/api/homes/${home.id}/diagnostics?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}` : null, [home, canRead, cursor]);
  const resource = useResource<Diagnostics>(path, 15000);
  return <section><div className="dashboard__heading"><div><h1>Diagnóstico</h1><p>Servicios y telemetría pública de los dispositivos.</p></div></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canRead ? <p>Sin permiso para ver diagnósticos.</p> : <><p className="muted">Actualización cada 15 segundos mientras esta pestaña está visible.</p><ResourceFeedback {...resource} onRetry={resource.refresh} />{resource.data && <><DiagnosticGrid homeId={home.id} data={resource.data} canControl={canControl} timeZone={home.timezone} onTerminal={() => void resource.refresh()} /><nav className="pagination panel-card diagnostics-pagination" aria-label="Paginación de diagnósticos"><button className="button button--secondary" disabled={!history.length} onClick={() => { const prior = [...history]; setCursor(prior.pop()); setHistory(prior); }}>Anterior</button><p>Página {history.length + 1}</p><button className="button button--secondary" disabled={!resource.data?.devices.next_cursor} onClick={() => { setHistory([...history, cursor]); setCursor(resource.data!.devices.next_cursor!); }}>Siguiente</button></nav></>}</>}
  </section>;
}
