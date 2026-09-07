import { decodeAudioFrame, encodeAudioFrame } from "./audio-frame";
import { parseServerMessage, type AudioOutputOverflowMessage, type ClientErrorCode, type ClientState, type CommandRequestMessage, type DeviceClientCallbacks, type ServerMessage } from "./contracts";

let pageBootId: string | undefined;
const pageStartedAt = Date.now();
const speakingStates: ClientState[] = ["preparing", "listening", "visitor_speaking", "assistant_speaking"];
// Fifty full 20 ms PAUD frames: at most one second (49,700 bytes) queued for transport.
const MAX_AUDIO_BUFFERED_BYTES = 50 * (34 + 960);
const closeErrors: Readonly<Record<number, ClientErrorCode>> = {
  4000: "PROTOCOL_ERROR",
  4001: "CONNECTION_REPLACED",
  4002: "AUTH_FAILED",
  4003: "PROTOCOL_VERSION_UNSUPPORTED",
  4004: "MESSAGE_TOO_LARGE",
  4005: "INVALID_SEQUENCE",
  4006: "HANDSHAKE_TIMEOUT",
};

function socketUrl(): string {
  const configured = process.env.NEXT_PUBLIC_DEVICE_WS_URL;
  const value = configured || (process.env.NODE_ENV === "development" ? "ws://localhost:8000/ws/device" : undefined);
  if (!value) throw new Error("Configure NEXT_PUBLIC_DEVICE_WS_URL for the device connection.");
  try {
    if (value.includes("?") || value.includes("#")) throw new Error();
    const parsed = new URL(value);
    if (!["ws:", "wss:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error();
    return parsed.href;
  } catch { throw new Error("NEXT_PUBLIC_DEVICE_WS_URL must be a ws:// or wss:// URL without credentials, query or fragment."); }
}

export class PorteroDeviceClient {
  #socket?: WebSocket;
  #state: ClientState = "disconnected";
  #jsonSequence = 0;
  #serverSequence = -1;
  #inputSequence = 0n;
  #outputSequence = 0n;
  #conversationId?: string;
  #streamId?: string;
  #outputOverflow = false;
  #heartbeat?: ReturnType<typeof setInterval>;
  #discardHandshake?: () => void;
  #callbacks: DeviceClientCallbacks;

  constructor(callbacks: DeviceClientCallbacks = {}) { this.#callbacks = callbacks; }
  get state(): ClientState { return this.#state; }
  get conversationId(): string | undefined { return this.#conversationId; }
  get streamId(): string | undefined { return this.#streamId; }

  /** Credentials live only in this handshake closure and are discarded after key import. */
  connect({ deviceId, secret }: { deviceId: string; secret: string }): void {
    if (this.#state !== "disconnected") throw new Error("INVALID_STATE");
    const url = socketUrl();
    if (!globalThis.crypto?.subtle || !globalThis.crypto.randomUUID) throw new Error("WEB_CRYPTO_UNAVAILABLE");
    if (!/^PI-[0-9]{6}$/.test(deviceId) || typeof secret !== "string" || secret.length < 32 || secret.length > 128) throw new Error("INVALID_CREDENTIALS");
    pageBootId ??= crypto.randomUUID();
    let socket: WebSocket;
    try { socket = new WebSocket(url); } catch { secret = ""; throw new Error("CONNECTION_ERROR"); }
    this.#socket = socket;
    socket.binaryType = "arraybuffer";
    this.#jsonSequence = 0;
    this.#serverSequence = -1;
    let phase: "hello" | "challenge" | "signing" | "auth" = "hello";
    this.#discardHandshake = () => { secret = ""; };
    socket.onopen = () => {
      if (!this.#isCurrent(socket) || phase !== "hello") return;
      phase = "challenge";
      this.#send({ type: "device.hello", version: "v1", device_id: deviceId, capabilities: ["audio_pcm16_v1"] });
      if (this.#isCurrent(socket)) this.#setState("authenticating");
    };
    socket.onmessage = async (event) => {
      if (!this.#isCurrent(socket)) return;
      let message: ServerMessage;
      try { message = parseServerMessage(event.data); } catch { this.#fail("PROTOCOL_ERROR", socket); return; }
      if (phase === "challenge" && message.type === "auth.challenge") {
        phase = "signing";
        const bytes = new TextEncoder().encode(secret);
        secret = "";
        try {
          const key = await crypto.subtle.importKey("raw", bytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
          bytes.fill(0);
          if (!this.#isCurrent(socket)) return;
          const canonical = new TextEncoder().encode(`v1\n${deviceId}\n${pageBootId}\n${message.nonce}`);
          const signature = await crypto.subtle.sign("HMAC", key, canonical);
          if (!this.#isCurrent(socket)) return;
          const digest = Array.from(new Uint8Array(signature), (byte) => byte.toString(16).padStart(2, "0")).join("");
          phase = "auth";
          this.#send({ type: "auth.response", nonce: message.nonce, digest });
        } catch { this.#fail("AUTH_FAILED", socket); }
        finally { bytes.fill(0); secret = ""; }
      } else if (phase === "auth" && message.type === "auth.ok") {
        secret = "";
        this.#discardHandshake = undefined;
        // Detach the credential closure. Upload credentials are deliberately not retained.
        socket.onopen = null;
        socket.onmessage = (next) => { if (this.#isCurrent(socket)) this.#receive(next.data, socket); };
        this.#sendStatus();
        if (!this.#isCurrent(socket)) return;
        this.#heartbeat = setInterval(() => {
          if (this.#isCurrent(socket)) this.#send({ type: "device.heartbeat", uptime_seconds: this.#uptime() });
        }, message.heartbeat_interval_seconds * 1000);
        this.#setState("ready");
      } else this.#fail("PROTOCOL_ERROR", socket);
    };
    socket.onclose = (event) => {
      if (this.#socket !== socket) return;
      if (event.code === 1000) this.disconnect();
      else this.#fail(closeErrors[event.code] ?? "CONNECTION_ERROR", socket);
    };
    socket.onerror = () => { this.#fail("CONNECTION_ERROR", socket); };
    this.#setState("connecting");
  }

  startConversation(): void {
    this.#require(this.#state === "ready");
    const socket = this.#socket!;
    this.#setState("starting");
    if (this.#isCurrent(socket) && this.#state === "starting") this.#send({ type: "conversation.start", version: 1, mode: "hands_free" });
  }

  sendAudio(pcm16: Int16Array): void {
    this.#require(speakingStates.includes(this.#state) && !!this.#streamId);
    const socket = this.#socket!;
    if (!(pcm16 instanceof Int16Array) || pcm16.length === 0 || pcm16.length > 480) throw new Error("INVALID_AUDIO_FRAME");
    const payload = new Uint8Array(pcm16.length * 2);
    const view = new DataView(payload.buffer);
    for (let i = 0; i < pcm16.length; i++) view.setInt16(i * 2, pcm16[i], true);
    const frame = encodeAudioFrame({ streamId: this.#streamId!, direction: 1, sequence: this.#inputSequence + 1n, payload });
    if (socket.bufferedAmount + frame.byteLength > MAX_AUDIO_BUFFERED_BYTES) {
      this.#fail("AUDIO_INPUT_OVERFLOW", socket);
      return;
    }
    this.#inputSequence++;
    try { socket.send(frame); } catch { this.#fail("CONNECTION_ERROR", socket); }
  }

  stopConversation(): void {
    this.#require((speakingStates.includes(this.#state) || this.#state === "error") && !!this.#conversationId);
    const socket = this.#socket!;
    const conversationId = this.#conversationId;
    this.#setState("stopping");
    if (this.#isCurrent(socket) && this.#state === "stopping") this.#send({ type: "conversation.stop", version: 1, conversation_id: conversationId, reason: "visitor_finished" });
  }

  /** Report only the audio run that is still owned by this authenticated visit.
   * Keep the IDs until ended so in-flight frames remain validated but unplayed.
   */
  reportAudioOutputOverflow(conversationId: string, streamId: string): void {
    const socket = this.#socket;
    if (!socket || !this.#isCurrent(socket) || !speakingStates.includes(this.#state)
      || conversationId !== this.#conversationId || streamId !== this.#streamId || this.#outputOverflow) return;
    this.#outputOverflow = true;
    this.#setState("stopping");
    if (!this.#isCurrent(socket)) return;
    const message: AudioOutputOverflowMessage = { type: "conversation.audio.output_overflow", version: 1, conversation_id: conversationId, stream_id: streamId };
    this.#send({ ...message });
    if (this.#isCurrent(socket)) this.#notify(() => this.#callbacks.onError?.("AUDIO_OUTPUT_OVERFLOW"));
  }

  disconnect(): void {
    const socket = this.#socket;
    this.#socket = undefined;
    this.#discardHandshake?.(); this.#discardHandshake = undefined;
    if (this.#heartbeat !== undefined) clearInterval(this.#heartbeat);
    this.#heartbeat = undefined;
    this.#clearConversation();
    if (socket) {
      socket.onopen = null; socket.onmessage = null; socket.onclose = null; socket.onerror = null;
      if (socket.readyState === 0 || socket.readyState === 1) { try { socket.close(); } catch { /* Already closing. */ } }
    }
    this.#setState("disconnected");
  }

  #receive(data: unknown, socket: WebSocket): void {
    if (!this.#isCurrent(socket)) return;
    if (data instanceof ArrayBuffer) {
      let samples: Int16Array;
      try {
        if (!this.#streamId || ![...speakingStates, "stopping"].includes(this.#state)) throw new Error();
        const frame = decodeAudioFrame(data, { streamId: this.#streamId, lastSequence: this.#outputSequence });
        this.#outputSequence = frame.sequence;
        samples = new Int16Array(frame.payload.byteLength / 2);
        const view = new DataView(frame.payload.buffer, frame.payload.byteOffset, frame.payload.byteLength);
        for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true);
      } catch { this.#fail("PROTOCOL_ERROR", socket); return; }
      if (this.#state !== "stopping") this.#notify(() => this.#callbacks.onAudio?.(samples));
      return;
    }
    let message: ServerMessage;
    try {
      if (typeof data !== "string") throw new Error();
      message = parseServerMessage(data);
      // V1 commands have no server sequence and must not alter conversation ordering.
      if (message.type === "command.request") {
        this.#handleCommand(message, socket);
        return;
      }
      if (!("seq" in message) || message.seq <= this.#serverSequence) throw new Error();
      if (message.type === "conversation.started") {
        if (this.#state !== "starting" || this.#conversationId) throw new Error();
      } else if ("conversation_id" in message && message.conversation_id !== this.#conversationId) throw new Error();
      if (message.type === "conversation.audio.clear" && message.stream_id !== this.#streamId) throw new Error();
      this.#serverSequence = message.seq;
    } catch { this.#fail("PROTOCOL_ERROR", socket); return; }
    switch (message.type) {
      case "conversation.started":
        this.#conversationId = message.conversation_id; this.#streamId = message.stream_id;
        this.#inputSequence = 0n; this.#outputSequence = 0n;
        this.#setState("preparing"); break;
      case "conversation.state":
        if (this.#state !== "stopping") this.#setState(message.state);
        break;
      case "conversation.transcript": this.#notify(() => this.#callbacks.onTranscript?.(message)); break;
      case "conversation.audio.clear": this.#notify(() => this.#callbacks.onAudioClear?.(message)); break;
      case "conversation.ended":
        this.#clearConversation(); this.#setState("ready");
        if (this.#isCurrent(socket)) this.#notify(() => this.#callbacks.onEnded?.(message));
        break;
      case "conversation.error":
        if (this.#state === "starting") this.#setState("ready");
        if (this.#isCurrent(socket) && !(this.#outputOverflow && message.code === "AUDIO_OUTPUT_OVERFLOW")) this.#notify(() => this.#callbacks.onError?.(message.code));
        break;
    }
  }

  #handleCommand(message: CommandRequestMessage, socket: WebSocket): void {
    if (message.command === "camera.capture") {
      this.#send({ type: "command.ack", command_id: message.command_id, status: "rejected", error_code: "CAMERA_NOT_READY" });
      return;
    }
    this.#send({ type: "command.ack", command_id: message.command_id, status: "accepted" });
    if (!this.#isCurrent(socket)) return;
    this.#sendStatus();
    if (!this.#isCurrent(socket)) return;
    this.#send({ type: "command.result", command_id: message.command_id, status: "completed", result: {} });
  }
  #sendStatus(): void {
    this.#send({ type: "device.status", firmware_version: "simulator", hardware_model: "simulator", uptime_seconds: this.#uptime(), ethernet: "online", camera: "unavailable", microphone: "ready", speaker: "ready", free_heap_bytes: 0 });
  }
  #notify(deliver: () => void): void {
    try { deliver(); } catch { /* Consumer errors never alter transport state or expose payloads. */ }
  }
  #setState(state: ClientState): void { if (this.#state !== state) { this.#state = state; this.#notify(() => this.#callbacks.onState?.(state)); } }
  #isCurrent(socket: WebSocket): boolean { return this.#socket === socket && socket.readyState === 1; }
  #require(condition: boolean): void { if (!condition || !this.#socket || !this.#isCurrent(this.#socket)) throw new Error("INVALID_STATE"); }
  #uptime(): number { return Math.max(0, Math.floor((Date.now() - pageStartedAt) / 1000)); }
  #clearConversation(): void { this.#conversationId = undefined; this.#streamId = undefined; this.#inputSequence = 0n; this.#outputSequence = 0n; this.#outputOverflow = false; }
  #fail(code: ClientErrorCode, socket: WebSocket): void {
    if (this.#socket !== socket) return;
    // Complete cleanup before notifying consumers; they may immediately reconnect.
    this.disconnect();
    this.#notify(() => this.#callbacks.onError?.(code));
  }
  #send(payload: Record<string, unknown>): void {
    const socket = this.#socket;
    if (!socket || !this.#isCurrent(socket)) return;
    if (this.#jsonSequence >= Number.MAX_SAFE_INTEGER) { this.#fail("PROTOCOL_ERROR", socket); return; }
    try { socket.send(JSON.stringify({ ...payload, boot_id: pageBootId, seq: ++this.#jsonSequence })); }
    catch { this.#fail("CONNECTION_ERROR", socket); }
  }
}
