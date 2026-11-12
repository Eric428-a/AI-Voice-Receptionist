import assert from 'node:assert/strict';
import { mock } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import type { WebSocketLike } from '../src/services/websocket-client.js';
import type { CaptureEngine, SpeechInput, SpeechInputHandlers, SpeechOutput } from '../src/types/audio.js';

// ---------------------------------------------------------------- mock helpers
interface Recorded {
  mock: { calls: Array<{ arguments: unknown[] }>; callCount(): number };
}

export function argsOf(fn: Recorded): unknown[][] {
  return fn.mock.calls.map((call) => call.arguments);
}

export function callCount(fn: Recorded): number {
  return fn.mock.callCount();
}

export function assertCalledWith(fn: Recorded, ...expected: unknown[]): void {
  const all = argsOf(fn);
  assert.ok(
    all.some((args) => isDeepStrictEqual(args, expected)),
    `expected a call with ${JSON.stringify(expected)}, got ${JSON.stringify(all)}`,
  );
}

const nextTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

/** Let pending promises settle (setImmediate is never mocked). */
export async function flush(turns = 3): Promise<void> {
  for (let i = 0; i < turns; i += 1) await nextTurn();
}

export function useFakeTimers(): void {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 0 });
}

export function useRealTimers(): void {
  mock.timers.reset();
}

/** Advance mocked time in small steps, settling promises between steps. */
export async function advance(ms: number, step = 5): Promise<void> {
  await flush(1);
  let elapsed = 0;
  while (elapsed < ms) {
    const delta = Math.min(step, ms - elapsed);
    mock.timers.tick(delta);
    elapsed += delta;
    await flush(1);
  }
  await flush();
}

export async function waitFor(check: () => void, turns = 200): Promise<void> {
  for (let i = 0; i < turns; i += 1) {
    try {
      check();
      return;
    } catch {
      await nextTurn();
    }
  }
  check();
}

// ---------------------------------------------------------------- fakes
export class FakeSocket implements WebSocketLike {
  static instances: FakeSocket[] = [];
  binaryType: BinaryType = 'blob';
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  readonly sent: Array<string | ArrayBuffer> = [];
  closedWith: number | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string | ArrayBufferLike | ArrayBufferView): void {
    this.sent.push(data as string | ArrayBuffer);
  }

  close(code = 1000): void {
    if (this.readyState === 3) return;
    this.closedWith = code;
    this.readyState = 3;
    this.onclose?.({ code, wasClean: true } as CloseEvent);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }

  receive(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  receiveBinary(buffer: ArrayBuffer): void {
    this.onmessage?.({ data: buffer } as MessageEvent);
  }

  drop(code = 1006): void {
    this.readyState = 3;
    this.onclose?.({ code, wasClean: false } as CloseEvent);
  }

  jsonSent(): Array<Record<string, unknown>> {
    return this.sent
      .filter((d): d is string => typeof d === 'string')
      .map((d) => JSON.parse(d) as Record<string, unknown>);
  }
}

export class FakeCapture implements CaptureEngine {
  onFrame: ((frame: ArrayBuffer) => void) | null = null;
  muted = false;
  start = mock.fn(async (_stream: MediaStream, onFrame: (frame: ArrayBuffer) => void): Promise<void> => {
    this.onFrame = onFrame;
  });
  setMuted = mock.fn((muted: boolean): void => {
    this.muted = muted;
  });
  stop = mock.fn(async (): Promise<void> => undefined);
  getAnalyser(): AnalyserNode | null {
    return null;
  }
}

export class FakeSpeechInput implements SpeechInput {
  supported = true;
  handlers: SpeechInputHandlers | null = null;
  language: string | null = null;
  start = mock.fn((language: string, handlers: SpeechInputHandlers): void => {
    this.language = language;
    this.handlers = handlers;
  });
  setMuted = mock.fn((_muted: boolean): void => undefined);
  stop = mock.fn((): void => undefined);
}

export class FakeSpeechOutput implements SpeechOutput {
  supported = true;
  readonly context: AudioContext | null = null;
  drained: ((id: number) => void) | null = null;
  spoken: Array<{ id: number; text: string }> = [];
  speaking = false;
  prepare = mock.fn((): void => undefined);
  configure = mock.fn((_language: string, _rate: number): void => undefined);
  beginResponse = mock.fn((_id: number): void => undefined);
  completeResponse = mock.fn((_id: number): void => undefined);
  close = mock.fn(async (): Promise<void> => undefined);
  stop = mock.fn((): void => {
    this.speaking = false;
  });
  speak = mock.fn((id: number, text: string): void => {
    this.spoken.push({ id, text });
    this.speaking = true;
  });
  isSpeaking(): boolean {
    return this.speaking;
  }
  currentText(): string {
    return this.speaking ? this.spoken.map((s) => s.text).join(' ') : '';
  }
  setDrainedHandler(handler: ((id: number) => void) | null): void {
    this.drained = handler;
  }
  getAnalyser(): AnalyserNode | null {
    return null;
  }
  getLevel(): number {
    return this.speaking ? 0.3 : 0;
  }
}

export function fakeStream(): MediaStream {
  const track = { stop: () => undefined, enabled: true };
  return { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

export const READY_MESSAGE = {
  type: 'session.ready',
  call_id: 'c'.repeat(32),
  capabilities: { llm: true, knowledge_documents: 3 },
  speech: { language: 'en-GB', rate: 1.1 },
  max_duration_seconds: 900,
};
