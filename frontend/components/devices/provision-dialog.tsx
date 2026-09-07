"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Check, Copy } from "lucide-react";
import { useRef, useState } from "react";
import { FormError } from "@/components/form-error";
import { ApiError, apiFetch } from "@/lib/api";
import { deviceSchema } from "@/lib/schemas/device";
import type { Device, ProvisionedDevice } from "@/types/api";

export function ProvisionDialog({ homeId, trigger, device, onClose, onSaved }: { homeId: string; trigger: HTMLButtonElement; device?: Device; onClose: () => void; onSaved: (message: string) => void }) {
  const [values, setValues] = useState({ device_id: "", name: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [secret, setSecret] = useState<string>();
  const [acknowledged, setAcknowledged] = useState(false);
  const [copied, setCopied] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const busy = useRef(false);
  const rotating = Boolean(device);
  const title = rotating ? `Rotar secreto de ${device?.name}` : "Aprovisionar dispositivo";
  const canClose = !submitting && (!secret || acknowledged);

  function close() {
    if (!canClose) return;
    setValues({ device_id: "", name: "" }); setErrors({}); setSecret(undefined); setAcknowledged(false); setCopied(false);
    onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy.current) return;
    let body: { device_id: string; name: string } | undefined;
    if (!rotating) {
      const parsed = deviceSchema.safeParse(values);
      if (!parsed.success) {
        const next: Record<string, string> = {};
        for (const issue of parsed.error.issues) next[String(issue.path[0])] ??= issue.message;
        setErrors(next); document.getElementById(`device-${Object.keys(next)[0]}`)?.focus(); return;
      }
      body = parsed.data;
    }
    setErrors({}); busy.current = true; setSubmitting(true);
    try {
      const response = await apiFetch<ProvisionedDevice>(rotating ? `/api/homes/${homeId}/devices/${device!.id}/rotate` : `/api/homes/${homeId}/devices`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      setSecret(response.secret);
      onSaved(rotating ? "Se rotó el secreto del dispositivo." : "Dispositivo aprovisionado.");
    } catch (error) {
      setErrors({ root: error instanceof ApiError && error.status === 409 ? "El identificador ya está registrado o el dispositivo no admite esta operación." : error instanceof ApiError && error.status === 403 ? "No tenés permiso para realizar esta acción." : "No pudimos aprovisionar el dispositivo. Intentá nuevamente." });
    } finally { busy.current = false; setSubmitting(false); }
  }
  async function copySecret() {
    if (!secret) return;
    try { await navigator.clipboard.writeText(secret); setCopied(true); } catch { setErrors({ root: "No pudimos copiar el secreto. Seleccionalo y copialo manualmente." }); }
  }

  return <Dialog.Root open onOpenChange={(open) => { if (!open) close(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content" onCloseAutoFocus={(event) => { event.preventDefault(); trigger.focus(); }} onEscapeKeyDown={(event) => { if (!canClose) event.preventDefault(); }} onPointerDownOutside={(event) => { if (!canClose) event.preventDefault(); }} aria-describedby="provision-description">
    <Dialog.Title>{title}</Dialog.Title>
    {!secret ? <form noValidate onSubmit={submit}>
      <Dialog.Description id="provision-description">{rotating ? "El secreto actual dejará de funcionar. El reemplazo se mostrará una sola vez." : "Registrá el equipo. El secreto se mostrará una sola vez al finalizar."}</Dialog.Description>
      <FormError id="device-root-error" message={errors.root} />
      {!rotating && <fieldset disabled={submitting}>
        <div className="field"><label htmlFor="device-device_id">Identificador del dispositivo</label><input id="device-device_id" value={values.device_id} onChange={(event) => setValues({ ...values, device_id: event.target.value.toUpperCase() })} maxLength={9} placeholder="PI-000001" aria-invalid={Boolean(errors.device_id)} aria-describedby={errors.device_id ? "device-id-error" : "device-id-hint"} /><p id="device-id-hint" className="muted">Formato: PI seguido de seis números.</p><FormError id="device-id-error" message={errors.device_id} /></div>
        <div className="field"><label htmlFor="device-name">Nombre del dispositivo</label><input id="device-name" value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} maxLength={128} aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? "device-name-error" : undefined} /><FormError id="device-name-error" message={errors.name} /></div>
      </fieldset>}
      <div className="actions"><button className="button button--primary" type="submit" disabled={submitting}>{submitting ? "Aprovisionando…" : rotating ? "Rotar secreto" : "Aprovisionar"}</button><button className="button button--secondary" type="button" disabled={submitting} onClick={close}>Cancelar</button></div>
    </form> : <div>
      <Dialog.Description id="provision-description"><strong>Guardalo ahora.</strong> Este secreto no volverá a mostrarse.</Dialog.Description>
      <output className="one-time-secret" aria-label="Secreto del dispositivo">{secret}</output>
      <button className="button button--secondary" type="button" onClick={() => void copySecret()}>{copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}{copied ? "Copiado" : "Copiar secreto"}</button>
      <div className="checkbox-field provision-ack"><input id="secret-ack" type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><label htmlFor="secret-ack">Guardé el secreto en un lugar seguro</label></div>
      <button className="button button--primary" type="button" disabled={!acknowledged} onClick={close}>Cerrar</button>
    </div>}
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
