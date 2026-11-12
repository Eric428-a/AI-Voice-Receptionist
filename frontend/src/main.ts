import { App, createDefaultConnection } from './app.js';
import { AudioCapture } from './services/audio-capture.js';
import { Ringtone, SpeechPlayback } from './services/audio-playback.js';
import { requestMicrophone } from './services/microphone-permissions.js';
import { BrowserSpeechRecognition } from './services/speech-recognition.js';
import { DEFAULT_CAPTURE_OPTIONS } from './types/audio.js';

const root = document.querySelector<HTMLElement>('#app');
if (!root) throw new Error('Missing #app root element');

// Speech-to-text and text-to-speech run in the browser; only text goes to the backend.
const capture = new AudioCapture(DEFAULT_CAPTURE_OPTIONS); // level meter for the orb only
const speechInput = new BrowserSpeechRecognition();
const speechOutput = new SpeechPlayback();
const app = new App({
  root,
  fetchFn: (input, init) => fetch(input, init),
  requestMicrophone: () => requestMicrophone(),
  capture,
  speechInput,
  speechOutput,
  ringtone: new Ringtone(() => speechOutput.context),
  createConnection: (handlers) => createDefaultConnection(handlers, { capture, speechInput, speechOutput }),
});
app.start();
