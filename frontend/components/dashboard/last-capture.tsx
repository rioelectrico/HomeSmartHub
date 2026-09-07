"use client";
import { Camera } from "lucide-react";
import Image from "next/image";
import { useState } from "react";

export type Capture = { url: string; capturedAt: string; deviceName: string };
export function LastCapture({ capture }: { capture?: Capture | null }) {
  const [failedUrl, setFailedUrl] = useState<string>();
  return <article className="panel-card"><h2>Última captura</h2>{capture ? <>
    {failedUrl === capture.url ? <p role="alert">No pudimos cargar la imagen. Verificá tu acceso a la cámara.</p> : <Image className="capture-image" src={capture.url} alt={`Última captura de ${capture.deviceName}`} width={640} height={480} unoptimized onError={() => setFailedUrl(capture.url)} />}
    <p className="muted">{new Date(capture.capturedAt).toLocaleString("es-AR")}</p>
  </> : <div className="empty-state"><Camera aria-hidden="true" /><p>Captura no disponible</p><p className="muted">Este resumen todavía no recibe imágenes del dispositivo.</p></div>}</article>;
}
