export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, '0');
  return h > 0 ? `${h}:${mm}:${String(s).padStart(2, '0')}` : `${mm}:${String(s).padStart(2, '0')}`;
}

/** Call duration display. */
export class CallTimer {
  readonly element: HTMLElement;
  private interval: ReturnType<typeof setInterval> | null = null;
  private startedAt = 0;

  constructor(private readonly now: () => number = () => Date.now()) {
    this.element = document.createElement('time');
    this.element.className = 'call-timer';
    this.element.setAttribute('aria-label', 'Call duration');
    this.element.textContent = '0:00';
  }

  get running(): boolean {
    return this.interval !== null;
  }

  get elapsedSeconds(): number {
    return this.running ? Math.floor((this.now() - this.startedAt) / 1000) : 0;
  }

  start(): void {
    this.stop();
    this.startedAt = this.now();
    this.render();
    this.interval = setInterval(() => this.render(), 1000);
  }

  stop(): void {
    if (this.interval !== null) clearInterval(this.interval);
    this.interval = null;
  }

  reset(): void {
    this.stop();
    this.element.textContent = '0:00';
  }

  private render(): void {
    const elapsed = (this.now() - this.startedAt) / 1000;
    this.element.textContent = formatDuration(elapsed);
    this.element.setAttribute('datetime', `PT${Math.floor(elapsed)}S`);
  }
}
