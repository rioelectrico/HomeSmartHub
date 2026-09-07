function sample(value: number): number {
  return Number.isNaN(value) ? 0 : Math.max(-1, Math.min(1, value));
}

/** Stateless linear interpolation. Callers own chunk boundaries and buffered audio. */
export function resampleTo24k(input: Float32Array, sourceRate: number): Float32Array {
  if (!Number.isFinite(sourceRate) || sourceRate <= 0) throw new Error("INVALID_SAMPLE_RATE");
  const length = Math.floor(input.length * 24000 / sourceRate);
  if (!Number.isSafeInteger(length) || length > 0x7fffffff) throw new Error("INVALID_SAMPLE_RATE");
  const output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const position = i * sourceRate / 24000;
    const left = Math.floor(position);
    const fraction = position - left;
    const a = sample(input[left]);
    const b = sample(input[Math.min(left + 1, input.length - 1)]);
    output[i] = a + (b - a) * fraction;
  }
  return output;
}

export function floatToPcm16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const value = sample(input[i]);
    output[i] = Math.round(value * (value < 0 ? 32768 : 32767));
  }
  return output;
}
