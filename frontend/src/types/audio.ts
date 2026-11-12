/** Shared types for microphone level metering and browser speech. */

export interface AudioLevelSource {
  /** Analyser node for visualization, or null when inactive. */
  getAnalyser(): AnalyserNode | null;
  /** Optional 0..1 activity level when no analyser exists (browser speech synthesis). */
  getLevel?(): number;
}

export interface CaptureOptions {
  targetSampleRate: number;
  frameMs: number;
}

export const DEFAULT_CAPTURE_OPTIONS: CaptureOptions = {
  targetSampleRate: 16000,
  frameMs: 20,
};

/** Microphone level meter for the orb (audio never leaves the browser). */
export interface CaptureEngine extends AudioLevelSource {
  start(stream: MediaStream, onFrame: (frame: ArrayBuffer) => void): Promise<void>;
  setMuted(muted: boolean): void;
  readonly muted: boolean;
  stop(): Promise<void>;
}

export interface SpeechInputHandlers {
  /** The caller started a new utterance (first interim words). */
  onSpeechStart(): void;
  /** Live, not-yet-final words of the current utterance. */
  onInterim(text: string): void;
  /** Final transcript of the utterance. */
  onFinal(text: string): void;
  /** Recognition problem; fatal ones mean recognition has stopped. */
  onError(code: string, message: string, fatal: boolean): void;
}

/** Browser speech-to-text. */
export interface SpeechInput {
  readonly supported: boolean;
  start(language: string, handlers: SpeechInputHandlers): void;
  setMuted(muted: boolean): void;
  stop(): void;
}

/** Browser text-to-speech for the receptionist's replies. */
export interface SpeechOutput extends AudioLevelSource {
  readonly supported: boolean;
  /** Unlocks audio; call synchronously inside the Call click. */
  prepare(): void;
  /** AudioContext used for the ring tone. */
  readonly context: AudioContext | null;
  configure(language: string, rate: number): void;
  beginResponse(responseId: number): void;
  speak(responseId: number, text: string): void;
  completeResponse(responseId: number): void;
  stop(): void;
  isSpeaking(): boolean;
  /** Text currently queued or being spoken (used to filter speaker echo). */
  currentText(): string;
  setDrainedHandler(handler: ((responseId: number) => void) | null): void;
  close(): Promise<void>;
}
