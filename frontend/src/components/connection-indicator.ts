import type { ConnectionHealth } from '../types/call.js';

const LABELS: Record<ConnectionHealth, string> = {
  checking: 'Checking availability',
  online: 'Receptionist available',
  degraded: 'Limited service',
  offline: 'Service unreachable',
};

export class ConnectionIndicator {
  readonly element: HTMLDivElement;
  private readonly label: HTMLSpanElement;
  private health: ConnectionHealth = 'checking';

  constructor() {
    this.element = document.createElement('div');
    this.element.className = 'connection';
    this.element.setAttribute('role', 'status');
    const dot = document.createElement('span');
    dot.className = 'connection__dot';
    dot.setAttribute('aria-hidden', 'true');
    this.label = document.createElement('span');
    this.label.className = 'connection__label';
    this.element.append(dot, this.label);
    this.set('checking');
  }

  get state(): ConnectionHealth {
    return this.health;
  }

  set(health: ConnectionHealth, detail?: string): void {
    this.health = health;
    this.element.dataset.health = health;
    this.label.textContent = LABELS[health];
    if (detail) this.element.title = detail;
    else this.element.removeAttribute('title');
  }
}
