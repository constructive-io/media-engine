// The microphone as the engine listens to it: one channel of 16-bit samples, 16,000 a second,
// handed over 512 at a time. A browser records at whatever rate its sound card likes, so each
// sample sent is the mean of the ones recorded in its place.
const RATE = 16000;
const AT_A_TIME = 512;

class Microphone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.each = sampleRate / RATE;
    this.due = this.each;
    this.seen = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(AT_A_TIME);
    this.filled = 0;
  }

  process(inputs) {
    const heard = inputs[0] && inputs[0][0];
    if (!heard) return true;
    for (let i = 0; i < heard.length; i += 1) {
      this.sum += heard[i];
      this.count += 1;
      this.seen += 1;
      if (this.seen < this.due) continue;
      const mean = this.sum / this.count;
      this.out[this.filled] = Math.max(-32768, Math.min(32767, Math.round(mean * 32767)));
      this.filled += 1;
      this.sum = 0;
      this.count = 0;
      this.due += this.each;
      if (this.filled === AT_A_TIME) {
        const sent = this.out.buffer.slice(0);
        this.port.postMessage(sent, [sent]);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("microphone", Microphone);
