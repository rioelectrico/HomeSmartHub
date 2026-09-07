import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SimulatorPage from "@/app/simulador-portero/page";
import { encodeAudioFrame } from "@/lib/simulator/audio-frame";

const conversationId = "123e4567-e89b-12d3-a456-426614174000";
const streamId = "223e4567-e89b-12d3-a456-426614174000";
class Socket {
  static current: Socket;
  readyState = 1;
  bufferedAmount = 0;
  sent: (string | ArrayBuffer)[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: unknown }) => void | Promise<void>;
  onclose?: (event: { code: number; reason: string }) => void;
  constructor() { Socket.current = this; }
  send(data: string | ArrayBuffer) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000, reason: "" }); }
  async receive(data: object | ArrayBuffer) { await this.onmessage?.({ data: data instanceof ArrayBuffer ? data : JSON.stringify(data) }); }
  controls() { return this.sent.filter((data): data is string => typeof data === "string").map((data) => JSON.parse(data)); }
}
type Processor = {
  port: { onmessage: (event: { data: unknown }) => void; postMessage: (data: unknown) => void };
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
};
let processors: Processor[];
let stopped: ReturnType<typeof vi.fn>;
beforeEach(() => {
  processors = [];
  stopped = vi.fn();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("WebSocket", Socket);
  vi.stubEnv("NEXT_PUBLIC_DEVICE_WS_URL", "ws://localhost:8000/ws/device");
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopped }] })) } });
  vi.stubGlobal("AudioContext", class {
    sampleRate = 24000;
    state = "running";
    destination = {};
    audioWorklet = { addModule: async () => {} };
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    close = async () => {};
  });
  vi.stubGlobal("AudioWorkletNode", class {
    port = { onmessage: null as null | ((event: { data: unknown }) => void), postMessage: (_data: unknown) => {}, close() {} };
    constructor() {
      let create!: new () => Processor;
      runInNewContext(readFileSync(resolve("public/portero-audio-worklet.js"), "utf8"), {
        sampleRate: 24000, Float32Array,
        AudioWorkletProcessor: class { port = { onmessage: null, postMessage: (data: unknown) => thisNode.port.onmessage?.({ data }) }; },
        registerProcessor: (_name: string, ctor: new () => Processor) => { create = ctor; },
      });
      const thisNode = this;
      const processor = new create();
      processors.push(processor);
      this.port.postMessage = (data) => processor.port.onmessage({ data });
    }
    connect() {}
    disconnect() {}
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("real simulator playback overflow integration", () => {
  it("accepts and plays a normal three-second response burst", async () => {
    render(<SimulatorPage />);
    fireEvent.change(screen.getByLabelText("Secreto del dispositivo"), { target: { value: "test-secret-1234567890123456789012345" } });
    fireEvent.click(screen.getByRole("button", { name: "Conectar" }));
    const socket = Socket.current;
    await act(async () => {
      socket.onopen?.();
      await socket.receive({ type: "auth.challenge", algorithm: "HMAC-SHA256", nonce: "test-nonce-1234567890123456789012345", expires_at: "2099-01-01T00:00:00Z" });
      await socket.receive({ type: "auth.ok", upload_token: "unused", upload_expires_at: "2099-01-01T00:00:00Z", heartbeat_interval_seconds: 10 });
    });
    fireEvent.click(screen.getByRole("button", { name: "Tocar timbre" }));
    await waitFor(() => expect(socket.controls().at(-1)?.type).toBe("conversation.start"));
    await act(() => socket.receive({ type: "conversation.started", version: 1, seq: 1, conversation_id: conversationId, stream_id: streamId, audio: { codec: "pcm16", sample_rate: 24000, channels: 1, frame_ms: 20 }, max_seconds: 300 }));

    let seq = 0n;
    const responseFrames = 150; // 3 seconds: over the old 2-second ring, below the new 10-second limit.
    const payload = new Uint8Array(960);
    for (let i = 1; i < payload.length; i += 2) payload[i] = 64; // PCM 16384 = 0.5
    await act(async () => {
      await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: ++seq, payload }));
    });

    expect(socket.controls().filter((m) => m.type === "conversation.audio.output_overflow")).toHaveLength(0);
    const firstOutput = new Float32Array(480);
    processors[0].process([], [[firstOutput]]);
    expect(firstOutput.every((value) => value === 0.5)).toBe(true);

    await act(async () => {
      for (let frame = 1; frame < responseFrames; frame++) await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: ++seq, payload }));
    });
    let played = 480;
    for (let frame = 1; frame < responseFrames; frame++) {
      const output = new Float32Array(480);
      processors[0].process([], [[output]]);
      played += output.filter((value) => value === 0.5).length;
    }
    expect(played).toBe(responseFrames * 480);
    expect(stopped).not.toHaveBeenCalled();
  });

  it.each(["burst", "three-times-playback"])("fails explicitly after the ten-second limit for %s and retries on the same authenticated socket", async (pattern) => {
    render(<SimulatorPage />);
    fireEvent.change(screen.getByLabelText("Secreto del dispositivo"), { target: { value: "test-secret-1234567890123456789012345" } });
    fireEvent.click(screen.getByRole("button", { name: "Conectar" }));
    const socket = Socket.current;
    await act(async () => {
      socket.onopen?.();
      await socket.receive({ type: "auth.challenge", algorithm: "HMAC-SHA256", nonce: "test-nonce-1234567890123456789012345", expires_at: "2099-01-01T00:00:00Z" });
      await socket.receive({ type: "auth.ok", upload_token: "unused", upload_expires_at: "2099-01-01T00:00:00Z", heartbeat_interval_seconds: 10 });
    });
    fireEvent.click(screen.getByRole("button", { name: "Tocar timbre" }));
    await waitFor(() => expect(socket.controls().at(-1)?.type).toBe("conversation.start"));
    await act(() => socket.receive({ type: "conversation.started", version: 1, seq: 1, conversation_id: conversationId, stream_id: streamId, audio: { codec: "pcm16", sample_rate: 24000, channels: 1, frame_ms: 20 }, max_seconds: 300 }));
    let seq = 0n;
    let played = 0;
    await act(async () => {
      for (let tick = 0; tick < (pattern === "burst" ? 502 : 250); tick++) {
        for (let part = 0; part < (pattern === "burst" ? 1 : 3); part++) {
          const payload = new Uint8Array(960);
          for (let i = 1; i < payload.length; i += 2) payload[i] = 64; // PCM 16384 = 0.5
          await socket.receive(encodeAudioFrame({ streamId, direction: 2, sequence: ++seq, payload }));
        }
        if (pattern !== "burst") {
          const output = new Float32Array(480);
          processors[0].process([], [[output]]);
          played += output.filter((value) => value === 0.5).length;
        }
      }
    });
    expect(played).toBe(pattern === "burst" ? 0 : 249 * 480);
    expect(socket.controls().filter((m) => m.type === "conversation.audio.output_overflow")).toHaveLength(1);
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("El audio de salida se saturó");
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeDisabled();
    expect(socket.readyState).toBe(1);
    const silent = new Float32Array(480);
    processors[0].process([[new Float32Array(480).fill(1)]], [[silent]]);
    expect(silent.every((value) => value === 0)).toBe(true);
    expect(socket.sent.some((data) => data instanceof ArrayBuffer)).toBe(false);
    await act(async () => {
      await socket.receive({ type: "conversation.audio.clear", version: 1, seq: 2, conversation_id: conversationId, stream_id: streamId, reason: "audio_output_overflow" });
      await socket.receive({ type: "conversation.error", version: 1, seq: 3, code: "AUDIO_OUTPUT_OVERFLOW", correlation_id: streamId });
      await socket.receive({ type: "conversation.ended", version: 1, seq: 4, conversation_id: conversationId, outcome: "failed", reason: "audio_output_overflow" });
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Tocar timbre" }));
    await waitFor(() => expect(processors).toHaveLength(2));
    expect(socket.controls().filter((m) => m.type === "conversation.start")).toHaveLength(2);
    expect(socket.readyState).toBe(1);
  });
});
