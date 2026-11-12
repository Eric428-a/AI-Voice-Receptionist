import type { SpeechInput, SpeechInputHandlers } from '../types/audio.js';

/**
 * Speech-to-text using the browser's built-in Web Speech API
 * (SpeechRecognition / webkitSpeechRecognition).
 *
 * Continuous mode with interim results gives live words while the caller is
 * still talking, which drives instant barge-in. Browsers end a recognition
 * session after silence or about a minute, so it is restarted automatically
 * while the call is live.
 *
 * Note: in Chrome the recognition itself is performed by the browser vendor's
 * speech service, so Chrome needs an internet connection for it.
 */

interface RecognitionAlternative {
  transcript: string;
}
interface RecognitionResult {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: RecognitionAlternative;
}
interface RecognitionResultList {
  readonly length: number;
  [index: number]: RecognitionResult;
}
export interface RecognitionResultEvent {
  readonly resultIndex: number;
  readonly results: RecognitionResultList;
}
export interface RecognitionErrorEvent {
  readonly error: string;
}

export interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}

export type RecognitionConstructor = new () => RecognitionLike;

export function findRecognitionConstructor(scope: object = globalThis): RecognitionConstructor | null {
  const candidate = scope as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  return candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition ?? null;
}

const FATAL_ERRORS: Record<string, string> = {
  'not-allowed': "Microphone access is blocked. Allow it in your browser's site settings, then call again.",
  'service-not-allowed': "This browser's speech recognition is turned off. Use Chrome or Edge, then call again.",
  'audio-capture': 'No microphone was found. Connect one and call again.',
  'language-not-supported': "This browser can't recognize the configured language.",
};

const IGNORED_ERRORS = new Set(['no-speech', 'aborted']);
const RESTART_DELAY_MS = 150;
const MAX_RESTARTS_PER_WINDOW = 8;
const RESTART_WINDOW_MS = 10000;

export class BrowserSpeechRecognition implements SpeechInput {
  private recognition: RecognitionLike | null = null;
  private handlers: SpeechInputHandlers | null = null;
  private language = 'en-US';
  private active = false;
  private muted = false;
  private inUtterance = false;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private restarts: number[] = [];

  constructor(private readonly Recognition: RecognitionConstructor | null = findRecognitionConstructor()) {}

  get supported(): boolean {
    return this.Recognition !== null;
  }

  start(language: string, handlers: SpeechInputHandlers): void {
    if (!this.Recognition) {
      handlers.onError('unsupported', "This browser can't recognize speech. Use Chrome or Edge.", true);
      return;
    }
    this.stop();
    this.language = language;
    this.handlers = handlers;
    this.active = true;
    this.restarts = [];
    if (!this.muted) this.spawn();
  }

  setMuted(muted: boolean): void {
    if (this.muted === muted) return;
    this.muted = muted;
    if (!this.active) return;
    if (muted) {
      this.inUtterance = false;
      this.abortCurrent();
    } else {
      this.spawn();
    }
  }

  stop(): void {
    this.active = false;
    this.inUtterance = false;
    this.abortCurrent();
    this.handlers = null;
    this.muted = false;
  }

  private spawn(): void {
    if (!this.Recognition || !this.active || this.muted || this.recognition) return;
    const recognition = new this.Recognition();
    recognition.lang = this.language;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => this.handleResult(event);
    recognition.onerror = (event) => this.handleError(event.error);
    recognition.onend = () => this.handleEnd(recognition);
    this.recognition = recognition;
    try {
      recognition.start();
    } catch {
      // start() throws if a previous session is still closing; retry shortly.
      this.recognition = null;
      this.scheduleRestart();
    }
  }

  private handleResult(event: RecognitionResultEvent): void {
    const handlers = this.handlers;
    if (!handlers || this.muted) return;
    let interim = '';
    let final = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const text = result[0]?.transcript ?? '';
      if (result.isFinal) final += text;
      else interim += text;
    }
    const spoken = (final + interim).trim();
    if (spoken && !this.inUtterance) {
      this.inUtterance = true;
      handlers.onSpeechStart();
    }
    if (interim.trim()) handlers.onInterim((final + interim).trim());
    if (final.trim()) {
      this.inUtterance = false;
      handlers.onFinal(final.trim());
    }
  }

  private handleError(code: string): void {
    if (IGNORED_ERRORS.has(code)) return;
    const fatal = FATAL_ERRORS[code];
    if (fatal) {
      const handlers = this.handlers;
      this.active = false;
      this.abortCurrent();
      handlers?.onError(code, fatal, true);
      return;
    }
    // e.g. "network": recoverable, the session restarts from onend.
    this.handlers?.onError(code, "Speech recognition had a problem. Keep talking; it's reconnecting.", false);
  }

  private handleEnd(recognition: RecognitionLike): void {
    if (this.recognition === recognition) this.recognition = null;
    this.inUtterance = false;
    if (this.active && !this.muted) this.scheduleRestart();
  }

  private scheduleRestart(): void {
    if (this.restartTimer !== null) return;
    const now = Date.now();
    this.restarts = this.restarts.filter((t) => now - t < RESTART_WINDOW_MS);
    this.restarts.push(now);
    if (this.restarts.length > MAX_RESTARTS_PER_WINDOW) {
      const handlers = this.handlers;
      this.active = false;
      handlers?.onError('restart-loop', 'Speech recognition keeps stopping. Check your internet connection and call again.', true);
      return;
    }
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      this.spawn();
    }, RESTART_DELAY_MS);
  }

  private abortCurrent(): void {
    if (this.restartTimer !== null) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    const recognition = this.recognition;
    this.recognition = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.abort();
      } catch {
        /* already stopped */
      }
    }
  }
}
