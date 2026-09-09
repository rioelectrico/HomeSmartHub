export type MicrophoneEnvironment = "off" | "near_field" | "far_field";
export type TurnDetectionMode = "manual" | "server_vad" | "semantic_vad";
export type ResponseTiming = "low" | "medium" | "high" | "auto";
export type ResponseLength = "short" | "normal" | "detailed" | "unlimited";
export type ReasoningEffort = "default" | "minimal" | "low" | "medium" | "high";

export interface AgentSessionControls {
  microphoneEnvironment: MicrophoneEnvironment;
  turnDetection: TurnDetectionMode;
  responseTiming: ResponseTiming;
  allowInterruptions: boolean;
  responseLength: ResponseLength;
  reasoningEffort: ReasoningEffort;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function supportsRealtimeReasoning(model: string): boolean {
  return /^gpt-realtime-2(?:\.|$)/.test(model);
}

export function readAgentSessionControls(options: Record<string, unknown>): AgentSessionControls {
  const input = record(record(options.audio).input);
  const noiseReduction = record(input.noise_reduction);
  const turnDetection = record(input.turn_detection);
  const reasoning = record(options.reasoning);
  const microphoneEnvironment =
    noiseReduction.type === "near_field" || noiseReduction.type === "far_field"
      ? noiseReduction.type
      : "off";
  const turnDetectionMode =
    turnDetection.type === "semantic_vad" || turnDetection.type === "server_vad"
      ? turnDetection.type
      : input.turn_detection === null ? "manual" : "server_vad";
  const responseTiming =
    turnDetection.eagerness === "low" || turnDetection.eagerness === "medium" || turnDetection.eagerness === "high"
      ? turnDetection.eagerness
      : "auto";
  const responseLength = options.max_output_tokens === 150 ? "short"
    : options.max_output_tokens === 300 ? "normal"
      : options.max_output_tokens === 600 ? "detailed" : "unlimited";
  const effort = reasoning.effort;
  const reasoningEffort =
    effort === "minimal" || effort === "low" || effort === "medium" || effort === "high"
      ? effort
      : "default";

  return {
    microphoneEnvironment,
    turnDetection: turnDetectionMode,
    responseTiming,
    allowInterruptions: turnDetection.interrupt_response !== false,
    responseLength,
    reasoningEffort,
  };
}

export function buildAgentSessionOptions(
  persisted: Record<string, unknown>,
  controls: AgentSessionControls,
  model: string
): Record<string, unknown> {
  const result = structuredClone(persisted);
  const audio = record(result.audio);
  const input = record(audio.input);

  input.noise_reduction = controls.microphoneEnvironment === "off"
    ? null
    : { type: controls.microphoneEnvironment };
  if (controls.turnDetection === "manual") {
    input.turn_detection = null;
  } else if (controls.turnDetection === "semantic_vad") {
    input.turn_detection = {
      type: "semantic_vad",
      create_response: true,
      eagerness: controls.responseTiming,
      interrupt_response: controls.allowInterruptions,
    };
  } else {
    input.turn_detection = {
      type: "server_vad",
      create_response: true,
      interrupt_response: controls.allowInterruptions,
    };
  }
  audio.input = input;
  result.audio = audio;
  result.max_output_tokens = controls.responseLength === "short" ? 150
    : controls.responseLength === "normal" ? 300
      : controls.responseLength === "detailed" ? 600 : "inf";

  if (supportsRealtimeReasoning(model) && controls.reasoningEffort !== "default") {
    result.reasoning = { ...record(result.reasoning), effort: controls.reasoningEffort };
  } else {
    delete result.reasoning;
  }
  return result;
}
