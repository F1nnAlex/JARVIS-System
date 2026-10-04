// AudioWorklet that forwards microphone audio to the main thread in
// fixed-size frames (512 samples = 32 ms at 16 kHz).

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(512);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    let offset = 0;
    while (offset < channel.length) {
      const take = Math.min(channel.length - offset, this.frame.length - this.filled);
      this.frame.set(channel.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.frame.length) {
        this.port.postMessage(this.frame);
        this.frame = new Float32Array(512);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
