import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PorteroAudioSession } from "@/lib/simulator/audio-session";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function browser(sampleRate = 48000) {
  const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
  const stream = { getTracks: () => tracks } as unknown as MediaStream;
  const getUserMedia = vi.fn().mockResolvedValue(stream);
  const source = { connect: vi.fn(), disconnect: vi.fn() };
  const context = {
    sampleRate, state: "suspended", destination: {},
    audioWorklet: { addModule: vi.fn().mockResolvedValue(undefined) },
    createMediaStreamSource: vi.fn(() => source),
    resume: vi.fn().mockResolvedValue(undefined), close: vi.fn().mockResolvedValue(undefined),
  };
  const node = {
    port: { onmessage: null as ((event: MessageEvent) => void) | null, postMessage: vi.fn(), close: vi.fn() },
    connect: vi.fn(), disconnect: vi.fn(), onprocessorerror: null,
  };
  const Context = vi.fn(function () { return context; });
  const Node = vi.fn(function () { return node; });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.stubGlobal("AudioContext", Context);
  vi.stubGlobal("AudioWorkletNode", Node);
  return { tracks, stream, getUserMedia, source, context, node, Context, Node,
    capture(samples: Float32Array, rms = 0.25) { node.port.onmessage?.({ data: { type: "capture", samples, rms } } as MessageEvent); },
  };
}

