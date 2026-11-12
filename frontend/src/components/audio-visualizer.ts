import type { AudioLevelSource } from '../types/audio.js';

const BAR_COUNT = 64;

/** Converts an analyser's waveform RMS to a perceptual 0..1 level. */
export function rmsToLevel(samples: Uint8Array<ArrayBuffer> | Uint8Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const v = (samples[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / samples.length);
  const db = 20 * Math.log10(Math.max(rms, 1e-5));
  return Math.max(0, Math.min(1, (db + 55) / 45)); // -55 dB -> 0, -10 dB -> 1
}

/**
 * Radial spectrum ring drawn around the orb from live analyser data
 * (microphone while listening, assistant playback while speaking).
 */
export class AudioVisualizer {
  readonly element: HTMLCanvasElement;
  private source: AudioLevelSource | null = null;
  private frame: number | null = null;
  private timeData = new Uint8Array(1024);
  private freqData = new Uint8Array(512);
  private readonly bars = new Float32Array(BAR_COUNT);
  private readonly reducedMotion: boolean;
  private colors = { a: '#5ED6EA', b: '#8C7BF7' };

  constructor(private readonly onLevel: (level: number) => void) {
    this.element = document.createElement('canvas');
    this.element.className = 'visualizer';
    this.element.setAttribute('aria-hidden', 'true');
    this.reducedMotion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  setSource(source: AudioLevelSource | null): void {
    this.source = source;
    if (source && this.frame === null) this.loop();
  }

  start(): void {
    const styles = getComputedStyle(document.documentElement);
    this.colors = {
      a: styles.getPropertyValue('--cyan').trim() || this.colors.a,
      b: styles.getPropertyValue('--violet').trim() || this.colors.b,
    };
    if (this.frame === null) this.loop();
  }

  stop(): void {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.bars.fill(0);
    this.clear();
    this.onLevel(0);
  }

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop);
    const analyser = this.source?.getAnalyser() ?? null;
    let level = 0;
    if (analyser) {
      if (this.timeData.length !== analyser.fftSize) this.timeData = new Uint8Array(analyser.fftSize);
      if (this.freqData.length !== analyser.frequencyBinCount) this.freqData = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(this.timeData);
      analyser.getByteFrequencyData(this.freqData);
      level = rmsToLevel(this.timeData);
      // Speech energy lives roughly in the lowest third of the bins.
      const usable = Math.max(BAR_COUNT, Math.floor(this.freqData.length / 3));
      for (let i = 0; i < BAR_COUNT; i += 1) {
        const target = this.freqData[Math.floor((i / BAR_COUNT) * usable)] / 255;
        this.bars[i] += (target - this.bars[i]) * (target > this.bars[i] ? 0.45 : 0.1);
      }
    } else if (this.source?.getLevel) {
      // Browser speech synthesis exposes no audio samples, only real speech progress (word boundaries).
      level = this.source.getLevel();
      for (let i = 0; i < BAR_COUNT; i += 1) {
        const shape = 0.55 + 0.45 * Math.abs(Math.sin((i / BAR_COUNT) * Math.PI * 3));
        const target = level * shape;
        this.bars[i] += (target - this.bars[i]) * (target > this.bars[i] ? 0.4 : 0.12);
      }
    } else {
      for (let i = 0; i < BAR_COUNT; i += 1) this.bars[i] *= 0.88;
    }
    this.onLevel(level);
    this.draw();
  };

  private clear(): void {
    const ctx = this.element.getContext('2d');
    ctx?.clearRect(0, 0, this.element.width, this.element.height);
  }

  private draw(): void {
    const canvas = this.element;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const ratio = window.devicePixelRatio || 1;
    const size = canvas.clientWidth;
    if (size === 0) return;
    if (canvas.width !== Math.round(size * ratio)) {
      canvas.width = Math.round(size * ratio);
      canvas.height = Math.round(size * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const center = size / 2;
    const inner = size * 0.34;
    const maxLength = size * (this.reducedMotion ? 0.05 : 0.13);
    const gradient = ctx.createLinearGradient(0, 0, size, size);
    gradient.addColorStop(0, this.colors.a);
    gradient.addColorStop(1, this.colors.b);
    ctx.strokeStyle = gradient;
    ctx.lineCap = 'round';
    ctx.lineWidth = Math.max(2, size / 160);
    for (let i = 0; i < BAR_COUNT; i += 1) {
      // Mirror the spectrum so low frequencies sit at the top and bottom symmetrically.
      const value = this.bars[i < BAR_COUNT / 2 ? i * 2 : (BAR_COUNT - 1 - i) * 2];
      if (value < 0.02) continue;
      const angle = (i / BAR_COUNT) * Math.PI * 2 - Math.PI / 2;
      const length = 2 + value * maxLength;
      ctx.globalAlpha = 0.25 + value * 0.6;
      ctx.beginPath();
      ctx.moveTo(center + Math.cos(angle) * inner, center + Math.sin(angle) * inner);
      ctx.lineTo(center + Math.cos(angle) * (inner + length), center + Math.sin(angle) * (inner + length));
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
}
