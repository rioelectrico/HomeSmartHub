import { createHmac, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { decodeAudioFrame, encodeAudioFrame } from "@/lib/simulator/audio-frame";
import { floatToPcm16, resampleTo24k } from "@/lib/simulator/audio-codec";
import { parseServerMessage } from "@/lib/simulator/contracts";
import { PorteroDeviceClient } from "@/lib/simulator/device-client";

const conversationId = "223e4567-e89b-12d3-a456-426614174000";
const streamId = "123e4567-e89b-12d3-a456-426614174000";
const nonce = "n".repeat(32);
const secret = "test-only-secret-01234567890123456789";
const challenge = { type: "auth.challenge", algorithm: "HMAC-SHA256", nonce, expires_at: "2099-01-01T00:00:00+00:00" };
const authOk = { type: "auth.ok", upload_token: "unused-upload-token", upload_expires_at: "2099-01-01T00:00:00+00:00", heartbeat_interval_seconds: 10 };
const started = { type: "conversation.started", version: 1, seq: 1, conversation_id: conversationId, stream_id: streamId, audio: { codec: "pcm16", sample_rate: 24000, channels: 1, frame_ms: 20 }, max_seconds: 180 };
const statusRequest = { type: "command.request", command_id: "423e4567-e89b-12d3-a456-426614174000", command: "device.status.request", payload: {} };

class Socket {
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "blob";
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => unknown) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { Socket.instances.push(this); }
  send(data: string | ArrayBuffer) { if (this.readyState !== 1) throw new Error("closed"); this.sent.push(data); }
  close(code = 1000, reason = "") { this.readyState = 3; this.onclose?.({ code, reason }); }
  open() { this.readyState = 1; this.onopen?.(); }
  async receive(data: unknown) { await this.onmessage?.({ data: typeof data === "object" && !(data instanceof ArrayBuffer) ? JSON.stringify(data) : data }); }
  json() { return this.sent.filter((item): item is string => typeof item === "string").map((item) => JSON.parse(item)); }
}

function setup() {
  const callbacks = { onState: vi.fn(), onTranscript: vi.fn(), onAudio: vi.fn(), onAudioClear: vi.fn(), onEnded: vi.fn(), onError: vi.fn() };
  const client = new PorteroDeviceClient(callbacks);
  client.connect({ deviceId: "PI-000001", secret });
  const socket = Socket.instances.at(-1)!;
  socket.open();
  return { client, socket, callbacks };
}

async function authenticated() {
  const result = setup();
  await result.socket.receive(challenge);
  await result.socket.receive(authOk);
  return result;
}

