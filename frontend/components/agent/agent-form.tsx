"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { FormError } from "@/components/form-error";
import { agentSchema, type AgentValues } from "@/lib/schemas/agent";
import type { AgentConfig } from "@/types/api";

export function AgentForm({ initialValue, onSave, canEdit = true }: { initialValue: AgentConfig; onSave: (value: AgentValues) => Promise<AgentConfig>; canEdit?: boolean }) {
  const [saved, setSaved] = useState(initialValue);
  const [message, setMessage] = useState("");
  const { register, handleSubmit, reset, setError, formState: { errors, isSubmitting } } = useForm<AgentValues>({ resolver: zodResolver(agentSchema), defaultValues: initialValue });
  const fields = [
    ["name", "Nombre del agente", 128], ["language", "Idioma", 32],
    ["voice", "Voz", 128], ["realtime_model", "Modelo Realtime", 128],
  ] as const;

  async function submit(values: AgentValues) {
    if (!canEdit) return;
    setMessage("");
    try {
      const response = await onSave(values);
      setSaved(response); reset(response); setMessage("Configuración guardada.");
    } catch { setError("root", { message: "No pudimos guardar. Revisá tus permisos y conexión e intentá nuevamente." }); }
  }

  return <form className="panel-card agent-form" noValidate onSubmit={handleSubmit(submit)}>
    <h2>Configuración del agente</h2>
    <p>Definí cómo recibe a tus visitantes. Los cambios se aplican al guardar.</p>
    {!canEdit && <p>Tenés acceso de solo lectura.</p>}
    <FormError id="agent-save-error" message={errors.root?.message} />
    <fieldset disabled={!canEdit || isSubmitting}>
      <div className="form-grid">{fields.map(([name, label, maxLength]) => <div className="field" key={name}>
        <label htmlFor={`agent-${name}`}>{label}</label>
        <input id={`agent-${name}`} maxLength={maxLength} aria-invalid={Boolean(errors[name])} aria-describedby={errors[name] ? `agent-${name}-error` : undefined} {...register(name)} />
        <FormError id={`agent-${name}-error`} message={errors[name]?.message} />
      </div>)}</div>
      <div className="field"><label htmlFor="agent-system-prompt">Instrucciones del sistema</label>
        <textarea id="agent-system-prompt" rows={6} maxLength={8000} aria-invalid={Boolean(errors.system_prompt)} aria-describedby={errors.system_prompt ? "agent-prompt-error" : undefined} {...register("system_prompt")} />
        <FormError id="agent-prompt-error" message={errors.system_prompt?.message} />
      </div>
      <div className="field"><label htmlFor="agent-speed">Velocidad de voz (0,5 a 2)</label>
        <input id="agent-speed" type="number" min={0.5} max={2} step={0.1} aria-invalid={Boolean(errors.voice_speed)} aria-describedby={errors.voice_speed ? "agent-speed-error" : undefined} {...register("voice_speed", { valueAsNumber: true })} />
        <FormError id="agent-speed-error" message={errors.voice_speed?.message} />
      </div>
      <div className="checkbox-field"><input id="agent-enabled" type="checkbox" {...register("enabled")} /><label htmlFor="agent-enabled">Agente habilitado</label></div>
    </fieldset>
    {canEdit && <div className="actions"><button type="submit" className="button button--primary" disabled={isSubmitting}>{isSubmitting ? "Guardando…" : "Guardar cambios"}</button>
      <button type="button" className="button button--secondary" disabled={isSubmitting} onClick={() => { reset(saved); setMessage(""); }}>Restaurar último guardado</button></div>}
    {message && <p role="status" className="success-message">{message}</p>}
    <p className="muted">{saved.updated_at ? `Último guardado: ${new Date(saved.updated_at).toLocaleString("es-AR")}` : "Todavía no se guardó una configuración."}</p>
  </form>;
}
