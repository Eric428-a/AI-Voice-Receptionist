import type { CaptureEngine, SpeechInput, SpeechOutput } from '../types/audio.js';
import type { CallError, CreateCallResponse } from '../types/call.js';
import type { AssistantState, ServerMessage, SessionReadyMessage } from '../types/websocket.js';
import { WebSocketClient, type WebSocketFactory } from './websocket-client.js';

export interface VoiceConnectionHandlers {
  onReady(message: SessionReadyMessage): void;
  onAssistantState(state: AssistantState): void;
  onCallerSpeaking(active: boolean): void;
  /** Live (interim) and final words of the caller, recognized in the browser. */
  onCallerTranscript(text: string, final: boolean): void;
  onAssistantText(responseId: number, text: string, final: boolean): void;
  onInterrupted(responseId: number): void;
  onServiceError(code: string, message: string, recoverable: boolean): void;
  onEnded(reason: string): void;
}

export interface VoiceConnectionDeps {
  fetchFn: typeof fetch;
  createSocket?: WebSocketFactory;
  /** Microphone level meter for the orb; audio never leaves the browser. */
  capture: CaptureEngine;
  speechInput: SpeechInput;
  speechOutput: SpeechOutput;
  location: Pick<Location, 'protocol' | 'host'>;
  readyTimeoutMs?: number;
  now?: () => number;
}

export class CallSetupError extends Error {
  constructor(readonly error: CallError) {
    super(error.message);
    this.name = 'CallSetupError';
  }
}

/** Words needed before speech heard during a reply counts as an interruption. */
export const MIN_BARGE_IN_WORDS = 2;
/** After the assistant stops talking, its words may still arrive from the recognizer. */
export const ECHO_TAIL_MS = 2000;

