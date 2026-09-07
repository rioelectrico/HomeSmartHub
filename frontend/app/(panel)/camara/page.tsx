"use client";

import { CameraPanel } from "@/components/camera/camera-panel";
import { useHome } from "@/components/home-context";

export default function CameraPage() {
  const home = useHome();
  const canView = Boolean(home?.permissions.includes("camera.view") && home.permissions.includes("events.read") && home.permissions.includes("devices.read"));
  const canControl = Boolean(home?.permissions.includes("devices.control"));
  return <section><div className="dashboard__heading"><div><h1>Cámara</h1><p>Última imagen durable y capturas bajo demanda.</p></div></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canView ? <p>Sin permiso para ver la cámara o sus eventos.</p> : <CameraPanel homeId={home.id} canControl={canControl} timeZone={home.timezone} />}
  </section>;
}
