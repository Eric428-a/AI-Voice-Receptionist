/** Call lifecycle states shown in the UI. */
export type CallState =
  | 'ready'
  | 'initiating'
  | 'connected'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'ended';

export type ConnectionHealth = 'checking' | 'online' | 'degraded' | 'offline';

export type TranscriptRole = 'caller' | 'assistant';

export interface TranscriptEntry {
  id: string;
  role: TranscriptRole;
  text: string;
  final: boolean;
  interrupted: boolean;
}

export interface CreateCallResponse {
  call_id: string;
  session_token: string;
  websocket_path: string;
  created_at: string;
  setup_expires_at: string;
  max_duration_seconds: number;
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  service: string;
  version: string;
  active_calls: number;
  providers: {
    llm: boolean;
    llm_model: string | null;
    embedding_model: string;
    speech: string;
  };
  knowledge: { indexed_documents: number; ingestion_running: boolean };
}

/** Errors surfaced to the caller, written in plain language. */
export type CallErrorKind =
  | 'microphone-denied'
  | 'microphone-unavailable'
  | 'speech-unsupported'
  | 'unsupported-browser'
  | 'server-unreachable'
  | 'lines-busy'
  | 'connection-lost'
  | 'setup-timeout'
  | 'service';

export interface CallError {
  kind: CallErrorKind;
  message: string;
}
