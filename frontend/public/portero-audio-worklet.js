/* global AudioWorkletProcessor, registerProcessor, sampleRate */

class PorteroAudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // Fixed ten-second ring. Saturation is terminal for this processor/run;
    // accepted speech is never silently replaced with a newer fragment.
    this.playback = new Float32Array(Math.ceil(sampleRate * 10));
    this.head = 0;
    this.size = 0;
    this.epoch = 0;
    this.failed = false;
    this.port.onmessage = ({ data }) => {
      if (data?.type === "clear") {
        if (this.size) this.port.postMessage({ type: "cleared", epoch: this.epoch, samples: this.size });
        this.head = 0;
        this.size = 0;
        this.playback.fill(0);
        this.epoch = data.epoch ?? this.epoch;
      } else if (!this.failed && data?.type === "play" && data.samples instanceof Float32Array) {
        const samples = data.samples;
        const capacity = this.playback.length;
        if (samples.length > capacity - this.size) {
          this.failed = true;
          this.head = 0;
          this.size = 0;
          this.playback.fill(0);
          this.port.postMessage({ type: "output_overflow" });
          return;
        }
        for (let i = 0; i < samples.length; i++) {
          this.playback[(this.head + this.size) % capacity] = Number.isFinite(samples[i]) ? samples[i] : 0;
          this.size++;
        }
      }
    };
  }

  process(inputs, outputs) {
    const output = outputs[0]?.[0];
    if (output) {
      output.fill(0);
      const played = Math.min(output.length, this.size);
      for (let i = 0; i < output.length && this.size > 0; i++) {
        output[i] = this.playback[this.head];
        this.playback[this.head] = 0;
        this.head = (this.head + 1) % this.playback.length;
        this.size--;
      }
      if (played) this.port.postMessage({ type: "played", epoch: this.epoch, samples: played });
    }
    const input = inputs[0]?.[0];
    if (!this.failed && input?.length) {
      const samples = new Float32Array(input.length);
      let energy = 0;
      for (let i = 0; i < input.length; i++) {
        const value = Number.isFinite(input[i]) ? Math.max(-1, Math.min(1, input[i])) : 0;
        samples[i] = value;
        energy += value * value;
      }
      this.port.postMessage({ type: "capture", samples, rms: Math.sqrt(energy / input.length) }, [samples.buffer]);
    }
    return true;
  }
}

registerProcessor("portero-audio", PorteroAudioProcessor);
