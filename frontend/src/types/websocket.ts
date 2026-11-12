/**
 * WebSocket protocol (JSON text only - no audio). Mirrors
 * backend/app/schemas/message.py exactly. Speech recognition and speech
 * synthesis happen in the browser; only text travels to the backend.
 */

// ---------------------------------------------------------------- client -> server
export type ClientMessage =
  | { type: 'session.auth'; token: string }
  | { type: 'session.start' }
  | { type: 'user.speaking' }
  | { type: 'user.text'; text: string }
  | { type: 'playback.finished'; response_id: number }
  | { type: 'session.end' }
  | { type: 'ping' };

// ---------------------------------------------------------------- server -> client
export type AssistantState = 'listening' | 'thinking' | 'speaking';
export type ResponseKind = 'greeting' | 'answer' | 'fallback';

export interface Capabilities {
  llm: boolean;
  knowledge_documents: number;
}

export interface SessionReadyMessage {
  type: 'session.ready';
  call_id: string;
  capabilities: Capabilities;
  speech: { language: string; rate: number };
  max_duration_seconds: number;
}
export interface StateMessage {
  type: 'state';
  state: AssistantState;
  response_id: number | null;
}
export interface ResponseStartMessage {
  type: 'response.start';
  response_id: number;
  kind: ResponseKind;
}
export interface ResponseTextDeltaMessage {
  type: 'response.text.delta';
  response_id: number;
  text: string;
}
export interface ResponseSentenceMessage {
  type: 'response.sentence';
  response_id: number;
  text: string;
}
export interface ResponseTextDoneMessage {
  type: 'response.text.done';
  response_id: number;
  text: string;
}
export interface ResponseInterruptedMessage {
  type: 'response.interrupted';
  response_id: number;
  reason: 'barge_in' | 'user_continued';
}
export interface ErrorMessage {
  type: 'error';
  code: string;
  message: string;
  recoverable: boolean;
}
export interface SessionEndedMessage {
  type: 'session.ended';
  reason: string;
}
export interface PongMessage {
  type: 'pong';
}

export type ServerMessage =
  | SessionReadyMessage
  | StateMessage
  | ResponseStartMessage
  | ResponseTextDeltaMessage
  | ResponseSentenceMessage
  | ResponseTextDoneMessage
  | ResponseInterruptedMessage
  | ErrorMessage
  | SessionEndedMessage
  | PongMessage;

// ---------------------------------------------------------------- runtime validation
type Shape = Record<string, 'string' | 'number' | 'boolean' | 'object' | 'number|null'>;

const SHAPES: Record<ServerMessage['type'], Shape> = {
  'session.ready': { call_id: 'string', capabilities: 'object', speech: 'object', max_duration_seconds: 'number' },
  state: { state: 'string', response_id: 'number|null' },
  'response.start': { response_id: 'number', kind: 'string' },
  'response.text.delta': { response_id: 'number', text: 'string' },
  'response.sentence': { response_id: 'number', text: 'string' },
  'response.text.done': { response_id: 'number', text: 'string' },
  'response.interrupted': { response_id: 'number', reason: 'string' },
  error: { code: 'string', message: 'string', recoverable: 'boolean' },
  'session.ended': { reason: 'string' },
  pong: {},
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Parse and validate a server text frame. Returns null for anything off-protocol. */
export function parseServerMessage(raw: string): ServerMessage | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || typeof data.type !== 'string' || !(data.type in SHAPES)) return null;
  const shape = SHAPES[data.type as ServerMessage['type']];
  for (const [key, kind] of Object.entries(shape)) {
    const value = data[key];
    if (kind === 'number|null') {
      if (value !== null && value !== undefined && typeof value !== 'number') return null;
    } else if (kind === 'object') {
      if (!isRecord(value)) return null;
    } else if (typeof value !== kind) {
      return null;
    }
  }
  if (data.type === 'state' && !['listening', 'thinking', 'speaking'].includes(data.state as string)) return null;
  return data as unknown as ServerMessage;
}
