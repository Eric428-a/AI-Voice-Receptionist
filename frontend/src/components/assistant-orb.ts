import type { CallState } from '../types/call.js';

/**
 * The central orb. Visual state comes from `data-state` (CSS animations);
 * loudness comes from real audio via setLevel(), driven by the visualizer's
 * analyser loop.
 */
export class AssistantOrb {
  readonly element: HTMLDivElement;
  private smoothed = 0;

  constructor() {
    this.element = document.createElement('div');
    this.element.className = 'orb';
    this.element.dataset.state = 'ready';
    this.element.setAttribute('aria-hidden', 'true');
    this.element.innerHTML = `
      <div class="orb__ring orb__ring--1"></div>
      <div class="orb__ring orb__ring--2"></div>
      <div class="orb__ring orb__ring--3"></div>
      <div class="orb__halo"></div>
      <div class="orb__body">
        <div class="orb__swirl"></div>
        <div class="orb__core"></div>
      </div>`;
  }

  setState(state: CallState): void {
    this.element.dataset.state = state;
    if (state === 'ready' || state === 'ended' || state === 'thinking') this.setLevel(0, true);
  }

  setCallerSpeaking(active: boolean): void {
    this.element.classList.toggle('orb--caller-speaking', active);
  }

  /** level in 0..1; attack fast, release slow, like a VU meter. */
  setLevel(level: number, immediate = false): void {
    const clamped = Math.max(0, Math.min(1, level));
    if (immediate) {
      this.smoothed = clamped;
    } else {
      const factor = clamped > this.smoothed ? 0.5 : 0.12;
      this.smoothed += (clamped - this.smoothed) * factor;
    }
    this.element.style.setProperty('--level', this.smoothed.toFixed(3));
  }
}
