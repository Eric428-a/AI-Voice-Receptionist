import './setup.js';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { pickVoice, SpeechPlayback, type SynthLike } from '../src/services/audio-playback.js';
import {
  BrowserSpeechRecognition,
  type RecognitionLike,
  type RecognitionResultEvent,
} from '../src/services/speech-recognition.js';
import { isLikelyEcho } from '../src/services/voice-connection.js';
import { advance, useFakeTimers, useRealTimers } from './fakes.js';

// ---------------------------------------------------------------- recognition fakes
class FakeRecognition implements RecognitionLike {
  static instances: FakeRecognition[] = [];
  lang = '';
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  onresult: ((event: RecognitionResultEvent) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  started = false;
  aborted = false;
  constructor() {
    FakeRecognition.instances.push(this);
  }
  start(): void {
    this.started = true;
  }
  abort(): void {
    this.aborted = true;
  }
  emit(parts: Array<{ text: string; final: boolean }>): void {
    const results = parts.map((p) => Object.assign([{ transcript: p.text }], { isFinal: p.final }));
    this.onresult?.({ resultIndex: 0, results: results as unknown as RecognitionResultEvent['results'] });
  }
}

function recorder() {
  const log: string[] = [];
  return {
    log,
    handlers: {
      onSpeechStart: () => log.push('start'),
      onInterim: (t: string) => log.push(`interim:${t}`),
      onFinal: (t: string) => log.push(`final:${t}`),
      onError: (code: string, _m: string, fatal: boolean) => log.push(`error:${code}:${fatal}`),
    },
  };
}

describe('BrowserSpeechRecognition', () => {
  beforeEach(() => {
    FakeRecognition.instances = [];
    useFakeTimers();
  });
  afterEach(useRealTimers);

  it('streams live interim words and a final transcript', () => {
    const recognition = new BrowserSpeechRecognition(FakeRecognition);
    const { log, handlers } = recorder();
    recognition.start('en-GB', handlers);
    const rec = FakeRecognition.instances[0];
    assert.equal(rec.lang, 'en-GB');
    assert.equal(rec.continuous, true);
    assert.equal(rec.interimResults, true);
    rec.emit([{ text: 'what about', final: false }]);
    rec.emit([{ text: 'what about your projects', final: true }]);
    assert.deepEqual(log, ['start', 'interim:what about', 'final:what about your projects']);
    recognition.stop();
  });

  it('restarts automatically when the browser ends the session, but not when muted or stopped', async () => {
    const recognition = new BrowserSpeechRecognition(FakeRecognition);
    recognition.start('en-US', recorder().handlers);
    FakeRecognition.instances[0].onend?.();
    await advance(200, 10);
    assert.equal(FakeRecognition.instances.length, 2);
    assert.equal(FakeRecognition.instances[1].started, true);

    recognition.setMuted(true);
    assert.equal(FakeRecognition.instances[1].aborted, true);
    await advance(500, 50);
    assert.equal(FakeRecognition.instances.length, 2);
    recognition.setMuted(false);
    assert.equal(FakeRecognition.instances.length, 3);

    recognition.stop();
    await advance(500, 50);
    assert.equal(FakeRecognition.instances.length, 3);
  });

  it('reports blocked microphones as fatal and ignores silence', () => {
    const recognition = new BrowserSpeechRecognition(FakeRecognition);
    const { log, handlers } = recorder();
    recognition.start('en-US', handlers);
    FakeRecognition.instances[0].onerror?.({ error: 'no-speech' });
    assert.deepEqual(log, []);
    FakeRecognition.instances[0].onerror?.({ error: 'not-allowed' });
    assert.deepEqual(log, ['error:not-allowed:true']);
  });

  it('knows when the browser has no speech recognition', () => {
    const recognition = new BrowserSpeechRecognition(null);
    assert.equal(recognition.supported, false);
    const { log, handlers } = recorder();
    recognition.start('en-US', handlers);
    assert.deepEqual(log, ['error:unsupported:true']);
  });
});

// ---------------------------------------------------------------- synthesis fakes
interface FakeUtterance {
  text: string;
  lang: string;
  rate: number;
  volume: number;
  voice: SpeechSynthesisVoice | null;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onboundary: (() => void) | null;
}

class FakeSynth implements SynthLike {
  queue: FakeUtterance[] = [];
  cancelled = 0;
  voices: SpeechSynthesisVoice[] = [];
  onvoiceschanged: SynthLike['onvoiceschanged'] = null;
  speak(u: SpeechSynthesisUtterance): void {
    this.queue.push(u as unknown as FakeUtterance);
  }
  cancel(): void {
    this.cancelled += 1;
    const pending = this.queue;
    this.queue = [];
    pending.forEach((u) => u.onerror?.()); // browsers fire error/end for cancelled utterances
  }
  getVoices(): SpeechSynthesisVoice[] {
    return this.voices;
  }
  finishNext(): void {
    this.queue.shift()?.onend?.();
  }
}

function makePlayback(synth: FakeSynth) {
  const factory = (text: string) =>
    ({ text, lang: '', rate: 1, volume: 1, voice: null, onstart: null, onend: null, onerror: null, onboundary: null }) as unknown as SpeechSynthesisUtterance;
  return new SpeechPlayback(synth, factory, () => ({ state: 'running', resume: async () => undefined, close: async () => undefined }) as unknown as AudioContext);
}

describe('SpeechPlayback', () => {
  it('speaks sentences in order and reports when the whole reply has been spoken', () => {
    const synth = new FakeSynth();
    const playback = makePlayback(synth);
    const drained: number[] = [];
    playback.setDrainedHandler((id) => drained.push(id));
    playback.configure('en-GB', 1.1);
    playback.beginResponse(3);
    playback.speak(3, 'Hello there.');
    playback.speak(3, 'How can I help?');
    playback.speak(99, 'stale reply'); // wrong response id is ignored
    assert.deepEqual(synth.queue.map((u) => [u.text, u.lang, u.rate]), [['Hello there.', 'en-GB', 1.1], ['How can I help?', 'en-GB', 1.1]]);
    assert.equal(playback.isSpeaking(), true);
    assert.equal(playback.currentText(), 'Hello there. How can I help?');
    synth.queue[0].onboundary?.();
    assert.ok(playback.getLevel() > 0.3);
    synth.finishNext();
    playback.completeResponse(3);
    assert.deepEqual(drained, []);
    synth.finishNext();
    assert.deepEqual(drained, [3]);
    assert.equal(playback.getLevel(), 0);
  });

  it('stops instantly on barge-in and ignores late events from cancelled speech', () => {
    const synth = new FakeSynth();
    const playback = makePlayback(synth);
    const drained: number[] = [];
    playback.setDrainedHandler((id) => drained.push(id));
    playback.beginResponse(1);
    playback.speak(1, 'A long sentence.');
    playback.completeResponse(1);
    const before = synth.cancelled;
    playback.stop();
    assert.equal(synth.cancelled, before + 1);
    assert.equal(playback.isSpeaking(), false);
    assert.deepEqual(drained, []); // a cancelled reply is not reported as finished
  });

  it('prefers natural voices for the configured language', () => {
    const voice = (name: string, lang: string, isDefault = false) =>
      ({ name, lang, default: isDefault }) as SpeechSynthesisVoice;
    const voices = [voice('Robot', 'en-US', true), voice('Google UK English Female', 'en-GB'), voice('Amélie', 'fr-FR')];
    assert.equal(pickVoice(voices, 'en-GB')?.name, 'Google UK English Female');
    assert.equal(pickVoice(voices, 'de-DE'), null);
  });
});

describe('echo filtering', () => {
  it('recognizes the assistant hearing itself', () => {
    const spoken = 'Haumex Labs works on artificial intelligence and robotics.';
    assert.equal(isLikelyEcho('works on artificial intelligence', spoken), true);
    assert.equal(isLikelyEcho('wait what about your biomedical projects', spoken), false);
    assert.equal(isLikelyEcho('hello', ''), false);
  });
});
