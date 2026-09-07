import { z } from "zod";

// UUID bytes have no mandated UUID version in the backend contract.
export const uuidSchema = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i).transform((value) => value.toLowerCase());
// Reject integers that JSON.parse cannot represent exactly. Never round a wire sequence.
const integer = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const timestamp = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/).refine((value) => Number.isFinite(Date.parse(value)));
const envelope = { version: z.literal(1), seq: integer };
const identity = { ...envelope, conversation_id: uuidSchema };
const commandPayload = z.record(z.string(), z.json()).refine((value) => new TextEncoder().encode(JSON.stringify(value)).byteLength <= 8192);
export const conversationStates = ["preparing", "listening", "visitor_speaking", "assistant_speaking", "error"] as const;
export const conversationErrorCodes = ["AI_UNCONFIGURED", "AI_UNAVAILABLE", "CONVERSATION_ALREADY_ACTIVE", "CONVERSATION_NOT_ACTIVE", "INVALID_AUDIO_FRAME", "AUDIO_INPUT_OVERFLOW", "AUDIO_OUTPUT_OVERFLOW", "STREAM_NOT_FOUND", "STREAM_NOT_OWNED", "INVALID_SEQUENCE", "CONVERSATION_TIMEOUT", "CONVERSATION_IDLE_TIMEOUT", "DEVICE_DISCONNECTED"] as const;
const serverSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("auth.challenge"), algorithm: z.literal("HMAC-SHA256"), nonce: z.string().min(32).max(128).regex(/^[A-Za-z0-9_-]+$/), expires_at: timestamp }),
  z.strictObject({ type: z.literal("auth.ok"), upload_token: z.string().min(1), upload_expires_at: timestamp, heartbeat_interval_seconds: integer.min(1).max(2147483) }),
  z.strictObject({ type: z.literal("command.request"), command_id: uuidSchema, command: z.enum(["device.status.request", "camera.capture"]), payload: commandPayload.default({}) }),
  z.strictObject({ type: z.literal("conversation.started"), ...identity, stream_id: uuidSchema, audio: z.strictObject({ codec: z.literal("pcm16"), sample_rate: z.literal(24000), channels: z.literal(1), frame_ms: z.literal(20) }), max_seconds: integer.min(1) }).refine((value) => value.conversation_id !== value.stream_id),
  z.strictObject({ type: z.literal("conversation.state"), ...identity, state: z.enum(conversationStates) }),
  z.strictObject({ type: z.literal("conversation.transcript"), ...identity, role: z.enum(["user", "assistant"]), text: z.string().min(1).max(8000), final: z.literal(true) }),
  z.strictObject({ type: z.literal("conversation.audio.clear"), ...identity, stream_id: uuidSchema, reason: z.enum(["barge_in", "audio_output_overflow"]) }).refine((value) => value.conversation_id !== value.stream_id),
  z.strictObject({ type: z.literal("conversation.ended"), ...identity, outcome: z.enum(["registered", "declined", "abandoned", "failed"]), reason: z.enum(["visitor_finished", "device_disconnected", "conversation_timeout", "conversation_idle_timeout", "audio_input_overflow", "audio_output_overflow"]) }),
  z.strictObject({ type: z.literal("conversation.error"), ...envelope, code: z.enum(conversationErrorCodes), correlation_id: uuidSchema }),
]);

export type ServerMessage = z.infer<typeof serverSchema>;
export type CommandRequestMessage = Extract<ServerMessage, { type: "command.request" }>;
export type TranscriptMessage = Extract<ServerMessage, { type: "conversation.transcript" }>;
export type EndedMessage = Extract<ServerMessage, { type: "conversation.ended" }>;
export type AudioClearMessage = Extract<ServerMessage, { type: "conversation.audio.clear" }>;
export interface AudioOutputOverflowMessage {
  type: "conversation.audio.output_overflow";
  version: 1;
  conversation_id: string;
  stream_id: string;
}
export type ClientState = "disconnected" | "connecting" | "authenticating" | "ready" | "starting" | "stopping" | typeof conversationStates[number];
export type ClientErrorCode = typeof conversationErrorCodes[number] | "PROTOCOL_ERROR" | "AUTH_FAILED" | "CONNECTION_ERROR" | "CONNECTION_REPLACED" | "PROTOCOL_VERSION_UNSUPPORTED" | "MESSAGE_TOO_LARGE" | "HANDSHAKE_TIMEOUT";
export interface DeviceClientCallbacks {
  onState?: (state: ClientState) => void;
  onTranscript?: (message: TranscriptMessage) => void;
  onAudio?: (pcm16: Int16Array) => void;
  onAudioClear?: (message: AudioClearMessage) => void;
  onEnded?: (message: EndedMessage) => void;
  onError?: (code: ClientErrorCode) => void;
}

export function parseServerMessage(text: string): ServerMessage {
  try {
    if (typeof text !== "string" || text.length > 65536 || new TextEncoder().encode(text).byteLength > 65536) throw new Error();
    return serverSchema.parse(JSON.parse(text));
  } catch {
    // Never expose a rejected payload, Zod issue or credential-bearing server error.
    throw new Error("PROTOCOL_ERROR");
  }
}
