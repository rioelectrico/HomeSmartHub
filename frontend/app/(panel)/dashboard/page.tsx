"use client";
import { Activity, Camera, MessageSquare, Radio } from "lucide-react";
import { useState } from "react";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { DeviceSummary } from "@/components/dashboard/device-summary";
import { LastCapture } from "@/components/dashboard/last-capture";
import { MetricCard } from "@/components/dashboard/metric-card";
import { useResource } from "@/lib/use-resource";
import type { Diagnostics, Statistics } from "@/types/api";

export default function DashboardPage() {
  const home = useHome();
  const [paused, setPaused] = useState(false);
  const canReadStatistics = home?.permissions.includes("events.read");
  const canReadDevices = home?.permissions.includes("devices.read");
  const stats = useResource<Statistics>(home && canReadStatistics ? `/api/homes/${home.id}/statistics` : null, paused ? 0 : 15000);
  const diagnostics = useResource<Diagnostics>(home && canReadDevices ? `/api/homes/${home.id}/diagnostics?limit=100` : null, paused ? 0 : 15000);
  return <section aria-labelledby="dashboard-title"><div className="dashboard__heading"><div><h1 id="dashboard-title">Dashboard</h1><p>Actividad y estado de tu hogar.</p></div><button className="button button--secondary" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Reanudar actualización" : "Pausar actualización"}</button></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : <>
      <p className="muted">{paused ? "Actualización automática pausada." : "Actualización cada 15 segundos mientras esta pestaña está visible."}</p>
      {!canReadStatistics ? <p>Sin permiso para ver estadísticas.</p> : <><ResourceFeedback {...stats} onRetry={stats.refresh} />{stats.data && <>
        <p className="muted">Actualizado: <time dateTime={stats.data.generated_at}>{new Date(stats.data.generated_at).toLocaleString("es-AR", { timeZone: stats.data.timezone })}</time> · {stats.data.timezone}</p>
        <div className="metrics-grid"><MetricCard title="Eventos hoy" value={stats.data.today.events} detail={`${stats.data.last_7_days.events} en los últimos 7 días`} icon={Activity} /><MetricCard title="Conversaciones hoy" value={stats.data.today.conversations} detail={`${stats.data.last_7_days.conversations} en los últimos 7 días`} icon={MessageSquare} /><MetricCard title="Capturas hoy" value={stats.data.today.captures} detail={`${stats.data.last_7_days.captures} en los últimos 7 días`} icon={Camera} /><MetricCard title="Dispositivos conectados" value={stats.data.devices.online} detail={`${stats.data.devices.total} dispositivos en total`} icon={Radio} /></div>
      </>}</>}
      {!canReadDevices ? <p>Sin permiso para ver diagnósticos.</p> : <><ResourceFeedback {...diagnostics} onRetry={diagnostics.refresh} />{diagnostics.data && <>
        <section className="panel-card service-summary" aria-label="Salud de servicios"><h2>Servicios</h2><div className="actions"><span>Backend: disponible</span><span>PostgreSQL: {diagnostics.data.services.postgresql === "up" ? "disponible" : "no disponible"}</span><span>OpenAI: {diagnostics.data.services.openai === "configured" ? "configurado" : "sin configurar"}</span></div><p className="muted">Actualizado: {new Date(diagnostics.updatedAt!).toLocaleString("es-AR")}. Configurado no implica disponibilidad comprobada.</p></section>
        <div className="dashboard-grid">{diagnostics.data.devices.items.map((device) => <DeviceSummary key={device.id} device={device} />)}{diagnostics.data.devices.items.length === 0 && <div className="panel-card"><p>No hay dispositivos en esta vivienda.</p></div>}<LastCapture /></div>
        {diagnostics.data.devices.next_cursor && <p>Se muestran los primeros 100 dispositivos de la vivienda.</p>}
      </>}</>}
    </>}
  </section>;
}
