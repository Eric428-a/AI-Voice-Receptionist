import type { SpeechOutput } from '../types/audio.js';

/** Minimal speechSynthesis surface (lets tests inject a fake). */
export interface SynthLike {
  speak(utterance: SpeechSynthesisUtterance): void;
  cancel(): void;
  getVoices(): SpeechSynthesisVoice[];
  onvoiceschanged: ((this: SpeechSynthesis, ev: Event) => unknown) | null;
}

export type UtteranceFactory = (text: string) => SpeechSynthesisUtterance;

const NATURAL_VOICE_HINTS = ['natural', 'neural', 'online', 'google', 'premium', 'enhanced'];
const SPEAKING_BASE_LEVEL = 0.22;

function defaultSynth(): SynthLike | null {
  return typeof speechSynthesis !== 'undefined' ? (speechSynthesis as SynthLike) : null;
}

function defaultUtteranceFactory(): UtteranceFactory | null {
  return typeof SpeechSynthesisUtterance !== 'undefined' ? (text) => new SpeechSynthesisUtterance(text) : null;
}

/** Pick the most natural-sounding installed voice for the language. */
export function pickVoice(voices: SpeechSynthesisVoice[], language: string): SpeechSynthesisVoice | null {
  const lang = language.toLowerCase();
  const base = lang.split('-')[0];
  const candidates = voices.filter((v) => v.lang.toLowerCase().replace('_', '-').startsWith(base));
  if (candidates.length === 0) return null;
  const score = (voice: SpeechSynthesisVoice): number => {
    const name = voice.name.toLowerCase();
    let total = 0;
    if (voice.lang.toLowerCase().replace('_', '-') === lang) total += 4;
    if (NATURAL_VOICE_HINTS.some((hint) => name.includes(hint))) total += 3;
    if (voice.default) total += 1;
    return total;
  };
  return [...candidates].sort((a, b) => score(b) - score(a))[0];
}

/**
 * Speaks the receptionist's replies with the browser's built-in speech
 * synthesis, sentence by sentence as they stream in. No audio is downloaded
 * or sent anywhere.
 */
export class SpeechPlayback implements SpeechOutput {
  private ctx: AudioContext | null = null;
  private activeId: number | null = null;
  private generation = 0;
  private pending: Array<{ text: string }> = [];
  private streamComplete = false;
  private language = 'en-US';
  private rate = 1;
  private voice: SpeechSynthesisVoice | null = null;
  private pulse = 0;
  private pulseAt = 0;
  private drainedHandler: ((responseId: number) => void) | null = null;

  constructor(
    private readonly synth: SynthLike | null = defaultSynth(),
    private readonly createUtterance: UtteranceFactory | null = defaultUtteranceFactory(),
    private readonly createAudioContext: () => AudioContext = () => new AudioContext({ latencyHint: 'interactive' }),
  ) {
    if (this.synth) {
      this.synth.onvoiceschanged = () => this.refreshVoice();
    }
  }

  get supported(): boolean {
    return this.synth !== null && this.createUtterance !== null;
  }

  get context(): AudioContext | null {
    return this.ctx;
  }

  getAnalyser(): AnalyserNode | null {
    return null; // speech synthesis output cannot be tapped by Web Audio
  }

  /** Activity level for the orb: a base level while speaking plus a pulse on every spoken word. */
  getLevel(): number {
    if (this.pending.length === 0) return 0;
    const age = (Date.now() - this.pulseAt) / 1000;
    return Math.min(1, SPEAKING_BASE_LEVEL + this.pulse * Math.exp(-age * 7));
  }

  setDrainedHandler(handler: ((responseId: number) => void) | null): void {
    this.drainedHandler = handler;
  }

  prepare(): void {
    try {
      if (!this.ctx || this.ctx.state === 'closed') this.ctx = this.createAudioContext();
      void this.ctx.resume();
    } catch {
      this.ctx = null; // ring tone simply stays silent
    }
    if (!this.supported) return;
    this.synth?.cancel();
    this.refreshVoice();
    // Some mobile browsers only allow speech that was first started from a tap.
    const unlock = this.createUtterance?.(' ');
    if (unlock) {
      unlock.volume = 0;
      this.synth?.speak(unlock);
    }
  }

  configure(language: string, rate: number): void {
    this.language = language;
    this.rate = rate;
    this.refreshVoice();
  }

  beginResponse(responseId: number): void {
    this.stop();
    this.activeId = responseId;
  }

