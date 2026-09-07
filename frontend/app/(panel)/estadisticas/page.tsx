"use client";

import { ActivityChart } from "@/components/statistics/activity-chart";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { useResource } from "@/lib/use-resource";
import { formatHomeDateTime } from "@/lib/timezone";
import type { Statistics } from "@/types/api";

export default function StatisticsPage() {
  const home = useHome();
  const canRead = Boolean(home?.permissions.includes("events.read"));
  const resource = useResource<Statistics>(home && canRead ? `/api/homes/${home.id}/statistics` : null, 30000);
  return <section><div className="dashboard__heading"><div><h1>Estadísticas</h1><p>Resumen de actividad calculado en la zona horaria del hogar.</p></div></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canRead ? <p>Sin permiso para ver estadísticas.</p> : <><p className="muted">Actualización cada 30 segundos mientras esta pestaña está visible.</p><ResourceFeedback {...resource} onRetry={resource.refresh} />{resource.data && <><p className="muted">Generado: <time dateTime={resource.data.generated_at}>{formatHomeDateTime(resource.data.generated_at, home.timezone)}</time> · {home.timezone}</p><ActivityChart statistics={resource.data} /></>}</>}
  </section>;
}
