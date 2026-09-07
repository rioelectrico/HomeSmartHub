import { floatToPcm16 } from "./audio-codec";

type OnFrame = (pcm16: Int16Array) => void;
type OnLevel = (rms: number) => void;
type OnError = (code: "AUDIO_OUTPUT_OVERFLOW") => void;

/** Linear streaming interpolation. Phase is measured in target-rate ticks, so
 * integer hardware rates (including 44100) never accumulate fractional drift.
 * Only one boundary sample is retained; there is no growing input buffer.
 * The last fractional output waits for the next real sample, never extrapolates.
 */
class StreamingResampler {
  private phase = 0;
  private previous = 0;

  constructor(private sourceRate: number, private targetRate: number) {
    if (![sourceRate, targetRate].every((rate) => Number.isFinite(rate) && rate >= 8000 && rate <= 384000)) {
      throw new Error("INVALID_SAMPLE_RATE");
    }
  }

  push(input: Float32Array, emit: (value: number) => boolean): void {
    for (const raw of input) {
      const current = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0;
      while (this.phase <= 0) {
        const fraction = 1 + this.phase / this.targetRate;
        const value = this.previous + (current - this.previous) * fraction;
        this.phase += this.sourceRate;
        if (!emit(value)) return;
      }
      this.previous = current;
      this.phase -= this.targetRate;
    }
  }
}

interface Run {
  cancelled: boolean;
  ready: boolean;
  started?: Promise<void>;
  disposed?: Promise<void>;
  waitForClose?: Promise<void>;
  stream?: MediaStream;
  context?: AudioContext;
  source?: MediaStreamAudioSourceNode;
  node?: AudioWorkletNode;
  capture?: StreamingResampler;
  playback?: StreamingResampler;
  frame: Float32Array;
  used: number;
  onFrame?: OnFrame;
  onLevel?: OnLevel;
  onError?: OnError;
  pendingPlayback: number;
  playbackEpoch: number;
  pendingByEpoch: Map<number, number>;
}

function safely(action: () => void): void {
  try { action(); } catch { /* A consumer or already-disconnected resource cannot interrupt audio cleanup. */ }
}

/** Browser-only resources are acquired in start(), making imports safe for SSR.
 * Repeated start shares the active attempt. close cancels synchronously, returns
 * after owned resources close, and stops any late permission result on arrival.
 * A cancelled start resolves without reactivating the session. Restarts wait for
 * prior context cleanup and can themselves be cancelled while waiting.
 */
export class PorteroAudioSession {
  private run?: Run;
  private closing?: Promise<void>;

  start(onFrame: OnFrame, onLevel: OnLevel, onError?: OnError): Promise<void> {
    if (this.run) return this.run.started!;
    const run: Run = { cancelled: false, ready: false, frame: new Float32Array(480), used: 0, onFrame, onLevel, onError, pendingPlayback: 0, playbackEpoch: 0, pendingByEpoch: new Map(), waitForClose: this.closing };
    this.run = run;
    run.started = run.waitForClose
      ? run.waitForClose.then(() => { if (!run.cancelled) return this.initialize(run); })
      : this.initialize(run);
    return run.started;
  }

