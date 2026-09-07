import { uuidSchema } from "./contracts";

const HEADER_BYTES = 34;
const MAX_SEQUENCE = 18446744073709551615n;
export interface AudioFrame {
  streamId: string;
  sequence: bigint;
  direction: 1 | 2;
  payload: Uint8Array;
}

function validate(frame: AudioFrame): void {
  if (typeof frame.sequence !== "bigint" || frame.sequence < 1n || frame.sequence > MAX_SEQUENCE) throw new Error("INVALID_SEQUENCE");
  if (!uuidSchema.safeParse(frame.streamId).success || (frame.direction !== 1 && frame.direction !== 2) || !(frame.payload instanceof Uint8Array) || frame.payload.byteLength === 0 || frame.payload.byteLength > 960 || frame.payload.byteLength % 2 !== 0) throw new Error("INVALID_AUDIO_FRAME");
}

export function encodeAudioFrame(frame: AudioFrame): ArrayBuffer {
  validate(frame);
  const buffer = new ArrayBuffer(HEADER_BYTES + frame.payload.byteLength);
  const bytes = new Uint8Array(buffer);
  bytes.set([80, 65, 85, 68, 1, frame.direction]);
  const hex = frame.streamId.replaceAll("-", "");
  for (let i = 0; i < 16; i++) bytes[6 + i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  const view = new DataView(buffer);
  view.setBigUint64(22, frame.sequence, false);
  view.setUint32(30, frame.payload.byteLength, false);
  bytes.set(frame.payload, HEADER_BYTES);
  return buffer;
}

/** Output decoding requires the current stream and its last accepted sequence. */
export function decodeAudioFrame(buffer: ArrayBuffer, options: { streamId: string; lastSequence: bigint; expectedDirection?: 1 | 2 }): AudioFrame {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < HEADER_BYTES || buffer.byteLength > HEADER_BYTES + 960) throw new Error("INVALID_AUDIO_FRAME");
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  const direction = options.expectedDirection ?? 2;
  if (bytes[0] !== 80 || bytes[1] !== 65 || bytes[2] !== 85 || bytes[3] !== 68 || bytes[4] !== 1 || bytes[5] !== direction || view.getUint32(30, false) !== buffer.byteLength - HEADER_BYTES) throw new Error("INVALID_AUDIO_FRAME");
  const hex = Array.from(bytes.slice(6, 22), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const streamId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  const frame = { streamId, direction, sequence: view.getBigUint64(22, false), payload: bytes.slice(HEADER_BYTES) };
  validate(frame);
  if (!uuidSchema.safeParse(options.streamId).success || streamId !== options.streamId.toLowerCase()) throw new Error("STREAM_NOT_OWNED");
  if (typeof options.lastSequence !== "bigint" || options.lastSequence < 0n || options.lastSequence > MAX_SEQUENCE || frame.sequence <= options.lastSequence) throw new Error("INVALID_SEQUENCE");
  return frame;
}