describe("browser audio session", () => {
  let session: PorteroAudioSession;
  beforeEach(() => { session = new PorteroAudioSession(); });
  afterEach(async () => { await session.close(); });

  it("requests mono voice capture, loads registered processor and resumes the connected graph", async () => {
    const b = browser();
    await session.start(vi.fn(), vi.fn());
    expect(b.getUserMedia).toHaveBeenCalledWith({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    expect(b.context.audioWorklet.addModule).toHaveBeenCalledWith("/portero-audio-worklet.js");
    expect(b.Node).toHaveBeenCalledWith(b.context, "portero-audio", expect.objectContaining({ outputChannelCount: [1] }));
    expect(b.source.connect).toHaveBeenCalledWith(b.node);
    expect(b.node.connect).toHaveBeenCalledWith(b.context.destination);
    expect(b.context.resume).toHaveBeenCalledTimes(1);
  });

  it.each([[48000, 19200, 9600], [44100, 18816, 10240], [16000, 6401, 9601], [24000, 9600, 9600]])(
    "streams %i Hz over arbitrary chunks with exact frames and continuous phase", async (rate, length, count) => {
      const b = browser(rate);
      const frames: Int16Array[] = [];
      await session.start((frame) => frames.push(frame), vi.fn());
      // A ramp makes dropped/duplicated boundary samples observable independently of the resampler.
      const signal = Float32Array.from({ length }, (_, i) => i / length * 0.8);
      for (let offset = 0; offset < length; offset += 128) b.capture(signal.slice(offset, offset + 128));
      expect(frames).toHaveLength(Math.floor(count / 480));
      expect(frames.every((frame) => frame.length === 480)).toBe(true);
      const values = frames.flatMap((frame) => Array.from(frame));
      const errors = values.map((value, index) => {
        const expected = Math.round(index * rate / 24000 / length * 0.8 * 32767);
        return Math.abs(value - expected);
      });
      expect(Math.max(...errors)).toBeLessThanOrEqual(1);
    },
  );

  it("retains the 44.1k residual and emits the next frame without losing duration", async () => {
    const b = browser(44100);
    const frames = vi.fn();
    await session.start(frames, vi.fn());
    for (let i = 0; i < 147; i++) b.capture(new Float32Array(128).fill(0.5));
    expect(frames).toHaveBeenCalledTimes(21); // 10240 samples => 21 frames + 160 residual.
    b.capture(new Float32Array(588).fill(0.5)); // 320 more samples.
    expect(frames).toHaveBeenCalledTimes(22);
    expect(Array.from(frames.mock.calls[21][0] as Int16Array)).toEqual(Array(480).fill(16384));
  });

  it("preserves one-sample boundaries across irregular capture partitions", async () => {
    const b = browser(44100);
    const frames: Int16Array[] = [];
    await session.start((frame) => frames.push(frame), vi.fn());
    const signal = Float32Array.from({ length: 8820 }, (_, i) => Math.sin(i * 0.04) * 0.7);
    const sizes = [1, 7, 513, 2, 127, 89];
    let offset = 0, chunk = 0;
    while (offset < signal.length) {
      const length = sizes[chunk++ % sizes.length];
      b.capture(signal.slice(offset, offset + length));
      offset += length;
    }
    expect(frames).toHaveLength(10);
    const values = frames.flatMap((frame) => Array.from(frame));
    const errors = values.map((value, i) => {
      const position = i * 147 / 80;
      const left = Math.floor(position), fraction = position - left;
      const interpolated = signal[left] * (1 - fraction) + signal[left + 1] * fraction;
      return Math.abs(value - Math.round(interpolated * (interpolated < 0 ? 32768 : 32767)));
    });
    expect(Math.max(...errors)).toBeLessThanOrEqual(1);
  });

  it("saturates PCM16, sanitizes nonfinite capture and isolates callback exceptions", async () => {
    const b = browser(24000);
    const frames = vi.fn((_frame: Int16Array) => { throw new Error("consumer"); });
    const level = vi.fn(() => { throw new Error("consumer"); });
    await session.start(frames, level);
    const samples = new Float32Array(960).fill(2);
    samples.set([-2, NaN, Infinity, -Infinity]);
    expect(() => b.capture(samples, NaN)).not.toThrow();
    expect(frames).toHaveBeenCalledTimes(2);
    expect(Array.from(frames.mock.calls[0][0] as unknown as Int16Array).slice(0, 5)).toEqual([-32768, 0, 0, 0, 32767]);
    expect(level).toHaveBeenCalledWith(0);
  });

  it("decodes little endian bytes including subviews and clears queued playback", async () => {
    const b = browser(24000);
    await session.start(vi.fn(), vi.fn());
    session.enqueuePlayback(new Uint8Array([99, 0, 128, 255, 127, 0, 0, 99]).subarray(1, 7));
    const message = b.node.port.postMessage.mock.calls[0][0];
    expect(message.type).toBe("play");
    expect(Array.from(message.samples)).toEqual([-1, 32767 / 32768, 0]);
    session.clearPlayback();
    expect(b.node.port.postMessage).toHaveBeenLastCalledWith({ type: "clear", epoch: 1 });
    expect(() => session.enqueuePlayback(new Uint8Array(3))).toThrow();
  });

  it("plays 24k server audio at the hardware rate and resets interpolation on clear", async () => {
    const b = browser(48000);
    await session.start(vi.fn(), vi.fn());
    session.enqueuePlayback(new Uint8Array([0, 0, 0, 64]));
    session.enqueuePlayback(new Uint8Array([0, 0]));
    const played = b.node.port.postMessage.mock.calls.flatMap(([message]) => Array.from(message.samples ?? []));
    expect(played).toEqual([0, 0.25, 0.5, 0.25, 0]);
    session.clearPlayback();
    session.enqueuePlayback(new Uint8Array([0, 128]));
    expect(Array.from(b.node.port.postMessage.mock.calls.at(-1)![0].samples)).toEqual([-1]);
  });

  it("accepts normalized PCM16 samples directly and owns the posted float buffer", async () => {
    const b = browser(24000);
    await session.start(vi.fn(), vi.fn());
    const pcm = new Int16Array([-32768, 0, 32767]);
    session.enqueuePlayback(pcm);
    const [message, transfers] = b.node.port.postMessage.mock.calls[0];
    pcm.fill(123);
    expect(Array.from(message.samples)).toEqual([-1, 0, 32767 / 32768]);
    expect(transfers).toEqual([message.samples.buffer]);
  });

  it("bounds pending MessagePort audio and fails once before posting excess playback", async () => {
    const b = browser(24000);
    const failed = vi.fn();
    const frames = vi.fn();
    await session.start(frames, vi.fn(), failed);
    const oldHandler = b.node.port.onmessage!;
    for (let i = 0; i < 501; i++) session.enqueuePlayback(new Int16Array(480).fill(100));
    expect(failed).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
    expect(b.node.port.postMessage.mock.calls.filter(([message]) => message.type === "play")).toHaveLength(500);
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
    oldHandler({ data: { type: "capture", samples: new Float32Array(480), rms: 1 } } as MessageEvent);
    oldHandler({ data: { type: "output_overflow" } } as MessageEvent);
    session.enqueuePlayback(new Int16Array(480));
    expect(frames).not.toHaveBeenCalled();
    expect(failed).toHaveBeenCalledTimes(1);
    await session.close();
    expect(b.context.close).toHaveBeenCalledTimes(1);
  });

  it("accepts consumed capacity while ignoring pre-clear acknowledgements", async () => {
    const b = browser(24000);
    const failed = vi.fn();
    await session.start(vi.fn(), vi.fn(), failed);
    session.enqueuePlayback(new Int16Array(240000));
    b.node.port.onmessage!({ data: { type: "played", epoch: 0, samples: 480 } } as MessageEvent);
    session.enqueuePlayback(new Int16Array(480));
    expect(failed).not.toHaveBeenCalled();
    session.clearPlayback();
    b.node.port.onmessage!({ data: { type: "cleared", epoch: 0, samples: 240000 } } as MessageEvent);
    session.enqueuePlayback(new Int16Array(240000));
    b.node.port.onmessage!({ data: { type: "played", epoch: 0, samples: 240000 } } as MessageEvent);
    session.enqueuePlayback(new Int16Array(1));
    expect(failed).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
  });

  it("does not free MessagePort capacity until a pending clear is actually processed", async () => {
    browser(24000);
    const failed = vi.fn();
    await session.start(vi.fn(), vi.fn(), failed);
    session.enqueuePlayback(new Int16Array(240000));
    session.clearPlayback();
    session.clearPlayback();
    session.enqueuePlayback(new Int16Array(1));
    expect(failed).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
  });

  it("propagates a terminal worklet overflow despite concurrent clear and permits a fresh run", async () => {
    const b = browser();
    const failed = vi.fn();
    await session.start(vi.fn(), vi.fn(), failed);
    session.clearPlayback();
    b.node.port.onmessage!({ data: { type: "output_overflow" } } as MessageEvent);
    expect(failed).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
    await session.close();
    const next = browser();
    await session.start(vi.fn(), vi.fn(), failed);
    session.enqueuePlayback(new Int16Array(480));
    expect(next.node.port.postMessage).toHaveBeenCalled();
    expect(next.context.close).not.toHaveBeenCalled();
  });

  it("rejects an oversized PCM input before conversion, without retaining its tail", async () => {
    const b = browser();
    const failed = vi.fn();
    await session.start(vi.fn(), vi.fn(), failed);
    session.enqueuePlayback(new Int16Array(240001));
    expect(failed).toHaveBeenCalledExactlyOnceWith("AUDIO_OUTPUT_OVERFLOW");
    expect(b.node.port.postMessage.mock.calls.some(([message]) => message.type === "play")).toBe(false);
  });

  it.each(["mediaDevices", "getUserMedia", "AudioContext", "AudioWorkletNode", "audioWorklet"])("fails cleanly with missing %s", async (api) => {
    const b = browser();
    if (api === "mediaDevices") vi.stubGlobal("navigator", {});
    else if (api === "getUserMedia") vi.stubGlobal("navigator", { mediaDevices: {} });
    else if (api === "audioWorklet") Object.assign(b.context, { audioWorklet: undefined });
    else vi.stubGlobal(api, undefined);
    await expect(session.start(vi.fn(), vi.fn())).rejects.toThrow();
    await session.close();
    if (api === "audioWorklet") {
      b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
      expect(b.context.close).toHaveBeenCalledTimes(1);
    }
  });

  it.each(["permission", "module", "node", "source", "connect", "resume"])("cleans partial initialization after %s failure", async (step) => {
    const b = browser();
    const error = new Error(step);
    if (step === "permission") b.getUserMedia.mockRejectedValueOnce(error);
    if (step === "module") b.context.audioWorklet.addModule.mockRejectedValueOnce(error);
    if (step === "node") b.Node.mockImplementationOnce(function () { throw error; });
    if (step === "source") b.context.createMediaStreamSource.mockImplementationOnce(() => { throw error; });
    if (step === "connect") b.node.connect.mockImplementationOnce(() => { throw error; });
    if (step === "resume") b.context.resume.mockRejectedValueOnce(error);
    await expect(session.start(vi.fn(), vi.fn())).rejects.toThrow(step);
    await session.close();
    expect(b.context.close).toHaveBeenCalledTimes(step === "permission" ? 0 : 1);
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(step === "permission" ? 0 : 1));
    expect(b.node.port.onmessage).toBeNull();
  });

  it.each(["permission", "module", "resume"])("close during %s prevents late callbacks and disposes once", async (step) => {
    const b = browser();
    const gate = deferred<MediaStream | undefined>();
    if (step === "permission") b.getUserMedia.mockReturnValueOnce(gate.promise);
    if (step === "module") b.context.audioWorklet.addModule.mockReturnValueOnce(gate.promise);
    if (step === "resume") b.context.resume.mockReturnValueOnce(gate.promise);
    const frames = vi.fn();
    const started = session.start(frames, vi.fn());
    await vi.waitFor(() => expect(step === "permission" ? b.getUserMedia : step === "module" ? b.context.audioWorklet.addModule : b.context.resume).toHaveBeenCalled());
    const oldHandler = b.node.port.onmessage;
    await session.close();
    gate.resolve(step === "permission" ? b.stream : undefined);
    await started;
    oldHandler?.({ data: { type: "capture", samples: new Float32Array(2000), rms: 0 } } as MessageEvent);
    expect(frames).not.toHaveBeenCalled();
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
    expect(b.context.close).toHaveBeenCalledTimes(step === "permission" ? 0 : 1);
    expect(b.node.port.onmessage).toBeNull();
  });

  it("shares failed-start cleanup with repeated close until the context actually closes", async () => {
    const b = browser();
    const gate = deferred<void>();
    const failure = new Error("worklet failed");
    b.context.audioWorklet.addModule.mockRejectedValueOnce(failure);
    b.context.close.mockReturnValueOnce(gate.promise);
    const started = session.start(vi.fn(), vi.fn()).catch((error: unknown) => error);
    await vi.waitFor(() => expect(b.context.close).toHaveBeenCalledTimes(1));
    let closed = false;
    const closing = session.close();
    const repeated = session.close();
    void closing.then(() => { closed = true; });
    try {
      await Promise.resolve();
      expect(closed).toBe(false);
      expect(repeated).toBe(closing);
      b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
    } finally {
      gate.resolve();
    }
    await closing;
    expect(await started).toBe(failure);
    expect(b.context.close).toHaveBeenCalledTimes(1);
    const next = browser(24000);
    const frames = vi.fn();
    await session.start(frames, vi.fn());
    next.capture(new Float32Array(480));
    expect(frames).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("waits for failed-start cleanup before restart (cancel queued restart: %s)", async (cancel) => {
    const b = browser();
    const gate = deferred<void>();
    const failure = new Error("worklet failed");
    b.context.audioWorklet.addModule.mockRejectedValueOnce(failure);
    b.context.close.mockReturnValueOnce(gate.promise);
    const started = session.start(vi.fn(), vi.fn()).catch((error: unknown) => error);
    await vi.waitFor(() => expect(b.context.close).toHaveBeenCalledTimes(1));
    const next = browser(24000);
    const restart = session.start(vi.fn(), vi.fn());
    let closed = false;
    const closing = cancel ? session.close() : undefined;
    if (closing) void closing.then(() => { closed = true; });
    try {
      await Promise.resolve();
      await Promise.resolve();
      expect(next.getUserMedia).not.toHaveBeenCalled();
      expect(closed).toBe(false);
    } finally {
      gate.resolve();
    }
    await Promise.all([closing, restart]);
    expect(await started).toBe(failure);
    expect(next.getUserMedia).toHaveBeenCalledTimes(cancel ? 0 : 1);
    expect(b.context.close).toHaveBeenCalledTimes(1);
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
  });

  it("shares double start, disconnects once, drops residuals and rejects old capture after restart", async () => {
    const b = browser(24000);
    const oldFrame = vi.fn();
    const first = session.start(oldFrame, vi.fn());
    const second = session.start(vi.fn(), vi.fn());
    await Promise.all([first, second]);
    expect(b.getUserMedia).toHaveBeenCalledTimes(1);
    b.capture(new Float32Array(479));
    const oldHandler = b.node.port.onmessage!;
    await Promise.all([session.close(), session.close()]);
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
    expect(b.context.close).toHaveBeenCalledTimes(1);
    expect(b.source.disconnect).toHaveBeenCalledTimes(1);
    expect(b.node.disconnect).toHaveBeenCalledTimes(1);
    expect(b.node.port.close).toHaveBeenCalledTimes(1);
    const next = browser(24000);
    const newFrame = vi.fn();
    await session.start(newFrame, vi.fn());
    oldHandler({ data: { type: "capture", samples: new Float32Array(960), rms: 0 } } as MessageEvent);
    next.capture(new Float32Array(1));
    expect(oldFrame).not.toHaveBeenCalled();
    expect(newFrame).not.toHaveBeenCalled();
  });

  it("stops a capture loop immediately when the callback closes it", async () => {
    const b = browser(24000);
    const frames = vi.fn(() => { void session.close(); });
    await session.start(frames, vi.fn());
    b.capture(new Float32Array(1920));
    expect(frames).toHaveBeenCalledTimes(1);
  });

  it("continues cleanup if disconnect/close fail without unhandled rejection", async () => {
    const b = browser();
    await session.start(vi.fn(), vi.fn());
    b.source.disconnect.mockImplementation(() => { throw new Error("disconnected"); });
    b.context.close.mockRejectedValueOnce(new Error("closed"));
    await expect(session.close()).resolves.toBeUndefined();
    expect(b.node.disconnect).toHaveBeenCalledTimes(1);
    b.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
  });

  it.each(["resolve", "reject"])("an old pending start cannot affect a new session when it later %ss", async (outcome) => {
    const old = browser();
    const gate = deferred<MediaStream>();
    old.getUserMedia.mockReturnValueOnce(gate.promise);
    const oldFrames = vi.fn();
    const pending = session.start(oldFrames, vi.fn());
    await session.close();
    const next = browser(24000);
    const frames = vi.fn();
    await session.start(frames, vi.fn());
    if (outcome === "resolve") gate.resolve(old.stream);
    else gate.reject(new Error("late permission failure"));
    await expect(pending).resolves.toBeUndefined();
    next.capture(new Float32Array(480).fill(0.5));
    expect(frames).toHaveBeenCalledTimes(1);
    expect(oldFrames).not.toHaveBeenCalled();
    expect(next.context.close).not.toHaveBeenCalled();
    old.tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(outcome === "resolve" ? 1 : 0));
  });

  it("does not resume an already running context and can retry a rejected start", async () => {
    const b = browser();
    b.getUserMedia.mockRejectedValueOnce(new Error("permission"));
    await expect(session.start(vi.fn(), vi.fn())).rejects.toThrow("permission");
    b.context.state = "running";
    await session.start(vi.fn(), vi.fn());
    expect(b.getUserMedia).toHaveBeenCalledTimes(2);
    expect(b.context.resume).not.toHaveBeenCalled();
  });

  it("imports and constructs without browser APIs (SSR)", async () => {
    vi.stubGlobal("navigator", undefined);
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("AudioWorkletNode", undefined);
    vi.resetModules();
    const { PorteroAudioSession: Session } = await import("@/lib/simulator/audio-session");
    const server = new Session();
    await expect(server.close()).resolves.toBeUndefined();
    await expect(server.start(vi.fn(), vi.fn())).rejects.toThrow("BROWSER_AUDIO_UNAVAILABLE");
  });
});

type Processor = {
  port: { onmessage: (event: { data: unknown }) => void; postMessage: ReturnType<typeof vi.fn> };
  process: (inputs: Float32Array[][], outputs: Float32Array[][]) => boolean;
};
function processor(rate = 24000) {
  let registered: (new () => Processor) | undefined;
  const registerProcessor = vi.fn((_name: string, ctor: new () => Processor) => { registered = ctor; });
  runInNewContext(readFileSync(resolve("public/portero-audio-worklet.js"), "utf8"), {
    AudioWorkletProcessor: class { port = { onmessage: null, postMessage: vi.fn() }; },
    registerProcessor, sampleRate: rate, Float32Array,
  });
  expect(registerProcessor.mock.calls[0][0]).toBe("portero-audio");
  return new registered!();
}

describe("real worklet processor", () => {
  it("plays FIFO across blocks and writes exact zero on every empty sample", () => {
    const p = processor();
    p.port.onmessage({ data: { type: "play", samples: new Float32Array([0.1, 0.2, 0.3]) } });
    p.port.onmessage({ data: { type: "play", samples: new Float32Array([0.4]) } });
    const a = new Float32Array(2), b = new Float32Array(4).fill(9);
    expect(p.process([], [[a]])).toBe(true);
    p.process([], [[b]]);
    expect(a).toEqual(new Float32Array([0.1, 0.2]));
    expect(b).toEqual(new Float32Array([0.3, 0.4, 0, 0]));
  });

  it("clear erases partly consumed and queued blocks before the next quantum", () => {
    const p = processor();
    p.port.onmessage({ data: { type: "play", samples: new Float32Array(300).fill(0.5) } });
    p.process([], [[new Float32Array(128)]]);
    p.port.onmessage({ data: { type: "play", samples: new Float32Array([1]) } });
    p.port.onmessage({ data: { type: "clear" } });
    const output = new Float32Array(512).fill(7);
    p.process([], [[output]]);
    expect(output.every((value) => value === 0)).toBe(true);
  });

  it("fails and silences exactly once before a burst exceeds ten seconds", () => {
    const p = processor();
    p.port.onmessage({ data: { type: "play", samples: new Float32Array(240000).fill(0.25) } });
    p.port.onmessage({ data: { type: "play", samples: new Float32Array(128).fill(0.5) } });
    expect(p.port.postMessage).toHaveBeenCalledWith({ type: "output_overflow" });
    const output = new Float32Array(240128);
    p.process([], [[output]]);
    expect(output.every((v) => v === 0)).toBe(true);
    p.port.onmessage({ data: { type: "clear" } });
    p.port.onmessage({ data: { type: "play", samples: new Float32Array(240001).fill(0.75) } });
    p.process([[new Float32Array(128)]], [[output]]);
    expect(output.every((v) => v === 0)).toBe(true);
    expect(p.port.postMessage).toHaveBeenCalledTimes(1);
  });

  it("reports terminal overflow in a 3x delivery pattern instead of losing words", () => {
    const p = processor();
    let played = 0;
    for (let tick = 0; tick < 250; tick++) {
      for (let frame = 0; frame < 3; frame++) p.port.onmessage({ data: { type: "play", samples: new Float32Array(480).fill(0.5) } });
      const output = new Float32Array(480);
      p.process([], [[output]]);
      played += output.filter((value) => value === 0.5).length;
    }
    expect(p.port.postMessage.mock.calls.filter(([message]) => message.type === "output_overflow")).toHaveLength(1);
    expect(played).toBe(249 * 480); // The 250th tick would hold 501 frames.
  });

  it("rejects one oversized chunk without playing a partial response", () => {
    const p = processor();
    p.port.onmessage({ data: { type: "play", samples: new Float32Array(240001).fill(0.5) } });
    expect(p.port.postMessage).toHaveBeenCalledWith({ type: "output_overflow" });
    const output = new Float32Array(128);
    p.process([], [[output]]);
    expect(output.every((value) => value === 0)).toBe(true);
  });

  it("copies first-channel capture with finite RMS and a transferable buffer", () => {
    const p = processor();
    const input = new Float32Array([0.5, -0.5, NaN, Infinity]);
    p.process([[input, new Float32Array(4).fill(1)]], [[new Float32Array(4)]]);
    const [message, transfers] = p.port.postMessage.mock.calls[0];
    expect(message.type).toBe("capture");
    expect(message.samples).not.toBe(input);
    expect(message.samples).toEqual(new Float32Array([0.5, -0.5, 0, 0]));
    expect(message.rms).toBeCloseTo(Math.sqrt(0.125));
    expect(transfers).toEqual([message.samples.buffer]);
    expect(input[0]).toBe(0.5);
    p.process([], [[new Float32Array(128)]]);
    expect(p.port.postMessage).toHaveBeenCalledTimes(1);
  });
});
