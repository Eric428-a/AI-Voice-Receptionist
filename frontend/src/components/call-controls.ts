import type { CallState } from '../types/call.js';

export interface CallControlHandlers {
  onCall(): void;
  onEndCall(): void;
  onToggleMute(): void;
  onToggleTranscript(): void;
}

const ICONS = {
  phone:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z"/></svg>',
  mic:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11z"/></svg>',
  micOff:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 11h-2a5 5 0 0 1-.6 2.4l1.5 1.5A7 7 0 0 0 19 11zM15 11V5a3 3 0 0 0-5.9-.8L15 10.1zM4.3 3 3 4.3l6 6V11a3 3 0 0 0 4.3 2.7l1.6 1.6A5 5 0 0 1 7 11H5a7 7 0 0 0 6 6.9V21h2v-3.1a7 7 0 0 0 3.4-1.4l3.3 3.3 1.3-1.3z"/></svg>',
  hangup:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 9c-1.6 0-3.15.25-4.6.72v3.1a1 1 0 0 1-.56.9 11.5 11.5 0 0 0-2.66 1.85 1 1 0 0 1-1.4 0L.29 13.08a1 1 0 0 1 0-1.41C3.34 8.78 7.46 7 12 7s8.66 1.78 11.71 4.67a1 1 0 0 1 0 1.41l-2.48 2.49a1 1 0 0 1-1.4 0 11.3 11.3 0 0 0-2.67-1.85 1 1 0 0 1-.56-.9v-3.1A15 15 0 0 0 12 9z"/></svg>',
  transcript:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v2H4zm0 4h16v2H4zm0 4h10v2H4zm0 4h7v2H4z"/></svg>',
};

function button(className: string, html: string, label: string): HTMLButtonElement {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = className;
  el.innerHTML = html;
  el.setAttribute('aria-label', label);
  return el;
}

export class CallControls {
  readonly element: HTMLDivElement;
  readonly callButton: HTMLButtonElement;
  readonly muteButton: HTMLButtonElement;
  readonly endButton: HTMLButtonElement;
  readonly transcriptButton: HTMLButtonElement;
  private readonly abort = new AbortController();

  constructor(handlers: CallControlHandlers) {
    this.element = document.createElement('div');
    this.element.className = 'controls';

    this.callButton = button('control control--call', `${ICONS.phone}<span>Call</span>`, 'Call Haumex Labs Inc.');
    this.muteButton = button('control control--round', `${ICONS.mic}<span class="control__label">Mute</span>`, 'Mute microphone');
    this.endButton = button('control control--end', `${ICONS.hangup}<span class="control__label">End call</span>`, 'End call');
    this.transcriptButton = button(
      'control control--round',
      `${ICONS.transcript}<span class="control__label">Transcript</span>`,
      'Show transcript',
    );
    this.muteButton.setAttribute('aria-pressed', 'false');
    this.transcriptButton.setAttribute('aria-pressed', 'false');
    this.transcriptButton.setAttribute('aria-controls', 'call-transcript');

    const inCall = document.createElement('div');
    inCall.className = 'controls__in-call';
    inCall.append(this.muteButton, this.endButton, this.transcriptButton);
    this.element.append(this.callButton, inCall);

    const signal = this.abort.signal;
    this.callButton.addEventListener('click', () => handlers.onCall(), { signal });
    this.endButton.addEventListener('click', () => handlers.onEndCall(), { signal });
    this.muteButton.addEventListener('click', () => handlers.onToggleMute(), { signal });
    this.transcriptButton.addEventListener('click', () => handlers.onToggleTranscript(), { signal });
    this.setState('ready');
  }

  setState(state: CallState): void {
    const idle = state === 'ready';
    this.element.dataset.mode = idle ? 'idle' : 'in-call';
    this.callButton.hidden = !idle;
    this.callButton.disabled = !idle;
    this.endButton.disabled = state === 'ended';
    this.muteButton.disabled = state === 'ended' || state === 'initiating';
    this.transcriptButton.disabled = state === 'ended';
  }

  setMuted(muted: boolean): void {
    this.muteButton.setAttribute('aria-pressed', String(muted));
    this.muteButton.setAttribute('aria-label', muted ? 'Unmute microphone' : 'Mute microphone');
    this.muteButton.innerHTML = `${muted ? ICONS.micOff : ICONS.mic}<span class="control__label">${muted ? 'Unmute' : 'Mute'}</span>`;
    this.muteButton.classList.toggle('control--active', muted);
  }

  setTranscriptVisible(visible: boolean): void {
    this.transcriptButton.setAttribute('aria-pressed', String(visible));
    this.transcriptButton.setAttribute('aria-label', visible ? 'Hide transcript' : 'Show transcript');
    this.transcriptButton.classList.toggle('control--active', visible);
  }

  destroy(): void {
    this.abort.abort();
    this.element.remove();
  }
}
