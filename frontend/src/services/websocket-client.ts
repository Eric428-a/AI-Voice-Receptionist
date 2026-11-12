import type { ClientMessage, ServerMessage } from '../types/websocket.js';
import { parseServerMessage } from '../types/websocket.js';

/** Minimal WebSocket surface used here (lets tests inject a fake). */
export interface WebSocketLike {
  binaryType: BinaryType;
  readonly readyState: number;
  onopen: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  send(data: string | ArrayBufferLike | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface WebSocketClientHandlers {
  onMessage(message: ServerMessage): void;
  onBinary(data: ArrayBuffer): void;
  /** Called once when the socket closes after having been opened. */
  onClose(code: number, clean: boolean): void;
}

const OPEN = 1;
const HEARTBEAT_MS = 15000;
const CONNECT_TIMEOUT_MS = 8000;

export class WebSocketClient {
  private socket: WebSocketLike | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private closedByUs = false;

  constructor(
    private readonly url: string,
    private readonly handlers: WebSocketClientHandlers,
    private readonly factory: WebSocketFactory = (u) => new WebSocket(u) as WebSocketLike,
  ) {}

  get isOpen(): boolean {
    return this.socket?.readyState === OPEN;
  }

  connect(timeoutMs: number = CONNECT_TIMEOUT_MS): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const socket = this.factory(this.url);
      socket.binaryType = 'arraybuffer';
      this.socket = socket;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.teardown();
        socket.close();
        reject(new Error('WebSocket connection timed out'));
      }, timeoutMs);

      socket.onopen = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.heartbeat = setInterval(() => this.send({ type: 'ping' }), HEARTBEAT_MS);
        resolve();
      };
      socket.onerror = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.teardown();
        reject(new Error('WebSocket connection failed'));
      };
      socket.onclose = (event: CloseEvent) => {
        clearTimeout(timer);
        const wasOpen = settled;
        if (!settled) {
          settled = true;
          reject(new Error(`WebSocket closed during connect (${event.code})`));
        }
        this.teardown();
        if (wasOpen && !this.closedByUs) this.handlers.onClose(event.code, event.wasClean);
      };
      socket.onmessage = (event: MessageEvent) => {
        if (typeof event.data === 'string') {
          const message = parseServerMessage(event.data);
          if (message) this.handlers.onMessage(message);
        } else if (event.data instanceof ArrayBuffer) {
          this.handlers.onBinary(event.data);
        }
      };
    });
  }

  send(message: ClientMessage): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  sendAudio(frame: ArrayBuffer): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false;
    this.socket.send(frame);
    return true;
  }

  close(code = 1000, reason = 'call ended'): void {
    this.closedByUs = true;
    const socket = this.socket;
    this.teardown();
    if (socket && socket.readyState <= OPEN) socket.close(code, reason);
  }

  private teardown(): void {
    if (this.heartbeat !== null) clearInterval(this.heartbeat);
    this.heartbeat = null;
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      // keep onclose until the close event has been delivered
    }
  }
}