function wordsOf(text: string): string[] {
  return text.toLowerCase().match(/[\p{L}\p{N}']+/gu) ?? [];
}

/**
 * True when what the microphone "heard" is mostly the assistant's own words
 * coming back from the speakers (echo), not the caller.
 */
export function isLikelyEcho(heard: string, spoken: string): boolean {
  const heardWords = wordsOf(heard);
  if (heardWords.length === 0) return true;
  const spokenWords = new Set(wordsOf(spoken));
  if (spokenWords.size === 0) return false;
  const overlap = heardWords.filter((word) => spokenWords.has(word)).length / heardWords.length;
  return overlap >= 0.6;
}

/** One live call: REST session, text WebSocket, browser speech in and out. */
export class VoiceConnection {
  private client: WebSocketClient | null = null;
  private callId: string | null = null;
  private language = 'en-US';
  private started = false;
  private ended = false;
  private muted = false;
  private callerSpeaking = false;
  private currentReplyText = '';
  private recentReplyText = '';
  private recentReplyUntil = 0;
  private readyResolver: ((message: SessionReadyMessage) => void) | null = null;
  private readyRejecter: ((error: Error) => void) | null = null;

  constructor(
    private readonly handlers: VoiceConnectionHandlers,
    private readonly deps: VoiceConnectionDeps,
  ) {}

  get id(): string | null {
    return this.callId;
  }

  private now(): number {
    return (this.deps.now ?? Date.now)();
  }

  /** Create the backend session, open and authenticate the socket, start the level meter. */
  async open(stream: MediaStream): Promise<SessionReadyMessage> {
    const call = await this.createCall();
    if (this.ended) throw new CallSetupError({ kind: 'service', message: 'Call cancelled.' });
    this.callId = call.call_id;

    const scheme = this.deps.location.protocol === 'https:' ? 'wss' : 'ws';
    const client = new WebSocketClient(
      `${scheme}://${this.deps.location.host}${call.websocket_path}`,
      {
        onMessage: (message) => this.handleMessage(message),
        onBinary: () => undefined, // the protocol is text only
        onClose: () => this.handleUnexpectedClose(),
      },
      this.deps.createSocket,
    );
    this.client = client;
    try {
      await client.connect();
    } catch {
      throw new CallSetupError({
        kind: 'server-unreachable',
        message: "The call service isn't reachable. Check that the backend is running, then try again.",
      });
    }
    const ready = new Promise<SessionReadyMessage>((resolve, reject) => {
      this.readyResolver = resolve;
      this.readyRejecter = reject;
    });
    const timeout = setTimeout(
      () => this.readyRejecter?.(new CallSetupError({ kind: 'setup-timeout', message: "The call couldn't connect in time. Try again." })),
      this.deps.readyTimeoutMs ?? 8000,
    );
    client.send({ type: 'session.auth', token: call.session_token });
    try {
      const message = await ready;
      this.language = message.speech.language;
      this.deps.speechOutput.configure(message.speech.language, message.speech.rate);
      this.deps.speechOutput.setDrainedHandler((responseId) => this.handleDrained(responseId));
      await this.deps.capture.start(stream, () => undefined);
      return message;
    } finally {
      clearTimeout(timeout);
      this.readyResolver = null;
      this.readyRejecter = null;
    }
  }

  /** Called when ringing ends: the server greets the caller and listening begins. */
  begin(): void {
    if (this.started || this.ended) return;
    this.started = true;
    this.client?.send({ type: 'session.start' });
    this.deps.speechInput.start(this.language, {
      onSpeechStart: () => undefined, // wait for words so echo can be told apart
      onInterim: (text) => this.handleCallerWords(text, false),
      onFinal: (text) => this.handleCallerWords(text, true),
      onError: (code, message, fatal) => this.handlers.onServiceError(`speech_${code}`, message, !fatal),
    });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.deps.capture.setMuted(muted);
    this.deps.speechInput.setMuted(muted);
    if (muted && this.callerSpeaking) {
      this.callerSpeaking = false;
      this.handlers.onCallerSpeaking(false);
    }
  }

  /** Hang up and release every resource. Safe to call more than once. */
  async end(): Promise<void> {
    if (this.ended) return;
    this.ended = true;
    this.readyRejecter?.(new CallSetupError({ kind: 'service', message: 'Call cancelled.' }));
    this.deps.speechInput.stop();
    this.deps.speechOutput.stop();
    this.deps.speechOutput.setDrainedHandler(null);
    this.client?.send({ type: 'session.end' });
    this.client?.close();
    this.client = null;
    await this.deps.capture.stop();
    if (this.callId) {
      const id = this.callId;
      this.callId = null;
      // The socket close already ends the session; this covers a socket that never opened.
      void this.deps.fetchFn(`/api/calls/${id}`, { method: 'DELETE', keepalive: true }).catch(() => undefined);
    }
  }

  // ------------------------------------------------------------------ caller speech
  private echoReference(): string {
    const output = this.deps.speechOutput;
    if (output.isSpeaking()) return `${this.currentReplyText} ${output.currentText()}`;
    return this.now() < this.recentReplyUntil ? this.recentReplyText : '';
  }

  private handleCallerWords(text: string, final: boolean): void {
    if (this.ended || this.muted || !this.started) return;
    const words = text.trim();
    if (!words) return;
    const reference = this.echoReference();
    if (reference && isLikelyEcho(words, reference)) return; // the assistant hearing itself
    const assistantTalking = this.deps.speechOutput.isSpeaking();
    if (assistantTalking && !final && wordsOf(words).length < MIN_BARGE_IN_WORDS) return;

    if (!this.callerSpeaking) {
      this.callerSpeaking = true;
      this.deps.speechOutput.stop(); // silence the assistant instantly (barge-in)
      this.client?.send({ type: 'user.speaking' });
      this.handlers.onCallerSpeaking(true);
    }
    this.handlers.onCallerTranscript(words, final);
    if (final) {
      this.callerSpeaking = false;
      this.handlers.onCallerSpeaking(false);
      this.client?.send({ type: 'user.text', text: words });
    }
  }

  private handleDrained(responseId: number): void {
    this.recentReplyText = this.currentReplyText;
    this.recentReplyUntil = this.now() + ECHO_TAIL_MS;
    this.client?.send({ type: 'playback.finished', response_id: responseId });
  }

  // ------------------------------------------------------------------ server messages
  private async createCall(): Promise<CreateCallResponse> {
    let response: Response;
    try {
      response = await this.deps.fetchFn('/api/calls', { method: 'POST' });
    } catch {
      throw new CallSetupError({
        kind: 'server-unreachable',
        message: "The call service isn't reachable. Check that the backend is running, then try again.",
      });
    }
    if (response.status === 503) {
      throw new CallSetupError({ kind: 'lines-busy', message: 'All lines are busy. Try again in a minute.' });
    }
    if (!response.ok) {
      throw new CallSetupError({ kind: 'service', message: `The call could not be created (HTTP ${response.status}).` });
    }
    return (await response.json()) as CreateCallResponse;
  }

  private handleMessage(message: ServerMessage): void {
    if (this.ended) return;
    const output = this.deps.speechOutput;
    switch (message.type) {
      case 'session.ready':
        this.readyResolver?.(message);
        this.handlers.onReady(message);
        break;
      case 'state':
        this.handlers.onAssistantState(message.state);
        break;
      case 'response.start':
        this.currentReplyText = '';
        output.beginResponse(message.response_id);
        break;
      case 'response.sentence':
        this.currentReplyText = `${this.currentReplyText} ${message.text}`.trim();
        output.speak(message.response_id, message.text);
        break;
      case 'response.text.delta':
        this.handlers.onAssistantText(message.response_id, message.text, false);
        break;
      case 'response.text.done':
        this.handlers.onAssistantText(message.response_id, message.text, true);
        output.completeResponse(message.response_id);
        break;
      case 'response.interrupted':
        output.stop();
        this.handlers.onInterrupted(message.response_id);
        break;
      case 'error':
        if (message.code === 'unauthorized') {
          this.readyRejecter?.(new CallSetupError({ kind: 'service', message: 'The call could not be authorized. Try again.' }));
        }
        this.handlers.onServiceError(message.code, message.message, message.recoverable);
        break;
      case 'session.ended':
        this.handlers.onEnded(message.reason);
        break;
      case 'pong':
        break;
    }
  }

  private handleUnexpectedClose(): void {
    if (this.ended) return;
    this.readyRejecter?.(
      new CallSetupError({ kind: 'connection-lost', message: 'The call dropped. Check your connection and call again.' }),
    );
    this.handlers.onEnded('connection_lost');
  }
}
