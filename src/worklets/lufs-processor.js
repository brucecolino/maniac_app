/* LUFS / loudness AudioWorklet — ITU-R BS.1770 (semplificato).
 *
 * Calcola in tempo reale:
 *   - momentary  (M):    400 ms gating
 *   - short-term (S):    3 s gating
 *   - integrated (I):    media gated dell'intera sessione
 *   - true peak  (TP):   approssimato come max(|x|) nel buffer
 *   - rms L/R
 *
 * Pre-filter K-weighting omesso (costoso); per uso meter visivo è sufficiente.
 * postMessage ogni ~50ms con { lufsM, lufsS, lufsI, tp, rmsL, rmsR }.
 */
class LUFSProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._sampleRate = sampleRate || 48000;
    this._frameMs = 50;
    this._frameSamples = Math.max(1, Math.round(this._sampleRate * this._frameMs / 1000));

    // Ring buffers per blocchi 100ms
    this._block100 = Math.max(1, Math.round(this._sampleRate * 0.1));
    this._meanSquares = [];     // mean-square per blocco 100ms (mono, somma canali)
    this._partial = { sum: 0, n: 0 };

    // True peak rolling window (~50 ms)
    this._peakWindow = 0;
    this._peakDecay = 0;

    // Integrated gating (assoluto -70 LUFS, relativo -10 LU)
    this._absGateLin = Math.pow(10, (-70 + 0.691) / 10);
    this._integratedSum = 0;
    this._integratedN = 0;

    this._counter = 0;
  }

  static get parameterDescriptors() { return []; }

  _meanLUFS(blocks) {
    if (!blocks.length) return -Infinity;
    const mean = blocks.reduce((a, b) => a + b, 0) / blocks.length;
    if (mean <= 0) return -Infinity;
    return -0.691 + 10 * Math.log10(mean);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const ch0 = input[0] || new Float32Array(0);
    const ch1 = input[1] || ch0;
    const N = ch0.length;
    if (!N) return true;

    let rmsL = 0, rmsR = 0, peak = 0;
    for (let i = 0; i < N; i++) {
      const l = ch0[i] || 0, r = ch1[i] || 0;
      rmsL += l * l; rmsR += r * r;
      const ms = (l * l + r * r) / 2;
      this._partial.sum += ms;
      this._partial.n += 1;
      const ap = Math.max(Math.abs(l), Math.abs(r));
      if (ap > peak) peak = ap;

      if (this._partial.n >= this._block100) {
        const blockMs = this._partial.sum / this._partial.n;
        this._meanSquares.push(blockMs);
        if (this._meanSquares.length > 30) this._meanSquares.shift(); // tieni 3 s
        // Gating per integrated
        if (blockMs >= this._absGateLin) {
          this._integratedSum += blockMs;
          this._integratedN  += 1;
        }
        this._partial.sum = 0;
        this._partial.n = 0;
      }
    }
    rmsL = Math.sqrt(rmsL / N);
    rmsR = Math.sqrt(rmsR / N);

    // peak hold con decay
    this._peakWindow = Math.max(peak, this._peakWindow * 0.92);
    const tp = this._peakWindow > 0
      ? 20 * Math.log10(this._peakWindow) : -Infinity;

    // momentary: ultimi 4 blocchi (400 ms); short-term: tutti (3 s)
    const m = this._meanSquares.slice(-4);
    const s = this._meanSquares;
    const lufsM = this._meanLUFS(m);
    const lufsS = this._meanLUFS(s);
    const lufsI = this._integratedN > 0
      ? -0.691 + 10 * Math.log10(this._integratedSum / this._integratedN)
      : -Infinity;

    this._counter++;
    if (this._counter % 5 === 0) {
      this.port.postMessage({
        lufsM, lufsS, lufsI, tp,
        rmsL: rmsL > 0 ? 20 * Math.log10(rmsL) : -Infinity,
        rmsR: rmsR > 0 ? 20 * Math.log10(rmsR) : -Infinity,
      });
    }
    return true;
  }
}

registerProcessor('lufs-processor', LUFSProcessor);
