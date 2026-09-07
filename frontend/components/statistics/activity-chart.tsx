"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Statistics } from "@/types/api";

export function ActivityChart({ statistics }: { statistics: Statistics }) {
  return <section className="panel-card activity-section" aria-labelledby="activity-title">
    <h2 id="activity-title">Actividad de los últimos 7 días</h2>
    <p>En total: {statistics.last_7_days.events} eventos, {statistics.last_7_days.conversations} conversaciones y {statistics.last_7_days.captures} capturas.</p>
    <ul className="chart-key" aria-label="Leyenda del gráfico"><li><span className="chart-key__swatch chart-key__swatch--events" aria-hidden="true" />Eventos</li><li><span className="chart-key__swatch chart-key__swatch--conversations" aria-hidden="true" />Conversaciones</li><li><span className="chart-key__swatch chart-key__swatch--captures" aria-hidden="true" />Capturas</li></ul>
    <div className="activity-chart" role="region" aria-label="Gráfico de actividad">
      <ResponsiveContainer width="100%" height={320} minWidth={280}>
        <LineChart data={statistics.daily} margin={{ top: 16, right: 12, bottom: 8, left: -12 }}>
          <CartesianGrid strokeDasharray="4 4" stroke="var(--line)" />
          <XAxis dataKey="date" tickFormatter={(value: string) => value.slice(5)} />
          <YAxis allowDecimals={false} />
          <Tooltip labelFormatter={(value) => `Fecha: ${String(value)}`} />
          <Legend />
          <Line type="monotone" dataKey="events" name="Eventos" stroke="var(--primary)" strokeWidth={3} dot={{ r: 3 }} isAnimationActive={false} />
          <Line type="monotone" dataKey="conversations" name="Conversaciones" stroke="var(--status-success)" strokeWidth={3} dot={{ r: 3 }} isAnimationActive={false} />
          <Line type="monotone" dataKey="captures" name="Capturas" stroke="var(--status-warning)" strokeWidth={3} dot={{ r: 3 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <div className="activity-table-wrap"><table className="users-table activity-table" aria-label="Actividad diaria"><caption>Valores diarios usados en el gráfico</caption><thead><tr><th scope="col">Fecha</th><th scope="col">Eventos</th><th scope="col">Conversaciones</th><th scope="col">Capturas</th></tr></thead><tbody>{statistics.daily.map((day) => <tr key={day.date}><th scope="row">{new Date(`${day.date}T12:00:00`).toLocaleDateString("es-AR")}</th><td data-label="Eventos">{day.events}</td><td data-label="Conversaciones">{day.conversations}</td><td data-label="Capturas">{day.captures}</td></tr>)}</tbody></table></div>
  </section>;
}
