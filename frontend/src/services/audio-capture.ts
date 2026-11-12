import type { CaptureEngine, CaptureOptions } from '../types/audio.js';
import { DEFAULT_CAPTURE_OPTIONS } from '../types/audio.js';

/**
 * AudioWorklet that box-filter-decimates the microphone to the target rate
 * (works for any ratio, e.g. 48k or 44.1k -> 16k) and posts PCM16 frames.
 */
const WORKLET_SOURCE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = options.processorOptions;
    this.ratio = sampleRate / opts.targetSampleRate;
    this.frameSamples = opts.frameSamples;
    this.frame = new Int16Array(this.frameSamples);
    this.index = 0;
    this.acc = 0;
    this.count = 0;
    this.pos = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.count += 1;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const sample = Math.max(-1, Math.min(1, this.acc / this.count));
        this.acc = 0;
        this.count = 0;
        this.frame[this.index++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        if (this.index === this.frameSamples) {
          const out = this.frame;
          this.port.postMessage(out.buffer, [out.buffer]);
          this.frame = new Int16Array(this.frameSamples);
          this.index = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('haumex-pcm-capture', PcmCaptureProcessor);
`;

export type AudioContextFactory = () => AudioContext;

export class AudioCapture implements CaptureEngine {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private moduleUrl: string | null = null;
  private isMuted = false;

  constructor(
    private readonly options: CaptureOptions = DEFAULT_CAPTURE_OPTIONS,
    private readonly createContext: AudioContextFactory = () => new AudioContext({ latencyHint: 'interactive' }),
  ) {}

  get muted(): boolean {
    return this.isMuted;
  }

  getAnalyser(): AnalyserNode | null {
    return this.isMuted ? null : this.analyser;
  }

  async start(stream: MediaStream, onFrame: (frame: ArrayBuffer) => void): Promise<void> {
    await this.stop();
    if (typeof AudioWorkletNode === 'undefined') {
      throw new Error('AudioWorklet is not supported in this browser.');
    }
    this.stream = stream;
    const context = this.createContext();
    this.context = context;
    if (context.state === 'suspended') await context.resume();

    this.moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }));
    await context.audioWorklet.addModule(this.moduleUrl);

    this.source = context.createMediaStreamSource(stream);
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.6;

    const frameSamples = Math.round((this.options.targetSampleRate * this.options.frameMs) / 1000);
    this.worklet = new AudioWorkletNode(context, 'haumex-pcm-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      processorOptions: { targetSampleRate: this.options.targetSampleRate, frameSamples },
    });
    this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (!this.isMuted) onFrame(event.data);
    };
    // A silent sink keeps the worklet pulled by the graph without audible output.
    this.sink = context.createGain();
    this.sink.gain.value = 0;
    this.source.connect(this.analyser);
    this.source.connect(this.worklet);
    this.worklet.connect(this.sink);
    this.sink.connect(context.destination);
  }

  setMuted(muted: boolean): void {
    this.isMuted = muted;
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
  }

  async stop(): Promise<void> {
    if (this.worklet) {
      this.worklet.port.onmessage = null;
      this.worklet.disconnect();
    }
    this.source?.disconnect();
    this.analyser?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.moduleUrl) URL.revokeObjectURL(this.moduleUrl);
    const context = this.context;
    this.worklet = null;
    this.source = null;
    this.analyser = null;
    this.sink = null;
    this.stream = null;
    this.moduleUrl = null;
    this.context = null;
    this.isMuted = false;
    if (context && context.state !== 'closed') {
      try {
        await context.close();
      } catch {
        /* already closing */
      }
    }
  }
}
