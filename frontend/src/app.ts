import { CallScreen } from './components/call-screen.js';
import { CallSetupError, VoiceConnection, type VoiceConnectionHandlers } from './services/voice-connection.js';
import { MicrophoneError } from './services/microphone-permissions.js';
import { CallStateMachine, RING_DURATION_MS, RingCancelledError, RingingSequence } from './state/call-state.js';
import type { CaptureEngine, SpeechInput, SpeechOutput } from './types/audio.js';
import type { CallState, HealthResponse } from './types/call.js';
import type { AssistantState, SessionReadyMessage } from './types/websocket.js';

export interface VoiceConnectionLike {
  open(stream: MediaStream): Promise<SessionReadyMessage>;
  begin(): void;
  setMuted(muted: boolean): void;
  end(): Promise<void>;
}

export interface RingtoneLike {
  play(): Promise<void>;
  stop(): void;
}

export interface AppDeps {
  root: HTMLElement;
  fetchFn: typeof fetch;
  requestMicrophone(): Promise<MediaStream>;
  capture: CaptureEngine;
  speechInput: SpeechInput;
  speechOutput: SpeechOutput;
  ringtone: RingtoneLike;
  createConnection(handlers: VoiceConnectionHandlers): VoiceConnectionLike;
  ringDurationMs?: number;
  /** Extra time after ringing to finish connecting before giving up. */
  connectGraceMs?: number;
  endedDisplayMs?: number;
  interruptedDisplayMs?: number;
  healthIntervalMs?: number;
}

const UNSUPPORTED_SPEECH = "This browser can't recognize speech. Use Google Chrome or Microsoft Edge.";
const NO_VOICE = "This browser can't speak replies. They appear in the transcript.";

const SERVICE_NOTICES: Record<string, string> = {
  llm_unavailable: "The answering service isn't configured on the server yet.",
  llm_failed: 'The receptionist had trouble answering. Try asking again.',
  knowledge_unavailable: "Company documents couldn't be searched just now.",
};

const END_NOTICES: Record<string, string> = {
  connection_lost: 'The call dropped. Check your connection and call again.',
  max_duration: 'The call reached its time limit. Call again to keep talking.',
  setup_timeout: "The call couldn't connect in time. Try again.",
  error: 'The call ended because of a server error. Try again.',
  server_ended: 'The call was ended by the service.',
};

export class App {
  readonly machine = new CallStateMachine();
  readonly screen: CallScreen;
  private connection: VoiceConnectionLike | null = null;
  private ringing: RingingSequence | null = null;
  private generation = 0;
  private muted = false;
  private transcriptVisible = false;
  private endedTimer: ReturnType<typeof setTimeout> | null = null;
  private interruptedTimer: ReturnType<typeof setTimeout> | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private readonly unsubscribe: () => void;
  private readonly onPageHide = (): void => {
    void this.endCall();
  };

  constructor(private readonly deps: AppDeps) {
    this.screen = new CallScreen({
      onCall: () => void this.startCall(),
      onEndCall: () => void this.endCall(),
      onToggleMute: () => this.toggleMute(),
      onToggleTranscript: () => this.toggleTranscript(),
    });
    this.unsubscribe = this.machine.subscribe((state) => this.render(state));
  }

  start(): void {
    this.screen.mount(this.deps.root);
    this.render(this.machine.state);
    window.addEventListener('pagehide', this.onPageHide);
    void this.checkHealth();
    this.healthTimer = setInterval(() => {
      if (!this.machine.inCall) void this.checkHealth();
    }, this.deps.healthIntervalMs ?? 30000);
  }

  destroy(): void {
    void this.endCall();
    window.removeEventListener('pagehide', this.onPageHide);
    if (this.healthTimer !== null) clearInterval(this.healthTimer);
    this.clearTimers();
    this.unsubscribe();
    this.screen.destroy();
    void this.deps.speechOutput.close();
  }

  get state(): CallState {
    return this.machine.state;
  }

  get isMuted(): boolean {
    return this.muted;
  }

