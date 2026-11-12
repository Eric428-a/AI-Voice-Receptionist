import type { AudioLevelSource } from '../types/audio.js';
import type { CallState, ConnectionHealth } from '../types/call.js';
import { AssistantOrb } from './assistant-orb.js';
import { AudioVisualizer } from './audio-visualizer.js';
import { CallControls, type CallControlHandlers } from './call-controls.js';
import { CallTimer } from './call-timer.js';
import { ConnectionIndicator } from './connection-indicator.js';
import { TranscriptPanel } from './transcript-panel.js';

const STATUS_TEXT: Record<CallState, string> = {
  ready: '',
  initiating: 'Calling',
  connected: 'Connected',
  listening: 'Listening',
  thinking: 'One moment',
  speaking: 'Speaking',
  interrupted: 'Go ahead',
  ended: 'Call ended',
};

export type NoticeTone = 'error' | 'info';

/** Composes the whole call interface and exposes small, intention-revealing methods. */
export class CallScreen {
  readonly element: HTMLElement;
  readonly orb = new AssistantOrb();
  readonly visualizer: AudioVisualizer;
  readonly timer = new CallTimer();
  readonly connection = new ConnectionIndicator();
  readonly transcript = new TranscriptPanel();
  readonly controls: CallControls;
  private readonly status: HTMLParagraphElement;
  private readonly notice: HTMLParagraphElement;
  private state: CallState = 'ready';

  constructor(handlers: CallControlHandlers) {
    this.visualizer = new AudioVisualizer((level) => this.orb.setLevel(level));
    this.controls = new CallControls(handlers);

    this.element = document.createElement('main');
    this.element.className = 'call-screen';
    this.element.dataset.state = 'ready';

    const header = document.createElement('header');
    header.className = 'brand';
    header.innerHTML = `
      <img class="brand__logo" src="/assets/logo.svg" alt="" width="36" height="36" />
      <span class="brand__name">Haumex Labs Inc.</span>`;
    header.append(this.connection.element);

    const stage = document.createElement('section');
    stage.className = 'stage';
    stage.setAttribute('aria-label', 'Call');

    const intro = document.createElement('div');
    intro.className = 'intro';
    intro.innerHTML = `
      <h1 class="intro__title">Speak with our receptionist</h1>
      <p class="intro__text">Ask about Haumex Labs Inc., our work and how to reach the team. Answers come from our company documents.</p>`;

    const orbWrap = document.createElement('div');
    orbWrap.className = 'orb-wrap';
    orbWrap.append(this.visualizer.element, this.orb.element);

    const meta = document.createElement('div');
    meta.className = 'call-meta';
    this.status = document.createElement('p');
    this.status.className = 'call-status';
    this.status.setAttribute('aria-live', 'polite');
    meta.append(this.status, this.timer.element);

    this.notice = document.createElement('p');
    this.notice.className = 'notice';
    this.notice.setAttribute('role', 'alert');
    this.notice.hidden = true;

    const hint = document.createElement('p');
    hint.className = 'privacy-hint';
    hint.textContent = 'Your microphone turns on only after you press Call.';

    stage.append(intro, orbWrap, meta, this.notice);
    const footer = document.createElement('footer');
    footer.className = 'dock';
    footer.append(this.controls.element, hint);
    this.element.append(header, stage, footer, this.transcript.element);
  }

  mount(root: HTMLElement): void {
    root.replaceChildren(this.element);
    this.visualizer.start();
  }

  setState(state: CallState): void {
    this.state = state;
    this.element.dataset.state = state;
    this.orb.setState(state);
    this.controls.setState(state);
    this.status.textContent = STATUS_TEXT[state];
    if (state === 'ready') this.timer.reset();
  }

  setCountdown(secondsLeft: number): void {
    if (this.state === 'initiating') {
      this.status.textContent = secondsLeft > 0 ? `Ringing, ${secondsLeft}` : 'Connecting';
    }
  }

  setStatusText(text: string): void {
    this.status.textContent = text;
  }

  setCallerSpeaking(active: boolean): void {
    this.orb.setCallerSpeaking(active);
  }

  setAudioSource(source: AudioLevelSource | null): void {
    this.visualizer.setSource(source);
  }

  setHealth(health: ConnectionHealth, detail?: string): void {
    this.connection.set(health, detail);
  }

  showNotice(message: string, tone: NoticeTone = 'error'): void {
    this.notice.textContent = message;
    this.notice.dataset.tone = tone;
    this.notice.hidden = false;
  }

  clearNotice(): void {
    this.notice.hidden = true;
    this.notice.textContent = '';
  }

  get noticeText(): string | null {
    return this.notice.hidden ? null : this.notice.textContent;
  }

  get statusText(): string {
    return this.status.textContent ?? '';
  }

  setTranscriptVisible(visible: boolean): void {
    this.transcript.setVisible(visible);
    this.controls.setTranscriptVisible(visible);
    this.element.classList.toggle('call-screen--transcript', visible);
  }

  destroy(): void {
    this.visualizer.stop();
    this.timer.stop();
    this.controls.destroy();
    this.element.remove();
  }
}
