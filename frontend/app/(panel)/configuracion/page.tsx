"use client";

import { useHome } from "@/components/home-context";

export default function ConfigurationPage() {
  const home = useHome();
  const apiOrigin = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "Mismo origen mediante el proxy /api";
  return <section><div className="dashboard__heading"><div><h1>Configuración</h1><p>Valores operativos no secretos disponibles en esta entrega.</p></div></div>
    {!home ? <p>No tenés viviendas asignadas.</p> : <div className="panel-card config-card"><h2>Configuración de la vivienda</h2><p className="muted">Sólo lectura. Los secretos y credenciales no se exponen en el panel.</p><dl className="detail-list"><div><dt>Vivienda activa</dt><dd>{home.name}</dd></div><div><dt>Zona horaria</dt><dd>{home.timezone}</dd></div><div><dt>Rol actual</dt><dd>{home.role}</dd></div><div><dt>Origen de API</dt><dd>{apiOrigin}</dd></div><div><dt>Actualización operativa</dt><dd>15 segundos, sólo con pestaña visible</dd></div></dl></div>}
  </section>;
}