  // ------------------------------------------------------------------ call lifecycle
  async startCall(): Promise<void> {
    if (this.machine.state !== 'ready') return; // ignores double clicks
    if (!this.deps.speechInput.supported) {
      this.screen.showNotice(UNSUPPORTED_SPEECH);
      return;
    }
    this.clearTimers();
    this.screen.clearNotice();
    this.screen.transcript.clear();
    this.muted = false;
    this.screen.controls.setMuted(false);
    // Unlock audio and speech synchronously inside the click (autoplay policy).
    this.deps.speechOutput.prepare();
    this.machine.transition('initiating');
    const generation = ++this.generation;

    let stream: MediaStream;
    try {
      stream = await this.deps.requestMicrophone();
    } catch (error) {
      this.fail(error);
      return;
    }
    if (generation !== this.generation) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    void this.deps.ringtone.play();
    const ringing = new RingingSequence(this.deps.ringDurationMs ?? RING_DURATION_MS, (left) =>
      this.screen.setCountdown(left),
    );
    this.ringing = ringing;
    const connection = this.deps.createConnection(this.connectionHandlers(generation));
    this.connection = connection;
    const setup = connection.open(stream);
    setup.catch(() => undefined); // observed below; avoid unhandled rejection noise
    const setupFailure = setup.then(
      () => new Promise<never>(() => undefined),
      (error: unknown) => Promise.reject(error),
    );

    try {
      // Ring for exactly the configured duration while the session is set up in parallel.
      await Promise.race([ringing.start(), setupFailure]);
      this.deps.ringtone.stop();
      this.ringing = null;
      if (generation !== this.generation) return;
      this.screen.setStatusText('Connecting');
      await this.withTimeout(setup, this.deps.connectGraceMs ?? 10000);
      if (generation !== this.generation) return;
      this.machine.transition('connected');
      this.screen.timer.start();
      connection.begin(); // the server now greets the caller
    } catch (error) {
      ringing.cancel();
      this.deps.ringtone.stop();
      if (error instanceof RingCancelledError || generation !== this.generation) return;
      this.fail(error);
    }
  }

  async endCall(notice?: string): Promise<void> {
    if (!this.machine.inCall) return;
    this.generation += 1;
    this.ringing?.cancel();
    this.ringing = null;
    this.deps.ringtone.stop();
    this.screen.timer.stop();
    this.screen.setAudioSource(null);
    this.screen.setCallerSpeaking(false);
    const connection = this.connection;
    this.connection = null;
    if (this.machine.state === 'initiating') {
      this.machine.transition('ready');
    } else {
      this.machine.transition('ended');
      this.endedTimer = setTimeout(() => {
        this.endedTimer = null;
        if (this.machine.state === 'ended') this.machine.transition('ready');
      }, this.deps.endedDisplayMs ?? 1600);
    }
    if (notice) this.screen.showNotice(notice);
    await connection?.end();
  }

  toggleMute(): void {
    if (!this.connection || !this.machine.inCall || this.machine.state === 'initiating') return;
    this.muted = !this.muted;
    this.connection.setMuted(this.muted);
    this.screen.controls.setMuted(this.muted);
    this.render(this.machine.state);
  }

  toggleTranscript(): void {
    this.transcriptVisible = !this.transcriptVisible;
    this.screen.setTranscriptVisible(this.transcriptVisible);
  }

