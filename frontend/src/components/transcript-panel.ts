import type { TranscriptEntry, TranscriptRole } from '../types/call.js';

/** Lightweight, optional transcript. Voice stays the primary experience. */
export class TranscriptPanel {
  readonly element: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly empty: HTMLParagraphElement;
  private readonly entries = new Map<string, { data: TranscriptEntry; node: HTMLLIElement }>();
  private counter = 0;
  private pendingCallerId: string | null = null;

  constructor() {
    this.element = document.createElement('aside');
    this.element.className = 'transcript';
    this.element.id = 'call-transcript';
    this.element.hidden = true;
    this.element.setAttribute('aria-label', 'Call transcript');

    const heading = document.createElement('h2');
    heading.className = 'transcript__heading';
    heading.textContent = 'Transcript';
    this.empty = document.createElement('p');
    this.empty.className = 'transcript__empty';
    this.empty.textContent = 'What you and the receptionist say appears here.';
    this.list = document.createElement('ol');
    this.list.className = 'transcript__list';
    this.list.setAttribute('role', 'log');
    this.list.setAttribute('aria-live', 'polite');
    this.element.append(heading, this.empty, this.list);
  }

  get visible(): boolean {
    return !this.element.hidden;
  }

  setVisible(visible: boolean): void {
    this.element.hidden = !visible;
    if (visible) this.scrollToEnd();
  }

  addCaller(text: string): void {
    this.updateCaller(text, true);
  }

  /** Shows the caller's words live while they speak; `final` closes the entry. */
  updateCaller(text: string, final: boolean): void {
    if (!this.pendingCallerId) {
      this.counter += 1;
      this.pendingCallerId = `caller-${this.counter}`;
    }
    this.upsert(this.pendingCallerId, 'caller', text, final);
    const entry = this.entries.get(this.pendingCallerId);
    entry?.node.classList.toggle('transcript__entry--live', !final);
    if (final) this.pendingCallerId = null;
  }

  /** Streams assistant text into the entry for `responseId`. */
  updateAssistant(responseId: number, text: string, final: boolean): void {
    const id = `assistant-${responseId}`;
    const existing = this.entries.get(id);
    const next = final ? text : (existing?.data.text ?? '') + text;
    this.upsert(id, 'assistant', next, final);
  }

  markInterrupted(responseId: number): void {
    const entry = this.entries.get(`assistant-${responseId}`);
    if (!entry) return;
    entry.data.interrupted = true;
    entry.node.classList.add('transcript__entry--interrupted');
  }

  clear(): void {
    this.pendingCallerId = null;
    this.entries.clear();
    this.list.replaceChildren();
    this.empty.hidden = false;
  }

  entryCount(): number {
    return this.entries.size;
  }

  private upsert(id: string, role: TranscriptRole, text: string, final: boolean): void {
    const trimmed = text.trim();
    if (!trimmed) return;
    let entry = this.entries.get(id);
    if (!entry) {
      const node = document.createElement('li');
      node.className = `transcript__entry transcript__entry--${role}`;
      const who = document.createElement('span');
      who.className = 'transcript__who';
      who.textContent = role === 'caller' ? 'You' : 'Receptionist';
      const body = document.createElement('p');
      body.className = 'transcript__text';
      node.append(who, body);
      this.list.append(node);
      entry = { data: { id, role, text: '', final: false, interrupted: false }, node };
      this.entries.set(id, entry);
      this.empty.hidden = true;
    }
    entry.data.text = trimmed;
    entry.data.final = final;
    const body = entry.node.querySelector('.transcript__text');
    if (body) body.textContent = trimmed;
    this.scrollToEnd();
  }

  private scrollToEnd(): void {
    this.list.scrollTop = this.list.scrollHeight;
  }
}