  speak(responseId: number, text: string): void {
    if (!this.supported || responseId !== this.activeId || !text.trim()) return;
    const generation = this.generation;
    const entry = { text };
    this.pending.push(entry);
    const utterance = this.createUtterance!(text);
    utterance.lang = this.language;
    utterance.rate = this.rate;
    if (this.voice) utterance.voice = this.voice;
    const bump = (): void => {
      if (generation !== this.generation) return;
      this.pulse = 0.55;
      this.pulseAt = Date.now();
    };
    const finished = (): void => {
      if (generation !== this.generation) return;
      this.pending = this.pending.filter((item) => item !== entry);
      this.checkDrained();
    };
    utterance.onstart = bump;
    utterance.onboundary = bump;
    utterance.onend = finished;
    utterance.onerror = finished;
    this.synth!.speak(utterance);
  }

  completeResponse(responseId: number): void {
    if (responseId !== this.activeId) return;
    this.streamComplete = true;
    this.checkDrained();
  }

  /** Stop talking immediately (barge-in, hang-up). */
  stop(): void {
    this.generation += 1;
    this.pending = [];
    this.activeId = null;
    this.streamComplete = false;
    this.synth?.cancel();
  }

  isSpeaking(): boolean {
    return this.pending.length > 0;
  }

  currentText(): string {
    return this.pending.map((item) => item.text).join(' ');
  }

  async close(): Promise<void> {
    this.stop();
    this.drainedHandler = null;
    const ctx = this.ctx;
    this.ctx = null;
    if (ctx && ctx.state !== 'closed') {
      try {
        await ctx.close();
      } catch {
        /* ignore */
      }
    }
  }

  private refreshVoice(): void {
    if (!this.synth) return;
    this.voice = pickVoice(this.synth.getVoices(), this.language);
  }

  private checkDrained(): void {
    if (this.activeId !== null && this.streamComplete && this.pending.length === 0) {
      const id = this.activeId;
      this.activeId = null;
      this.streamComplete = false;
      this.drainedHandler?.(id);
    }
  }
}

/** Plays the ring tone (bundled MP3, synthesized fallback) on an unlocked AudioContext. */
export class Ringtone {
  private node: AudioScheduledSourceNode | null = null;
  private extraNodes: AudioNode[] = [];
  private cached: AudioBuffer | null = null;
  private playing = false;

  constructor(
    private readonly getContext: () => AudioContext | null,
    private readonly url: string = '/assets/sounds/ringing.mp3',
    private readonly volume: number = 0.45,
  ) {}

  async play(): Promise<void> {
    const ctx = this.getContext();
    if (!ctx) return;
    this.stop();
    this.playing = true;
    try {
      if (!this.cached) {
        const response = await fetch(this.url);
        if (!response.ok) throw new Error(`ring tone HTTP ${response.status}`);
        this.cached = await ctx.decodeAudioData(await response.arrayBuffer());
      }
      if (!this.playing) return;
      const source = ctx.createBufferSource();
      source.buffer = this.cached;
      const gain = ctx.createGain();
      gain.gain.value = this.volume;
      source.connect(gain).connect(ctx.destination);
      source.start();
      this.node = source;
      this.extraNodes = [gain];
    } catch {
      if (this.playing) this.synthesize(ctx);
    }
  }

  stop(): void {
    this.playing = false;
    if (this.node) {
      try {
        this.node.stop();
      } catch {
        /* not started */
      }
      this.node.disconnect();
    }
    for (const extra of this.extraNodes) {
      if ('stop' in extra && typeof extra.stop === 'function') {
        try {
          (extra as AudioScheduledSourceNode).stop();
        } catch {
          /* not started */
        }
      }
      extra.disconnect();
    }
    this.node = null;
    this.extraNodes = [];
  }

  /** 440 + 480 Hz ring cadence (two rings in five seconds). */
  private synthesize(ctx: AudioContext): void {
    const gain = ctx.createGain();
    const t0 = ctx.currentTime;
    gain.gain.setValueAtTime(0, t0);
    for (const start of [0, 2.5]) {
      gain.gain.setValueAtTime(0, t0 + start);
      gain.gain.linearRampToValueAtTime(this.volume * 0.25, t0 + start + 0.04);
      gain.gain.setValueAtTime(this.volume * 0.25, t0 + start + 1.1);
      gain.gain.linearRampToValueAtTime(0, t0 + start + 1.2);
    }
    const a = ctx.createOscillator();
    const b = ctx.createOscillator();
    a.frequency.value = 440;
    b.frequency.value = 480;
    a.connect(gain);
    b.connect(gain);
    gain.connect(ctx.destination);
    a.start(t0);
    b.start(t0);
    a.stop(t0 + 5);
    b.stop(t0 + 5);
    this.node = a;
    this.extraNodes = [b, gain];
  }
}