  private async initialize(run: Run): Promise<void> {
    try {
      if (typeof navigator === "undefined" || typeof navigator.mediaDevices?.getUserMedia !== "function"
        || typeof AudioContext === "undefined" || typeof AudioWorkletNode === "undefined") {
        throw new Error("BROWSER_AUDIO_UNAVAILABLE");
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      if (run.cancelled) {
        for (const track of stream.getTracks()) safely(() => track.stop());
        return;
      }
      run.stream = stream;
      const context = new AudioContext();
      run.context = context;
      run.capture = new StreamingResampler(context.sampleRate, 24000);
      run.playback = new StreamingResampler(24000, context.sampleRate);
      if (typeof context.audioWorklet?.addModule !== "function") throw new Error("AUDIO_WORKLET_UNAVAILABLE");
      await context.audioWorklet.addModule("/portero-audio-worklet.js");
      if (run.cancelled) return;
      const node = new AudioWorkletNode(context, "portero-audio", {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: "explicit",
      });
      run.node = node;
      node.port.onmessage = ({ data }: MessageEvent) => {
        if (run.cancelled || !run.ready) return;
        if (data?.type === "output_overflow") { this.outputOverflow(run); return; }
        if (data?.type === "played" || data?.type === "cleared") {
          const pending = run.pendingByEpoch.get(data.epoch) ?? 0;
          if (Number.isSafeInteger(data.samples) && data.samples > 0 && data.samples <= pending) {
            run.pendingPlayback -= data.samples;
            if (data.samples === pending) run.pendingByEpoch.delete(data.epoch);
            else run.pendingByEpoch.set(data.epoch, pending - data.samples);
          }
          return;
        }
        if (data?.type !== "capture" || !(data.samples instanceof Float32Array)) return;
        const rms = Number.isFinite(data.rms) ? Math.max(0, Math.min(1, data.rms)) : 0;
        safely(() => run.onLevel?.(rms));
        if (run.cancelled) return;
        run.capture?.push(data.samples, (value) => {
          if (run.cancelled) return false;
          run.frame[run.used++] = value;
          if (run.used === 480) {
            const frame = floatToPcm16(run.frame);
            run.used = 0;
            safely(() => run.onFrame?.(frame));
          }
          return !run.cancelled;
        });
      };
      run.source = context.createMediaStreamSource(stream);
      run.source.connect(node);
      node.connect(context.destination);
      if (context.state === "suspended") await context.resume();
      if (!run.cancelled) run.ready = true;
    } catch (error) {
      if (run.cancelled) return;
      if (this.run === run) this.run = undefined;
      await this.trackCleanup(run);
      throw error;
    }
  }

  /** Network bytes are explicitly PCM16 little endian; Int16Array is already
   * decoded by the transport. Resample 24k playback to the actual hardware rate.
   */
  enqueuePlayback(pcm16: Int16Array | Uint8Array): void {
    if (!(pcm16 instanceof Int16Array) && !(pcm16 instanceof Uint8Array)) throw new Error("INVALID_PCM16");
    if (pcm16 instanceof Uint8Array && pcm16.byteLength % 2) throw new Error("INVALID_PCM16");
    const run = this.run;
    if (!run?.ready || !run.node || !run.playback) return;
    const length = pcm16 instanceof Int16Array ? pcm16.length : pcm16.byteLength / 2;
    // Bound conversion and the MessagePort backlog, not just the worklet ring.
    if (length > 240000) { this.outputOverflow(run); return; }
    const floats = new Float32Array(length);
    const bytes = new DataView(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
    for (let i = 0; i < length; i++) {
      floats[i] = (pcm16 instanceof Int16Array ? pcm16[i] : bytes.getInt16(i * 2, true)) / 32768;
    }
    const output = new Float32Array(Math.ceil(floats.length * run.context!.sampleRate / 24000) + 1);
    let used = 0;
    run.playback!.push(floats, (value) => { output[used++] = value; return true; });
    if (used) {
      if (run.pendingPlayback + used > Math.ceil(run.context!.sampleRate * 10)) { this.outputOverflow(run); return; }
      run.pendingPlayback += used;
      run.pendingByEpoch.set(run.playbackEpoch, (run.pendingByEpoch.get(run.playbackEpoch) ?? 0) + used);
      const samples = output.slice(0, used);
      run.node.port.postMessage({ type: "play", samples }, [samples.buffer]);
    }
  }

  clearPlayback(): void {
    const run = this.run;
    if (!run?.node || run.cancelled) return;
    // A posted clear has not freed MessagePort memory yet. Its acknowledgement
    // releases only the old epoch; it cannot release newly accepted samples.
    run.node.port.postMessage({ type: "clear", epoch: ++run.playbackEpoch });
    run.playback = new StreamingResampler(24000, run.context!.sampleRate);
  }

  private outputOverflow(run: Run): void {
    if (this.run !== run || run.cancelled) return;
    const notify = run.onError;
    // Revoke capture/playback synchronously before notifying a consumer that
    // may immediately close or try to restart the audio session.
    this.run = undefined;
    void this.trackCleanup(run);
    safely(() => notify?.("AUDIO_OUTPUT_OVERFLOW"));
  }

  close(): Promise<void> {
    const run = this.run;
    if (!run) return this.closing ?? Promise.resolve();
    this.run = undefined;
    return this.trackCleanup(run);
  }

  private trackCleanup(run: Run): Promise<void> {
    const closing = this.dispose(run);
    this.closing = closing;
    void closing.then(() => {
      // An older cleanup must never erase a newer run's cleanup barrier.
      if (this.closing === closing) this.closing = undefined;
    });
    return closing;
  }

  private dispose(run: Run): Promise<void> {
    if (run.disposed) return run.disposed;
    run.cancelled = true;
    run.ready = false;
    run.onFrame = undefined;
    run.onLevel = undefined;
    run.onError = undefined;
    run.pendingPlayback = 0;
    run.pendingByEpoch.clear();
    run.capture = undefined;
    run.playback = undefined;
    run.frame = new Float32Array(0);
    run.used = 0;
    if (run.node) {
      run.node.port.onmessage = null;
      run.node.onprocessorerror = null;
      safely(() => run.node!.port.postMessage({ type: "clear" }));
      safely(() => run.node!.port.close());
      safely(() => run.node!.disconnect());
    }
    if (run.source) safely(() => run.source!.disconnect());
    if (run.stream) for (const track of run.stream.getTracks()) safely(() => track.stop());
    try {
      run.disposed = run.context ? Promise.resolve(run.context.close()).catch(() => {}) : Promise.resolve();
    } catch {
      run.disposed = Promise.resolve();
    }
    if (run.waitForClose) {
      run.disposed = Promise.all([run.waitForClose, run.disposed]).then(() => {});
    }
    return run.disposed;
  }
}