  // ------------------------------------------------------------------ server events
  private connectionHandlers(generation: number): VoiceConnectionHandlers {
    const live = (): boolean => generation === this.generation && this.machine.inCall;
    return {
      onReady: (message) => {
        if (!live()) return;
        if (!this.deps.speechOutput.supported) {
          this.screen.showNotice(NO_VOICE, 'info');
          if (!this.transcriptVisible) this.toggleTranscript();
        } else if (!message.capabilities.llm) {
          this.screen.showNotice(SERVICE_NOTICES.llm_unavailable, 'info');
        }
      },
      onAssistantState: (state) => {
        if (live()) this.applyAssistantState(state);
      },
      onCallerSpeaking: (active) => {
        if (live()) this.screen.setCallerSpeaking(active);
      },
      onCallerTranscript: (text, final) => {
        if (live()) this.screen.transcript.updateCaller(text, final);
      },
      onAssistantText: (responseId, text, final) => {
        if (live()) this.screen.transcript.updateAssistant(responseId, text, final);
      },
      onInterrupted: (responseId) => {
        if (!live()) return;
        this.screen.transcript.markInterrupted(responseId);
        this.machine.transition('interrupted');
        if (this.interruptedTimer !== null) clearTimeout(this.interruptedTimer);
        this.interruptedTimer = setTimeout(() => {
          this.interruptedTimer = null;
          if (this.machine.state === 'interrupted') this.machine.transition('listening');
        }, this.deps.interruptedDisplayMs ?? 450);
      },
      onServiceError: (code, message, recoverable) => {
        if (generation !== this.generation) return;
        if (!recoverable) {
          void this.endCall(SERVICE_NOTICES[code] ?? message);
          return;
        }
        const text = SERVICE_NOTICES[code] ?? (code.startsWith('speech_') ? message : undefined);
        if (text) this.screen.showNotice(text, 'info');
      },
      onEnded: (reason) => {
        if (generation !== this.generation) return;
        void this.endCall(END_NOTICES[reason]);
      },
    };
  }

  private applyAssistantState(state: AssistantState): void {
    // While "interrupted" is on screen, a listening update just confirms it.
    if (this.machine.state === 'interrupted' && state === 'listening') return;
    this.machine.transition(state);
  }

  // ------------------------------------------------------------------ rendering
  private render(state: CallState): void {
    this.screen.setState(state);
    if (this.muted && (state === 'listening' || state === 'interrupted')) this.screen.setStatusText('Muted');
    switch (state) {
      case 'listening':
      case 'interrupted':
        this.screen.setAudioSource(this.muted ? null : this.deps.capture);
        break;
      case 'speaking':
        this.screen.setAudioSource(this.deps.speechOutput);
        break;
      default:
        this.screen.setAudioSource(null);
    }
  }

  private fail(error: unknown): void {
    let message = 'The call could not be started. Try again.';
    if (error instanceof MicrophoneError) message = error.message;
    else if (error instanceof CallSetupError) message = error.error.message;
    else if (error instanceof Error && error.message === 'setup-timeout') {
      message = "The call couldn't connect in time. Try again.";
    }
    void this.endCall(message);
  }

  private withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('setup-timeout')), ms);
      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (error: unknown) => {
          clearTimeout(timer);
          reject(error);
        },
      );
    });
  }

  private clearTimers(): void {
    if (this.endedTimer !== null) clearTimeout(this.endedTimer);
    if (this.interruptedTimer !== null) clearTimeout(this.interruptedTimer);
    this.endedTimer = null;
    this.interruptedTimer = null;
  }

  private async checkHealth(): Promise<void> {
    try {
      const response = await this.deps.fetchFn('/api/health', { cache: 'no-store' });
      if (!response.ok) throw new Error(String(response.status));
      const health = (await response.json()) as HealthResponse;
      if (health.status === 'ok') {
        this.screen.setHealth('online');
      } else {
        this.screen.setHealth('degraded', 'The answering model (OpenRouter) is not configured on the server.');
      }
    } catch {
      this.screen.setHealth('offline', 'Start the backend, then reload this page.');
    }
  }
}

/** Production wiring. */
export function createDefaultConnection(
  handlers: VoiceConnectionHandlers,
  deps: { capture: CaptureEngine; speechInput: SpeechInput; speechOutput: SpeechOutput },
): VoiceConnection {
  return new VoiceConnection(handlers, {
    fetchFn: (input, init) => fetch(input, init),
    capture: deps.capture,
    speechInput: deps.speechInput,
    speechOutput: deps.speechOutput,
    location: window.location,
  });
}
