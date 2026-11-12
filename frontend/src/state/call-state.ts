import type { CallState } from '../types/call.js';

/** Allowed transitions. Any state may go to 'ended' (hang-up / failure). */
const TRANSITIONS: Record<CallState, readonly CallState[]> = {
  ready: ['initiating'],
  initiating: ['connected', 'ended', 'ready'],
  connected: ['listening', 'thinking', 'speaking', 'ended'],
  listening: ['thinking', 'speaking', 'interrupted', 'ended'],
  thinking: ['listening', 'speaking', 'interrupted', 'ended'],
  speaking: ['listening', 'thinking', 'interrupted', 'ended'],
  interrupted: ['listening', 'thinking', 'speaking', 'ended'],
  ended: ['ready'],
};

export type StateListener = (state: CallState, previous: CallState) => void;

export class CallStateMachine {
  private current: CallState = 'ready';
  private readonly listeners = new Set<StateListener>();

  get state(): CallState {
    return this.current;
  }

  /** True while a call is being set up or is connected. */
  get inCall(): boolean {
    return this.current !== 'ready' && this.current !== 'ended';
  }

  canTransition(next: CallState): boolean {
    return next === this.current || TRANSITIONS[this.current].includes(next);
  }

  /** Move to `next`. Returns false (and changes nothing) for illegal transitions. */
  transition(next: CallState): boolean {
    if (next === this.current) return true;
    if (!TRANSITIONS[this.current].includes(next)) return false;
    const previous = this.current;
    this.current = next;
    for (const listener of [...this.listeners]) listener(next, previous);
    return true;
  }

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  reset(): void {
    const previous = this.current;
    this.current = 'ready';
    if (previous !== 'ready') for (const listener of [...this.listeners]) listener('ready', previous);
  }
}

export const RING_DURATION_MS = 5000;

/**
 * Exactly-five-second ringing countdown. Resolves after `durationMs`; can be
 * cancelled (rejects with RingCancelledError) when the caller hangs up early.
 */
export class RingCancelledError extends Error {
  constructor() {
    super('Ringing cancelled');
    this.name = 'RingCancelledError';
  }
}

export class RingingSequence {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private rejecter: ((error: Error) => void) | null = null;

  constructor(
    private readonly durationMs: number = RING_DURATION_MS,
    private readonly onTick: (secondsLeft: number) => void = () => undefined,
  ) {}

  start(): Promise<void> {
    this.cancel();
    return new Promise<void>((resolve, reject) => {
      this.rejecter = reject;
      const startedAt = Date.now();
      this.onTick(Math.ceil(this.durationMs / 1000));
      this.ticker = setInterval(() => {
        const left = Math.ceil((this.durationMs - (Date.now() - startedAt)) / 1000);
        if (left > 0) this.onTick(left); // completion is signalled by the promise, not a 0 tick
      }, 1000);
      this.timer = setTimeout(() => {
        this.clear();
        this.rejecter = null;
        resolve();
      }, this.durationMs);
    });
  }

  cancel(): void {
    const reject = this.rejecter;
    this.clear();
    this.rejecter = null;
    reject?.(new RingCancelledError());
  }

  private clear(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    if (this.ticker !== null) clearInterval(this.ticker);
    this.timer = null;
    this.ticker = null;
  }
}