beforeEach(() => {
  Socket.instances = [];
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("crypto", webcrypto);
  vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", "ws://localhost:8000/ws/device");
});
afterEach(() => { for (const socket of Socket.instances) socket.close(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("PAUD and PCM codecs", () => {
  it("writes the exact network-order header and preserves little-endian PCM bytes", () => {
    const wire = encodeAudioFrame({ streamId, sequence: 1n, direction: 1, payload: new Uint8Array([0, 128, 255, 127]) });
    expect(Buffer.from(wire).toString("hex")).toBe("504155440101123e4567e89b12d3a4564266141740000000000000000001000000040080ff7f");
    const decoded = decodeAudioFrame(wire, { expectedDirection: 1, streamId, lastSequence: 0n });
    expect(decoded).toEqual({ streamId, sequence: 1n, direction: 1, payload: new Uint8Array([0, 128, 255, 127]) });
    expect(decodeAudioFrame(encodeAudioFrame({ ...decoded, direction: 2, sequence: 18446744073709551615n }), { streamId, lastSequence: 0n }).sequence).toBe(18446744073709551615n);
  });
  it("rejects invalid directions, IDs, lengths, sequence overflow and stale output", () => {
    const frame = { streamId, sequence: 1n, direction: 2 as const, payload: new Uint8Array([0, 0]) };
    for (const sequence of [0n, -1n, 18446744073709551616n]) expect(() => encodeAudioFrame({ ...frame, sequence })).toThrow();
    for (const length of [0, 1, 962]) expect(() => encodeAudioFrame({ ...frame, payload: new Uint8Array(length) })).toThrow();
    expect(() => encodeAudioFrame({ ...frame, streamId: "bad" })).toThrow();
    const valid = encodeAudioFrame(frame);
    for (const [offset, value] of [[0, 0], [4, 2], [5, 1], [33, 4]]) {
      const invalid = valid.slice(0); new DataView(invalid).setUint8(offset, value);
      expect(() => decodeAudioFrame(invalid, { streamId, lastSequence: 0n })).toThrow();
    }
    expect(() => decodeAudioFrame(valid, { streamId: conversationId, lastSequence: 0n })).toThrow();
    expect(() => decodeAudioFrame(valid, { streamId, lastSequence: 1n })).toThrow();
    expect(() => decodeAudioFrame(valid.slice(0, 20), { streamId, lastSequence: 0n })).toThrow();
  });
  it("resamples deterministically and saturates PCM16 including non-finite input", () => {
    expect(Array.from(resampleTo24k(new Float32Array([0, 1, 0, -1]), 48000))).toEqual([0, 0]);
    expect(Array.from(resampleTo24k(new Float32Array([0, 1]), 12000))).toEqual([0, 0.5, 1, 1]);
    expect(Array.from(floatToPcm16(new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2, NaN, Infinity, -Infinity])))).toEqual([-32768, -32768, -16384, 0, 16384, 32767, 32767, 0, 32767, -32768]);
    expect(resampleTo24k(new Float32Array(), 44100)).toHaveLength(0);
    for (const rate of [0, -1, NaN, Infinity]) expect(() => resampleTo24k(new Float32Array([1]), rate)).toThrow();
    expect(Array.from(resampleTo24k(new Float32Array([NaN, Infinity]), 24000)).every(Number.isFinite)).toBe(true);
  });
});

describe("strict server controls", () => {
  it("accepts legacy command envelopes without conversation sequencing and bounds their payload", () => {
    expect(parseServerMessage(JSON.stringify(statusRequest))).toEqual(statusRequest);
    expect(parseServerMessage(JSON.stringify({ ...statusRequest, payload: { text: "é".repeat(4090) + "a" } }))).toMatchObject({ type: "command.request" });
  });
  it.each([
    { ...statusRequest, command: "access.unlock" },
    { ...statusRequest, command_id: "bad-id" },
    { ...statusRequest, seq: 5 },
    { ...statusRequest, version: 1 },
    { ...statusRequest, boot_id: "boot" },
    { ...statusRequest, payload: [] },
    { ...statusRequest, payload: null },
    { ...statusRequest, payload: { text: "é".repeat(4091) } },
  ])("rejects malformed or unsupported legacy commands: %j", (message) => {
    expect(() => parseServerMessage(JSON.stringify(message))).toThrow("PROTOCOL_ERROR");
  });
  it.each([
    { ...started, version: "1" }, { ...started, seq: true }, { ...started, seq: -1 }, { ...started, seq: 1.5 }, { ...started, seq: Number.MAX_SAFE_INTEGER + 1 },
    { ...started, conversation_id: "bad" }, { ...started, stream_id: conversationId }, { ...started, extra: true },
    { ...started, stream_id: `${streamId}\n` },
    { ...started, audio: { ...started.audio, channels: "1" } }, { ...started, max_seconds: 0 },
    { type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "unknown" },
    { type: "conversation.error", version: 1, seq: 2, code: "UNKNOWN", correlation_id: streamId },
    { type: "conversation.transcript", version: 1, seq: 2, conversation_id: conversationId, role: "user", text: "hi", final: 1 },
    { ...challenge, nonce: "bad" }, { ...authOk, heartbeat_interval_seconds: "10" },
    { ...challenge, nonce: `${nonce}\n` },
  ])("rejects malformed controls before they can reach consumers: %j", (message) => {
    expect(() => parseServerMessage(JSON.stringify(message))).toThrow("PROTOCOL_ERROR");
  });
});

describe("browser device lifecycle", () => {
  it("reports local playback overflow once, suppresses late audio and retains authenticated retry", async () => {
    vi.useFakeTimers();
    const { client, socket, callbacks } = await authenticated();
    client.startConversation(); await socket.receive(started);
    client.reportAudioOutputOverflow(conversationId, streamId);
    client.reportAudioOutputOverflow(conversationId, streamId);
    expect(socket.json().filter((m) => m.type === "conversation.audio.output_overflow")).toEqual([
      { type: "conversation.audio.output_overflow", version: 1, conversation_id: conversationId, stream_id: streamId, boot_id: socket.json()[0].boot_id, seq: 5 },
    ]);
    expect(client.state).toBe("stopping");
    expect(callbacks.onError).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
    expect(() => client.sendAudio(new Int16Array(480))).toThrow("INVALID_STATE");
    await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([30, 0]) }));
    await socket.receive({ type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "listening" });
    await socket.receive({ type: "conversation.error", version: 1, seq: 3, code: "AUDIO_OUTPUT_OVERFLOW", correlation_id: streamId });
    await socket.receive({ type: "conversation.audio.clear", version: 1, seq: 4, conversation_id: conversationId, stream_id: streamId, reason: "audio_output_overflow" });
    expect(callbacks.onAudio).not.toHaveBeenCalled();
    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    expect(client.state).toBe("stopping");
    vi.advanceTimersByTime(10000);
    expect(socket.json().at(-1)).toMatchObject({ type: "device.heartbeat", seq: 6 });
    await socket.receive({ type: "conversation.ended", version: 1, seq: 5, conversation_id: conversationId, outcome: "failed", reason: "audio_output_overflow" });
    expect(client.state).toBe("ready");
    expect(socket.readyState).toBe(1);
    client.startConversation();
    const nextConversation = "523e4567-e89b-12d3-a456-426614174000";
    const nextStream = "623e4567-e89b-12d3-a456-426614174000";
    await socket.receive({ ...started, seq: 6, conversation_id: nextConversation, stream_id: nextStream });
    client.reportAudioOutputOverflow(conversationId, streamId); // stale audio run
    expect(client.state).toBe("preparing");
    expect(socket.json().filter((m) => m.type === "conversation.audio.output_overflow")).toHaveLength(1);
    await socket.receive(encodeAudioFrame({ streamId: nextStream, direction: 2, sequence: 1n, payload: new Uint8Array([40, 0]) }));
    expect(callbacks.onAudio).toHaveBeenCalledExactlyOnceWith(new Int16Array([40]));
  });

  it("ignores a late local overflow after manual stop or ended", async () => {
    const { client, socket, callbacks } = await authenticated();
    client.startConversation(); await socket.receive(started);
    client.stopConversation();
    client.reportAudioOutputOverflow(conversationId, streamId);
    await socket.receive({ type: "conversation.ended", version: 1, seq: 2, conversation_id: conversationId, outcome: "registered", reason: "visitor_finished" });
    client.reportAudioOutputOverflow(conversationId, streamId);
    expect(socket.json().filter((m) => m.type === "conversation.audio.output_overflow")).toHaveLength(0);
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(client.state).toBe("ready");
  });
  it.each([false, true])("completes a status request with valid telemetry while visiting=%s", async (visiting) => {
    vi.useFakeTimers();
    const { client, socket, callbacks } = await authenticated();
    if (visiting) {
      client.startConversation();
      await socket.receive(started);
      await socket.receive({ type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "assistant_speaking" });
      client.sendAudio(new Int16Array([10]));
    }
    const before = socket.json().length;
    const lastSequence = socket.json().at(-1).seq;
    const bootId = socket.json()[0].boot_id;
    callbacks.onState.mockClear();
    await socket.receive(statusRequest);
    expect(socket.json().slice(before)).toEqual([
      { type: "command.ack", command_id: statusRequest.command_id, status: "accepted", boot_id: bootId, seq: lastSequence + 1 },
      { type: "device.status", firmware_version: "simulator", hardware_model: "simulator", uptime_seconds: expect.any(Number), ethernet: "online", camera: "unavailable", microphone: "ready", speaker: "ready", free_heap_bytes: 0, boot_id: bootId, seq: lastSequence + 2 },
      { type: "command.result", command_id: statusRequest.command_id, status: "completed", result: {}, boot_id: bootId, seq: lastSequence + 3 },
    ]);
    expect(callbacks.onState).not.toHaveBeenCalled();
    expect(client.state).toBe(visiting ? "assistant_speaking" : "ready");
    vi.advanceTimersByTime(10000);
    expect(socket.json().at(-1)).toMatchObject({ type: "device.heartbeat", boot_id: bootId, seq: lastSequence + 4 });
    if (visiting) {
      expect(client.conversationId).toBe(conversationId);
      expect(client.streamId).toBe(streamId);
      client.sendAudio(new Int16Array([20]));
      const frames = socket.sent.filter((frame): frame is ArrayBuffer => frame instanceof ArrayBuffer);
      expect(frames.map((frame) => new DataView(frame).getBigUint64(22))).toEqual([1n, 2n]);
      await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([30, 0]) }));
      expect(callbacks.onAudio).toHaveBeenCalledWith(new Int16Array([30]));
      await socket.receive({ type: "conversation.state", version: 1, seq: 3, conversation_id: conversationId, state: "listening" });
      expect(client.state).toBe("listening");
      // A legacy command cannot reset the monotonically increasing server controls.
      await socket.receive({ type: "conversation.state", version: 1, seq: 3, conversation_id: conversationId, state: "visitor_speaking" });
      expect(callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
    } else {
      expect(socket.readyState).toBe(1);
      expect(callbacks.onError).not.toHaveBeenCalled();
    }
  });
  it("rejects unavailable camera with one V1 ACK while voice and heartbeat continue", async () => {
    vi.useFakeTimers();
    const { client, socket, callbacks } = await authenticated();
    client.startConversation(); await socket.receive(started);
    const before = socket.json().length;
    const getUserMedia = vi.fn();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    await socket.receive({ ...statusRequest, command: "camera.capture" });
    expect(socket.json().slice(before)).toEqual([
      { type: "command.ack", command_id: statusRequest.command_id, status: "rejected", error_code: "CAMERA_NOT_READY", boot_id: socket.json()[0].boot_id, seq: 5 },
    ]);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(client.conversationId).toBe(conversationId);
    vi.advanceTimersByTime(10000);
    expect(socket.json().at(-1)).toMatchObject({ type: "device.heartbeat", seq: 6 });
    client.sendAudio(new Int16Array([17]));
    expect(socket.sent.at(-1)).toBeInstanceOf(ArrayBuffer);
    await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([20, 0]) }));
    expect(callbacks.onAudio).toHaveBeenCalledWith(new Int16Array([20]));
    await socket.receive({ type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "listening" });
    expect(client.state).toBe("listening");
    expect(socket.readyState).toBe(1);
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onEnded).not.toHaveBeenCalled();
  });
  it.each([statusRequest, { ...statusRequest, command: "camera.capture" }])("rejects legacy commands before authentication: %j", async (command) => {
    const { client, socket, callbacks } = setup();
    await socket.receive(command);
    expect(socket.json()).toHaveLength(1);
    expect(client.state).toBe("disconnected");
    expect(callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
  });
  it("fails safely when a status ACK cannot be sent and does not send the result on a successor", async () => {
    const { client, socket, callbacks } = await authenticated();
    const before = socket.json().length;
    const send = socket.send.bind(socket);
    socket.send = (data) => {
      if (typeof data === "string" && JSON.parse(data).type === "command.ack") throw new Error("send failed");
      send(data);
    };
    callbacks.onError.mockImplementation(() => { client.connect({ deviceId: "PI-000001", secret }); Socket.instances.at(-1)!.open(); });
    await socket.receive(statusRequest);
    expect(socket.json()).toHaveLength(before);
    const successor = Socket.instances.at(-1)!;
    expect(successor).not.toBe(socket);
    expect(successor.json().map((message) => message.type)).toEqual(["device.hello"]);
    expect(callbacks.onError).toHaveBeenCalledWith("CONNECTION_ERROR");
  });
  it("becomes listening from the ordered ready controls without waiting for speech", async () => {
    const { client, socket, callbacks } = await authenticated();
    client.startConversation();
    expect(client.state).toBe("starting");
    await socket.receive(started);
    expect(client.state).toBe("preparing");
    await socket.receive({ type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "listening" });
    expect(client.state).toBe("listening");
    expect(callbacks.onState.mock.calls.slice(-3).map(([state]) => state)).toEqual(["starting", "preparing", "listening"]);
    expect(callbacks.onError).not.toHaveBeenCalled();
  });
  it("authenticates with real HMAC, sends one status and keeps boot identity across clients", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(document, "cookie", "set");
    const history = vi.spyOn(window.history, "replaceState");
    const { client, socket } = await authenticated();
    const [hello, auth, status] = socket.json();
    expect(socket.binaryType).toBe("arraybuffer");
    expect(hello).toMatchObject({ type: "device.hello", version: "v1", device_id: "PI-000001", capabilities: ["audio_pcm16_v1"], seq: 1 });
    expect(hello.boot_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(auth).toEqual({ type: "auth.response", boot_id: hello.boot_id, seq: 2, nonce, digest: createHmac("sha256", secret).update(`v1\nPI-000001\n${hello.boot_id}\n${nonce}`).digest("hex") });
    expect(status).toEqual({ type: "device.status", boot_id: hello.boot_id, seq: 3, firmware_version: "simulator", hardware_model: "simulator", uptime_seconds: expect.any(Number), ethernet: "online", camera: "unavailable", microphone: "ready", speaker: "ready", free_heap_bytes: 0 });
    expect(client.state).toBe("ready");
    expect(JSON.stringify(client)).not.toContain(secret);
    expect(JSON.stringify(socket.sent)).not.toContain(secret);
    expect(socket.url).toBe("ws://localhost:8000/ws/device");
    expect(storage).not.toHaveBeenCalled(); expect(cookie).not.toHaveBeenCalled(); expect(history).not.toHaveBeenCalled();
    client.disconnect();
    const next = setup(); expect(next.socket.json()[0].boot_id).toBe(hello.boot_id);
  });
  it("keeps JSON and PAUD counters separate, validates active IDs and stops only once", async () => {
    const { client, socket, callbacks } = await authenticated();
    client.startConversation();
    expect(socket.json().at(-1)).toMatchObject({ type: "conversation.start", version: 1, seq: 4, mode: "hands_free" });
    expect(() => client.startConversation()).toThrow("INVALID_STATE");
    await socket.receive(started);
    client.sendAudio(new Int16Array([-32768, 32767]));
    const audio = socket.sent.find((item): item is ArrayBuffer => item instanceof ArrayBuffer)!;
    expect(Buffer.from(audio).toString("hex")).toBe("504155440101123e4567e89b12d3a4564266141740000000000000000001000000040080ff7f");
    await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([0, 128]) }));
    expect(callbacks.onAudio).toHaveBeenCalledWith(new Int16Array([-32768]));
    await socket.receive({ type: "conversation.transcript", version: 1, seq: 2, conversation_id: conversationId, role: "assistant", text: "Hola", final: true });
    expect(callbacks.onTranscript).toHaveBeenCalledOnce();
    client.stopConversation();
    expect(socket.json().at(-1)).toMatchObject({ type: "conversation.stop", seq: 5, conversation_id: conversationId, reason: "visitor_finished" });
    expect(() => client.stopConversation()).toThrow("INVALID_STATE");
    expect(() => client.sendAudio(new Int16Array([0]))).toThrow("INVALID_STATE");
    await socket.receive({ type: "conversation.ended", version: 1, seq: 3, conversation_id: conversationId, outcome: "abandoned", reason: "visitor_finished" });
    expect(callbacks.onEnded).toHaveBeenCalledOnce(); expect(client.state).toBe("ready");
  });
  it.each(["close", "error", "malformed", "disconnect"])("clears heartbeat and blocks late events on %s", async (ending) => {
    const { client, socket, callbacks } = await authenticated();
    // Swap timers before creating the interval in a fresh connection.
    client.disconnect(); vi.useFakeTimers();
    client.connect({ deviceId: "PI-000001", secret });
    const active = Socket.instances.at(-1)!; active.open(); await active.receive(challenge); await active.receive(authOk);
    vi.advanceTimersByTime(10000);
    expect(active.json().at(-1)).toMatchObject({ type: "device.heartbeat", seq: 4 });
    const late = active.onmessage;
    if (ending === "close") active.close();
    if (ending === "error") active.onerror?.();
    if (ending === "malformed") await active.receive({ ...started, version: 2 });
    if (ending === "disconnect") client.disconnect();
    const count = active.sent.length;
    vi.advanceTimersByTime(60000); await late?.({ data: JSON.stringify(authOk) });
    expect(active.sent).toHaveLength(count); expect(vi.getTimerCount()).toBe(0);
    expect(() => client.startConversation()).toThrow("INVALID_STATE");
    expect(callbacks.onTranscript).not.toHaveBeenCalled(); expect(socket.readyState).toBe(3);
  });
  it("rejects stale sequence and foreign stream without dispatching payload callbacks", async () => {
    const first = await authenticated(); first.client.startConversation(); await first.socket.receive(started);
    await first.socket.receive({ type: "conversation.state", version: 1, seq: 1, conversation_id: conversationId, state: "listening" });
    expect(first.callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
    const second = await authenticated(); second.client.startConversation(); await second.socket.receive(started);
    await second.socket.receive(encodeAudioFrame({ streamId: conversationId, direction: 2, sequence: 1n, payload: new Uint8Array([0, 0]) }));
    expect(second.callbacks.onAudio).not.toHaveBeenCalled(); expect(second.callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
  });
  it("drops signing results after disconnect and sanitizes crypto failures", async () => {
    let release!: (value: ArrayBuffer) => void;
    vi.spyOn(webcrypto.subtle, "sign").mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const { client, socket } = setup(); const signing = socket.receive(challenge);
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    client.disconnect(); release(new ArrayBuffer(32)); await signing;
    expect(socket.json()).toHaveLength(1);
    vi.mocked(webcrypto.subtle.sign).mockRejectedValue(new Error(secret));
    const next = setup(); await next.socket.receive(challenge);
    expect(next.callbacks.onError).toHaveBeenCalledWith("AUTH_FAILED");
    expect(JSON.stringify(next.callbacks.onError.mock.calls)).not.toContain(secret);
  });
  it("requires Web Crypto and an explicit production URL", () => {
    vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", "");
    expect(() => new PorteroDeviceClient().connect({ deviceId: "PI-000001", secret })).toThrow("NEXT_PUBLIC_DEVICE_WS_URL");
    vi.stubEnv("NODE_ENV", "development"); vi.stubGlobal("crypto", {});
    expect(() => new PorteroDeviceClient().connect({ deviceId: "PI-000001", secret })).toThrow("WEB_CRYPTO_UNAVAILABLE");
    expect(Socket.instances).toHaveLength(0);
  });
  it("uses the development default and never places credentials in a URL", () => {
    vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", "");
    const result = setup(); expect(result.socket.url).toBe("ws://localhost:8000/ws/device"); result.client.disconnect();
    for (const url of ["https://localhost/ws/device", "ws://user:password@localhost/ws/device", "ws://localhost/ws/device?secret=value", "ws://localhost/ws/device#value"]) {
      vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", url);
      expect(() => new PorteroDeviceClient().connect({ deviceId: "PI-000001", secret })).toThrow("NEXT_PUBLIC_DEVICE_WS_URL");
    }
    expect(Socket.instances).toHaveLength(1);
  });
  it.each([
    { type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "unknown" },
    { type: "conversation.transcript", version: 1, seq: 2, conversation_id: conversationId, role: "assistant", text: "bad", final: false },
    { type: "conversation.transcript", version: 1, seq: 2, conversation_id: streamId, role: "user", text: "foreign", final: true },
    { type: "conversation.audio.clear", version: 1, seq: 2, conversation_id: conversationId, stream_id: streamId, reason: "unknown" },
    { type: "conversation.audio.clear", version: 1, seq: 2, conversation_id: conversationId, stream_id: "323e4567-e89b-12d3-a456-426614174000", reason: "barge_in" },
    { type: "conversation.ended", version: 1, seq: 2, conversation_id: conversationId, outcome: "unknown", reason: "visitor_finished" },
    { type: "conversation.error", version: 1, seq: 2, code: "AI_UNAVAILABLE", correlation_id: "bad" },
    { type: "conversation.error", version: 1, seq: 2, code: "AI_UNAVAILABLE", correlation_id: streamId, detail: secret },
    authOk, "invalid JSON",
  ])("closes safely on invalid active messages without payload callbacks: %j", async (invalid) => {
    const { client, socket, callbacks } = await authenticated(); client.startConversation(); await socket.receive(started);
    callbacks.onState.mockClear(); await socket.receive(invalid);
    expect(callbacks.onState.mock.calls).toEqual([["disconnected"]]);
    expect(callbacks.onError.mock.calls).toEqual([["PROTOCOL_ERROR"]]);
    expect(callbacks.onTranscript).not.toHaveBeenCalled(); expect(callbacks.onAudioClear).not.toHaveBeenCalled(); expect(callbacks.onEnded).not.toHaveBeenCalled();
    expect(client.conversationId).toBeUndefined(); expect(client.streamId).toBeUndefined(); expect(socket.readyState).toBe(3);
  });
  it("dispatches valid state and clear controls while preserving output sequence across barge-in", async () => {
    const { client, socket, callbacks } = await authenticated(); client.startConversation(); await socket.receive(started);
    await socket.receive({ type: "conversation.state", version: 1, seq: 2, conversation_id: conversationId, state: "assistant_speaking" });
    expect(client.state).toBe("assistant_speaking");
    const frame = encodeAudioFrame({ streamId, direction: 2, sequence: 7n, payload: new Uint8Array([1, 0]) });
    await socket.receive(frame);
    const clear = { type: "conversation.audio.clear", version: 1, seq: 3, conversation_id: conversationId, stream_id: streamId, reason: "barge_in" };
    await socket.receive(clear); expect(callbacks.onAudioClear).toHaveBeenCalledWith(clear);
    await socket.receive(frame); expect(callbacks.onAudio).toHaveBeenCalledOnce(); expect(callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
  });
  it("allows retry after a safe start error and resets only audio counters for a new conversation", async () => {
    const { client, socket, callbacks } = await authenticated();
    client.startConversation();
    await socket.receive({ type: "conversation.error", version: 1, seq: 0, code: "AI_UNAVAILABLE", correlation_id: streamId });
    expect(client.state).toBe("ready"); expect(callbacks.onError).toHaveBeenCalledWith("AI_UNAVAILABLE");
    client.startConversation(); await socket.receive(started); client.sendAudio(new Int16Array([1]));
    await socket.receive({ type: "conversation.ended", version: 1, seq: 2, conversation_id: conversationId, outcome: "failed", reason: "audio_input_overflow" });
    client.startConversation(); await socket.receive({ ...started, seq: 3 }); client.sendAudio(new Int16Array([2]));
    const frames = socket.sent.filter((item): item is ArrayBuffer => item instanceof ArrayBuffer);
    expect(frames.map((frame) => new DataView(frame).getBigUint64(22, false))).toEqual([1n, 1n]);
    expect(socket.json().map((message) => message.seq)).toEqual([1, 2, 3, 4, 5, 6]);
  });
  it("rejects auth.ok before signing and ignores an old connection after reconnect", async () => {
    const { client, socket, callbacks } = setup();
    const oldMessage = socket.onmessage;
    await socket.receive(authOk); expect(callbacks.onError).toHaveBeenCalledWith("PROTOCOL_ERROR");
    client.connect({ deviceId: "PI-000001", secret }); const current = Socket.instances.at(-1)!; current.open();
    await oldMessage?.({ data: JSON.stringify(challenge) }); expect(current.json()).toHaveLength(1);
    await current.receive(challenge); await current.receive(authOk); expect(client.state).toBe("ready");
    await current.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([0, 0]) }));
    expect(callbacks.onAudio).not.toHaveBeenCalled(); expect(client.state).toBe("disconnected");
  });
  it("cleans the connection if transport send fails without leaking the transport error", async () => {
    const { client, socket, callbacks } = await authenticated();
    vi.spyOn(socket, "send").mockImplementation(() => { throw new Error(secret); });
    client.startConversation(); expect(client.state).toBe("disconnected"); expect(callbacks.onError.mock.calls).toEqual([["CONNECTION_ERROR"]]);
    expect(socket.onmessage).toBeNull(); expect(socket.onopen).toBeNull();
  });
  it("contains every consumer callback exception and still sends start/stop", async () => {
    const { client, socket, callbacks } = await authenticated();
    for (const callback of Object.values(callbacks)) callback.mockImplementation(() => { throw new Error(secret); });
    expect(() => client.startConversation()).not.toThrow();
    expect(client.state).toBe("starting"); expect(socket.json().at(-1).type).toBe("conversation.start");
    await socket.receive(started); expect(client.state).toBe("preparing");
    await socket.receive({ type: "conversation.transcript", version: 1, seq: 2, conversation_id: conversationId, role: "user", text: "hi", final: true });
    await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([0, 0]) }));
    await socket.receive({ type: "conversation.audio.clear", version: 1, seq: 3, conversation_id: conversationId, stream_id: streamId, reason: "barge_in" });
    await socket.receive({ type: "conversation.error", version: 1, seq: 4, code: "INVALID_AUDIO_FRAME", correlation_id: streamId });
    expect(() => client.stopConversation()).not.toThrow();
    expect(client.state).toBe("stopping"); expect(socket.json().at(-1).type).toBe("conversation.stop");
    await socket.receive({ type: "conversation.ended", version: 1, seq: 5, conversation_id: conversationId, outcome: "abandoned", reason: "visitor_finished" });
    expect(client.state).toBe("ready"); expect(socket.readyState).toBe(1);
    for (const callback of Object.values(callbacks)) expect(callback).toHaveBeenCalled();
    expect(callbacks.onError.mock.calls).toEqual([["INVALID_AUDIO_FRAME"]]);
    expect(() => client.disconnect()).not.toThrow(); expect(client.state).toBe("disconnected");
  });
  it("cannot close a new connection when an old audio consumer reconnects and throws", async () => {
    const { client, socket, callbacks } = await authenticated(); client.startConversation(); await socket.receive(started);
    callbacks.onAudio.mockImplementation(() => {
      client.disconnect(); client.connect({ deviceId: "PI-000001", secret });
      throw new Error(secret);
    });
    await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: 1n, payload: new Uint8Array([0, 0]) }));
    const next = Socket.instances.at(-1)!;
    expect(next).not.toBe(socket); expect(next.readyState).toBe(0); expect(client.state).toBe("connecting");
    next.open(); await next.receive(challenge); await next.receive(authOk);
    expect(client.state).toBe("ready"); expect(callbacks.onError).not.toHaveBeenCalled();
  });
  it.each([
    [4000, "PROTOCOL_ERROR"], [4001, "CONNECTION_REPLACED"], [4002, "AUTH_FAILED"],
    [4003, "PROTOCOL_VERSION_UNSUPPORTED"], [4004, "MESSAGE_TOO_LARGE"], [4005, "INVALID_SEQUENCE"],
    [4006, "HANDSHAKE_TIMEOUT"], [4500, "CONNECTION_ERROR"], [1006, "CONNECTION_ERROR"], [4999, "CONNECTION_ERROR"],
  ])("reports safe code for close %s without exposing the reason", async (code, expected) => {
    const { client, socket, callbacks } = setup(); await socket.receive(challenge);
    expect(() => socket.close(code as number, secret)).not.toThrow();
    expect(callbacks.onError.mock.calls).toEqual([[expected]]); expect(client.state).toBe("disconnected");
    expect(socket.onmessage).toBeNull(); expect(JSON.stringify(callbacks.onError.mock.calls)).not.toContain(secret);
  });
  it("does not repeat local errors when a queued close callback arrives", async () => {
    const { client, socket, callbacks } = await authenticated(); const queuedClose = socket.onclose;
    callbacks.onError.mockImplementation(() => { throw new Error(secret); });
    await socket.receive("bad JSON");
    expect(() => queuedClose?.({ code: 4000, reason: secret })).not.toThrow();
    expect(callbacks.onError.mock.calls).toEqual([["PROTOCOL_ERROR"]]); expect(client.state).toBe("disconnected");
  });
  it.each([100 * 1024 * 1024, 50 * 994 - 993])("bounds pending audio at %s bytes and clears heartbeat without late writes", async (pending) => {
    vi.useFakeTimers();
    const { client, socket, callbacks } = await authenticated(); client.startConversation(); await socket.receive(started);
    socket.bufferedAmount = pending; const count = socket.sent.length;
    client.sendAudio(new Int16Array(480));
    expect(socket.sent).toHaveLength(count); expect(client.state).toBe("disconnected");
    expect(callbacks.onError.mock.calls).toEqual([["AUDIO_INPUT_OVERFLOW"]]); expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(60000); expect(socket.sent).toHaveLength(count);
    expect(() => client.sendAudio(new Int16Array(480))).toThrow("INVALID_STATE");
  });
  it("accepts exactly the pending audio cap boundary", async () => {
    const { client, socket, callbacks } = await authenticated(); client.startConversation(); await socket.receive(started);
    socket.bufferedAmount = 50 * 994 - 994;
    client.sendAudio(new Int16Array(480));
    expect(socket.sent.at(-1)).toBeInstanceOf(ArrayBuffer); expect(client.state).toBe("preparing");
    expect(callbacks.onError).not.toHaveBeenCalled();
  });
  it.each(["ws://localhost/ws/device?", "ws://localhost/ws/device#"])("rejects an empty URL delimiter: %s", (url) => {
    vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", url);
    expect(() => new PorteroDeviceClient().connect({ deviceId: "PI-000001", secret })).toThrow("NEXT_PUBLIC_DEVICE_WS_URL");
    expect(Socket.instances).toHaveLength(0);
  });
  it("rejects oversized strings before UTF-8 allocation and still checks multibyte size", () => {
    const encoder = vi.spyOn(TextEncoder.prototype, "encode");
    expect(() => parseServerMessage(" ".repeat(65537))).toThrow("PROTOCOL_ERROR");
    expect(encoder).not.toHaveBeenCalled();
    const multibyte = JSON.stringify({ ...authOk, upload_token: "é".repeat(33000) });
    expect(multibyte.length).toBeLessThan(65536);
    expect(() => parseServerMessage(multibyte)).toThrow("PROTOCOL_ERROR"); expect(encoder).toHaveBeenCalledOnce();
  });
});
