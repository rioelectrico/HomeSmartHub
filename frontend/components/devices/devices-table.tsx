"use client";

import { KeyRound, Power, Radio } from "lucide-react";
import { useState } from "react";
import { formatHomeDateTime } from "@/lib/timezone";
import type { Device } from "@/types/api";

export type DeviceAction = "rotate" | "enable" | "disable" | "status";
const statusLabels = { online: "En línea", offline: "Sin conexión", provisioning: "Pendiente de aprovisionamiento", disabled: "Deshabilitado" } as const;

export function DevicesTable({ devices, canManage, canControl, timeZone, onAction }: { devices: Device[]; canManage: boolean; canControl: boolean; timeZone: string; onAction: (action: DeviceAction, device: Device, trigger: HTMLButtonElement) => void }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(devices.length / 10));
  if (!devices.length) return <div className="panel-card empty-state"><p>No hay dispositivos en esta vivienda.</p></div>;
  return <div className="panel-card users-list"><table className="users-table devices-table"><caption>Dispositivos de la vivienda · {devices.length} en total</caption><thead><tr><th scope="col">Dispositivo</th><th scope="col">Estado</th><th scope="col">Última conexión</th>{(canManage || canControl) && <th scope="col">Acciones</th>}</tr></thead><tbody>
    {devices.slice(page * 10, page * 10 + 10).map((device) => <tr key={device.id}><th scope="row"><span>{device.name}</span><span className="user-email">{device.device_id}</span></th><td data-label="Estado"><span className={`status-badge status-badge--${device.status === "online" ? "online" : device.status === "offline" ? "offline" : "attention"}`}>{statusLabels[device.status]}</span></td><td data-label="Última conexión">{device.last_seen_at ? <time dateTime={device.last_seen_at}>{formatHomeDateTime(device.last_seen_at, timeZone)}</time> : "Sin registro"}</td>{(canManage || canControl) && <td><div className="actions user-actions">
      {canControl && <button className="button button--secondary" aria-label={`Solicitar estado de ${device.name}`} onClick={(event) => onAction("status", device, event.currentTarget)}><Radio aria-hidden="true" /><span>Solicitar estado</span></button>}
      {canManage && <button className="button button--secondary" aria-label={`Rotar secreto de ${device.name}`} onClick={(event) => onAction("rotate", device, event.currentTarget)}><KeyRound aria-hidden="true" /><span>Rotar secreto</span></button>}
      {canManage && <button className="button button--secondary" aria-label={`${device.status === "disabled" ? "Habilitar" : "Deshabilitar"} ${device.name}`} onClick={(event) => onAction(device.status === "disabled" ? "enable" : "disable", device, event.currentTarget)}><Power aria-hidden="true" /><span>{device.status === "disabled" ? "Habilitar" : "Deshabilitar"}</span></button>}
    </div></td>}</tr>)}
  </tbody></table><nav className="pagination" aria-label="Paginación de dispositivos"><button className="button button--secondary" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Anterior</button><p>Página {page + 1} de {pages}</p><button className="button button--secondary" disabled={page + 1 >= pages} onClick={() => setPage((value) => value + 1)}>Siguiente</button></nav></div>;
}
