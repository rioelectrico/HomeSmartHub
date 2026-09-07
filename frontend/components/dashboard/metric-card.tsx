import type { LucideIcon } from "lucide-react";
export function MetricCard({ title, value, detail, icon: Icon }: { title: string; value: number; detail: string; icon: LucideIcon }) {
  return <article className="panel-card metric-card" aria-label={title}><div className="metric-card__label"><h2>{title}</h2><Icon aria-hidden="true" /></div><p className="metric-card__value">{value.toLocaleString("es-AR")}</p><p className="muted">{detail}</p></article>;
}
