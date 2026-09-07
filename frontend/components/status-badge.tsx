import { cn } from "@/lib/utils";

type Status = "online" | "attention" | "offline";

const labels: Record<Status, string> = {
  online: "En línea",
  attention: "Requiere atención",
  offline: "Sin conexión",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={cn("status-badge", `status-badge--${status}`)}>{labels[status]}</span>;
}
