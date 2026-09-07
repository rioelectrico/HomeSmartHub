"use client";

import { useEffect, useRef } from "react";
import { useResource } from "@/lib/use-resource";
import { formatHomeDateTime } from "@/lib/timezone";
import type { CommandStatus, DeviceCommand } from "@/types/api";

const labels: Record<CommandStatus, string> = {
  pending: "Pendiente",
  sent: "Enviado",
  acknowledged: "Recibido por el dispositivo",
  completed: "Completado",
  failed: "Fallido",
  timeout: "Tiempo de espera agotado",
};

export const isTerminalCommand = (status: CommandStatus) => ["completed", "failed", "timeout"].includes(status);

export function CommandTimeline({ homeId, deviceId, command, timeZone, onTerminal }: { homeId: string; deviceId: string; command: DeviceCommand; timeZone: string; onTerminal?: (command: DeviceCommand) => void }) {
  const resource = useResource<DeviceCommand>(isTerminalCommand(command.status) ? null : `/api/homes/${homeId}/devices/${deviceId}/commands/${command.command_id}`, 1000, (value) => isTerminalCommand(value.status));
  const current = resource.data ?? command;
  const notified = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (isTerminalCommand(current.status) && notified.current !== current.command_id) {
      notified.current = current.command_id;
      onTerminal?.(current);
    }
  }, [current, onTerminal]);
  return <section className="command-timeline" aria-label="Progreso del comando" aria-live="polite">
    <p><strong>Estado actual:</strong> {labels[current.status]}</p>
    <ol>{current.timeline.map((entry, index) => <li key={`${entry.status}-${entry.at}-${index}`}><span>{labels[entry.status]}</span><time dateTime={entry.at}>{formatHomeDateTime(entry.at, timeZone)}</time></li>)}</ol>
    {resource.error && !isTerminalCommand(current.status) && <p className="form-error" role="alert">No pudimos actualizar el progreso. Se reintentará mientras la pestaña esté visible.</p>}
  </section>;
}
