"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { FormError } from "@/components/form-error";
import {
  buildAgentSessionOptions,
  readAgentSessionControls,
  supportsRealtimeReasoning,
  type AgentSessionControls,
} from "@/lib/openai-session-options";
import { agentSchema, type AgentValues } from "@/lib/schemas/agent";
import type { AgentConfig } from "@/types/api";

const REALTIME_MODELS = [
  ["env-default", "Environment default"],
  ["gpt-realtime-2.1", "GPT Realtime 2.1"],
  ["gpt-realtime-2.1-mini", "GPT Realtime 2.1 Mini"],
  ["gpt-realtime-2", "GPT Realtime 2"],
  ["gpt-realtime-1.5", "GPT Realtime 1.5"],
] as const;

const VOICE_OPTIONS = [
  "default", "marin", "cedar", "alloy", "ash", "ballad",
  "coral", "echo", "sage", "shimmer", "verse",
];

export function AgentForm({ initialValue, onSave, canEdit = true }: {
  initialValue: AgentConfig;
  onSave: (value: AgentValues) => Promise<AgentConfig>;
  canEdit?: boolean;
}) {
  const [saved, setSaved] = useState(initialValue);
  const [message, setMessage] = useState("");
  const [sessionControls, setSessionControls] = useState<AgentSessionControls>(() =>
    readAgentSessionControls(initialValue.openai_session_options ?? {})
  );
  const {
    register, handleSubmit, reset, control, setError, clearErrors,
    formState: { errors, isSubmitting },
  } = useForm<AgentValues>({ resolver: zodResolver(agentSchema), defaultValues: initialValue });
  const selectedModel = useWatch({ control, name: "realtime_model" });
  const reasoningSupported = supportsRealtimeReasoning(selectedModel);
  const knownModel = REALTIME_MODELS.some(([value]) => value === selectedModel);

  function updateSessionControl<K extends keyof AgentSessionControls>(key: K, value: AgentSessionControls[K]) {
    setSessionControls((current) => ({ ...current, [key]: value }));
  }

  function syncSaved(value: AgentConfig) {
    setSaved(value);
    reset(value);
    setSessionControls(readAgentSessionControls(value.openai_session_options ?? {}));
    setMessage("");
    clearErrors();
  }

  async function submit(values: AgentValues) {
    if (!canEdit) return;
    setMessage("");
    const openai_session_options = buildAgentSessionOptions(
      saved.openai_session_options ?? {}, sessionControls, values.realtime_model
    );
    try {
      const response = await onSave({ ...values, openai_session_options });
      syncSaved(response);
      setMessage("Configuración guardada.");
    } catch {
      setError("root", { message: "No pudimos guardar. Revisá permisos y conexión e intentá nuevamente." });
    }
  }

  return (
    <form className="panel-card agent-form" noValidate onSubmit={handleSubmit(submit)}>
      <h2>Configuración del agente</h2>
      <p>Configurá cómo conversa el agente. Los cambios se aplican a las sesiones nuevas.</p>
      {!canEdit && <p>Tenés acceso de solo lectura.</p>}
      <FormError id="agent-save-error" message={errors.root?.message} />
      <fieldset disabled={!canEdit || isSubmitting}>
        <section className="agent-settings-section" aria-labelledby="agent-identity-heading">
          <div className="agent-settings-heading">
            <h3 id="agent-identity-heading">Identidad</h3>
            <p>Nombre, idioma, instrucciones y modelo principal.</p>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="agent-name">Nombre del agente</label>
              <input id="agent-name" maxLength={128} aria-invalid={Boolean(errors.name)} {...register("name")} />
              <FormError id="agent-name-error" message={errors.name?.message} />
            </div>
            <div className="field">
              <label htmlFor="agent-language">Idioma</label>
              <input id="agent-language" maxLength={32} aria-invalid={Boolean(errors.language)} {...register("language")} />
              <FormError id="agent-language-error" message={errors.language?.message} />
            </div>
            <div className="field">
              <label htmlFor="agent-realtime-model">Modelo Realtime</label>
              <select id="agent-realtime-model" {...register("realtime_model")}>
                {!knownModel && <option value={selectedModel}>{selectedModel}</option>}
                {REALTIME_MODELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <p className="field-help">Mini prioriza velocidad y costo; 2.1 prioriza capacidad.</p>
              <FormError id="agent-realtime-model-error" message={errors.realtime_model?.message} />
            </div>
            <div className="field">
              <label htmlFor="agent-reasoning">Esfuerzo de razonamiento</label>
              <select id="agent-reasoning" value={sessionControls.reasoningEffort}
                disabled={!reasoningSupported}
                onChange={(event) => updateSessionControl("reasoningEffort", event.target.value as AgentSessionControls["reasoningEffort"])}>
                <option value="default">Predeterminado del modelo</option>
                <option value="minimal">Mínimo</option>
                <option value="low">Bajo</option>
                <option value="medium">Medio</option>
                <option value="high">Alto</option>
              </select>
              {!reasoningSupported && <p className="field-help">Requiere un modelo Realtime 2 o superior.</p>}
            </div>
          </div>
          <div className="field">
            <label htmlFor="agent-system-prompt">Instrucciones del sistema</label>
            <textarea id="agent-system-prompt" rows={6} maxLength={8000} aria-invalid={Boolean(errors.system_prompt)} {...register("system_prompt")} />
            <FormError id="agent-prompt-error" message={errors.system_prompt?.message} />
          </div>
        </section>

        <section className="agent-settings-section" aria-labelledby="agent-audio-heading">
          <div className="agent-settings-heading">
            <h3 id="agent-audio-heading">Voz y audio</h3>
            <p>Elegí cómo suena el agente y qué tipo de micrófono escucha.</p>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="agent-voice">Tipo de voz</label>
              <select id="agent-voice" {...register("voice")}>
                {VOICE_OPTIONS.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
              </select>
              <p className="field-help">OpenAI recomienda marin y cedar por su calidad.</p>
              <FormError id="agent-voice-error" message={errors.voice?.message} />
            </div>
            <div className="field">
              <label htmlFor="agent-speed">Velocidad de voz (0,25 a 1,5)</label>
              <input id="agent-speed" type="number" min={0.25} max={1.5} step={0.05}
                aria-invalid={Boolean(errors.voice_speed)} {...register("voice_speed", { valueAsNumber: true })} />
              <FormError id="agent-speed-error" message={errors.voice_speed?.message} />
            </div>
            <div className="field">
              <label htmlFor="agent-microphone">Entorno del micrófono</label>
              <select id="agent-microphone" value={sessionControls.microphoneEnvironment}
                onChange={(event) => updateSessionControl("microphoneEnvironment", event.target.value as AgentSessionControls["microphoneEnvironment"])}>
                <option value="off">Sin reducción de ruido</option>
                <option value="near_field">Micrófono cercano</option>
                <option value="far_field">Micrófono de ambiente</option>
              </select>
            </div>
          </div>
        </section>

        <section className="agent-settings-section" aria-labelledby="agent-conversation-heading">
          <div className="agent-settings-heading">
            <h3 id="agent-conversation-heading">Conversación</h3>
            <p>Controlá cuándo responde, cuánto habla y si puede ser interrumpido.</p>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="agent-turn-detection">Detección de turnos</label>
              <select id="agent-turn-detection" value={sessionControls.turnDetection}
                onChange={(event) => updateSessionControl("turnDetection", event.target.value as AgentSessionControls["turnDetection"])}>
                <option value="semantic_vad">Conversación natural</option>
                <option value="server_vad">Respuesta rápida</option>
                <option value="manual">Manual</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="agent-response-timing">Tiempo de respuesta</label>
              <select id="agent-response-timing" value={sessionControls.responseTiming}
                disabled={sessionControls.turnDetection !== "semantic_vad"}
                onChange={(event) => updateSessionControl("responseTiming", event.target.value as AgentSessionControls["responseTiming"])}>
                <option value="low">Paciente</option>
                <option value="medium">Equilibrado</option>
                <option value="high">Rápido</option>
                <option value="auto">Automático</option>
              </select>
              {sessionControls.turnDetection !== "semantic_vad" && <p className="field-help">Requiere Conversación natural en detección de turnos.</p>}
            </div>
            <div className="field">
              <label htmlFor="agent-response-length">Longitud de respuesta</label>
              <select id="agent-response-length" value={sessionControls.responseLength}
                onChange={(event) => updateSessionControl("responseLength", event.target.value as AgentSessionControls["responseLength"])}>
                <option value="short">Corta</option>
                <option value="normal">Normal</option>
                <option value="detailed">Detallada</option>
                <option value="unlimited">Sin límite específico</option>
              </select>
            </div>
          </div>
          {sessionControls.turnDetection !== "manual" && (
            <div className="checkbox-field">
              <input id="agent-interruptions" type="checkbox" checked={sessionControls.allowInterruptions}
                onChange={(event) => updateSessionControl("allowInterruptions", event.target.checked)} />
              <label htmlFor="agent-interruptions">Permitir interrupciones</label>
            </div>
          )}
        </section>

        <div className="checkbox-field">
          <input id="agent-enabled" type="checkbox" {...register("enabled")} />
          <label htmlFor="agent-enabled">Agente habilitado</label>
        </div>
      </fieldset>
      {canEdit && <div className="actions">
        <button type="submit" className="button button--primary" disabled={isSubmitting}>{isSubmitting ? "Guardando..." : "Guardar cambios"}</button>
        <button type="button" className="button button--secondary" disabled={isSubmitting} onClick={() => syncSaved(saved)}>Restaurar último guardado</button>
      </div>}
      {message && <p role="status" className="success-message">{message}</p>}
      <p className="muted">{saved.updated_at
        ? `Último guardado: ${new Date(saved.updated_at).toLocaleString("es-AR")}`
        : "Todavía no se guardó una configuración."}</p>
    </form>
  );
}
