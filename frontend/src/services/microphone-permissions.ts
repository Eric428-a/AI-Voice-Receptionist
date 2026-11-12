import type { CallErrorKind } from '../types/call.js';

export type MicrophonePermission = 'granted' | 'denied' | 'prompt' | 'unknown';

export class MicrophoneError extends Error {
  constructor(
    readonly kind: Extract<CallErrorKind, 'microphone-denied' | 'microphone-unavailable' | 'unsupported-browser'>,
    message: string,
  ) {
    super(message);
    this.name = 'MicrophoneError';
  }
}

/** Constraints that enable the browser's echo cancellation, noise suppression and AGC. */
export const MICROPHONE_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  },
  video: false,
};

export function isMicrophoneSupported(nav: Navigator = navigator): boolean {
  return Boolean(nav.mediaDevices && typeof nav.mediaDevices.getUserMedia === 'function');
}

export async function queryMicrophonePermission(nav: Navigator = navigator): Promise<MicrophonePermission> {
  try {
    if (!nav.permissions?.query) return 'unknown';
    const status = await nav.permissions.query({ name: 'microphone' as PermissionName });
    return status.state;
  } catch {
    return 'unknown'; // Firefox/Safari may not support querying "microphone"
  }
}

export function classifyMediaError(error: unknown): MicrophoneError {
  const name = error instanceof Error || error instanceof DOMException ? error.name : '';
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return new MicrophoneError(
        'microphone-denied',
        "Microphone access is blocked. Allow it in your browser's site settings, then press Call again.",
      );
    case 'NotFoundError':
    case 'OverconstrainedError':
      return new MicrophoneError('microphone-unavailable', 'No microphone was found. Connect one and press Call again.');
    case 'NotReadableError':
    case 'AbortError':
      return new MicrophoneError(
        'microphone-unavailable',
        'Your microphone is in use by another app. Close it there and press Call again.',
      );
    default:
      return new MicrophoneError('microphone-unavailable', 'The microphone could not be started. Press Call to try again.');
  }
}

/** Ask for microphone access (prompts only if not already granted). */
export async function requestMicrophone(nav: Navigator = navigator): Promise<MediaStream> {
  if (!isMicrophoneSupported(nav)) {
    throw new MicrophoneError(
      'unsupported-browser',
      "This browser can't make voice calls. Use a current version of Chrome, Edge, Firefox or Safari over HTTPS or localhost.",
    );
  }
  try {
    return await nav.mediaDevices.getUserMedia(MICROPHONE_CONSTRAINTS);
  } catch (error) {
    throw classifyMediaError(error);
  }
}
