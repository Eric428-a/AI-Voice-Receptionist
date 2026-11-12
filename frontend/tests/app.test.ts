import './setup.js';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { App, type VoiceConnectionLike } from '../src/app.js';
import { MicrophoneError } from '../src/services/microphone-permissions.js';
import type { VoiceConnectionHandlers } from '../src/services/voice-connection.js';
import type { SessionReadyMessage } from '../src/types/websocket.js';
import {
  advance, assertCalledWith, callCount, FakeCapture, FakeSpeechInput, FakeSpeechOutput, fakeStream, flush, jsonResponse, READY_MESSAGE,
  useFakeTimers, useRealTimers,
} from './fakes.js';

class FakeConnection implements VoiceConnectionLike {
  resolveOpen: (() => void) | null = null;
  rejectOpen: ((e: unknown) => void) | null = null;
  begin = mock.fn((): void => undefined);
  setMuted = mock.fn((_muted: boolean): void => undefined);
  end = mock.fn(async (): Promise<void> => undefined);
  constructor(readonly handlers: VoiceConnectionHandlers) {}
  open(): Promise<SessionReadyMessage> {
    return new Promise((resolve, reject) => {
      this.resolveOpen = () => resolve(READY_MESSAGE as SessionReadyMessage);
      this.rejectOpen = reject;
    });
  }
}

const HEALTH = {
  status: 'ok', service: 'x', version: '1', active_calls: 0,
  providers: { llm: true, llm_model: 'm', embedding_model: 'fastembed:x', speech: 'browser (en-US)' },
  knowledge: { indexed_documents: 2, ingestion_running: false },
};

const apps: App[] = [];

function createApp(options: { mic?: () => Promise<MediaStream>; healthStatus?: number; recognition?: boolean; voice?: boolean } = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  const connections: FakeConnection[] = [];
  const ringtone = { play: mock.fn(async (): Promise<void> => undefined), stop: mock.fn((): void => undefined) };
  const playback = new FakeSpeechOutput();
  playback.supported = options.voice ?? true;
  const speechInput = new FakeSpeechInput();
  speechInput.supported = options.recognition ?? true;
  const capture = new FakeCapture();
  const requestMicrophone = mock.fn(options.mic ?? (async (): Promise<MediaStream> => fakeStream()));
  const app = new App({
    root,
    fetchFn: (async () =>
      options.healthStatus ? new Response('', { status: options.healthStatus }) : jsonResponse(HEALTH)) as unknown as typeof fetch,
    requestMicrophone,
    capture,
    speechInput,
    speechOutput: playback,
    ringtone,
    createConnection: (handlers) => {
      const connection = new FakeConnection(handlers);
      connections.push(connection);
      return connection;
    },
  });
  apps.push(app);
  app.start();
  return { app, root, connections, ringtone, playback, requestMicrophone };
}

async function connectCall(ctx: ReturnType<typeof createApp>) {
  void ctx.app.startCall();
  await advance(0);
  ctx.connections[0].resolveOpen?.();
  await advance(5000, 50);
  return ctx.connections[0];
}

describe('App call lifecycle', () => {
  beforeEach(useFakeTimers);
  afterEach(async () => {
    apps.splice(0).forEach((app) => app.destroy());
    await flush();
    useRealTimers();
    document.body.replaceChildren();
  });

  it('initializes in READY with branding, intro, call button and status', async () => {
    const { app, root } = createApp();
    await advance(0);
    assert.equal(app.state, 'ready');
    assert.equal(root.querySelector('.brand__name')?.textContent, 'Haumex Labs Inc.');
    assert.equal(root.querySelector('.intro__title')?.textContent, 'Speak with our receptionist');
    assert.equal(root.querySelector<HTMLButtonElement>('.control--call')?.hidden, false);
    assert.equal(root.querySelector('.connection')?.getAttribute('data-health'), 'online');
  });

  it('shows the service as unreachable when the backend is down', async () => {
    const { root } = createApp({ healthStatus: 502 });
    await advance(0);
    assert.equal(root.querySelector('.connection')?.getAttribute('data-health'), 'offline');
  });

  it('asks for the microphone, rings exactly five seconds, then connects and greets', async () => {
    const ctx = createApp();
    void ctx.app.startCall();
    assert.equal(callCount(ctx.playback.prepare), 1); // unlocked inside the click
    assert.equal(ctx.app.state, 'initiating');
    await advance(0);
    assert.equal(callCount(ctx.requestMicrophone), 1);
    assert.equal(callCount(ctx.ringtone.play), 1);
    ctx.connections[0].resolveOpen?.(); // session ready well before the ring ends

    await advance(4999, 1);
    assert.equal(ctx.app.state, 'initiating');
    assert.equal(callCount(ctx.connections[0].begin), 0); // no greeting while ringing

    await advance(1, 1);
    assert.ok(callCount(ctx.ringtone.stop) >= 1);
    assert.equal(ctx.app.state, 'connected');
    assert.equal(callCount(ctx.connections[0].begin), 1);
    assert.equal(ctx.app.screen.timer.running, true);
  });

  it('explains which browsers work when speech recognition is unavailable', async () => {
    const ctx = createApp({ recognition: false });
    await ctx.app.startCall();
    assert.equal(ctx.app.state, 'ready');
    assert.match(ctx.app.screen.noticeText ?? '', /Chrome or Microsoft Edge/);
    assert.equal(callCount(ctx.requestMicrophone), 0);
  });

  it('ignores repeated clicks on Call', async () => {
    const ctx = createApp();
    void ctx.app.startCall();
    void ctx.app.startCall();
    await advance(0);
    assert.equal(callCount(ctx.requestMicrophone), 1);
    assert.equal(ctx.connections.length, 1);
  });

  it('returns to READY with guidance when microphone permission is denied', async () => {
    const ctx = createApp({
      mic: async () => {
        throw new MicrophoneError('microphone-denied', "Microphone access is blocked. Allow it in your browser's site settings, then press Call again.");
      },
    });
    await ctx.app.startCall();
    assert.equal(ctx.app.state, 'ready');
    assert.match(ctx.app.screen.noticeText ?? '', /site settings/);
    assert.equal(ctx.connections.length, 0);
  });

  it('aborts ringing immediately when setup fails', async () => {
    const ctx = createApp();
    void ctx.app.startCall();
    await advance(0);
    ctx.connections[0].rejectOpen?.(new Error('boom'));
    await advance(0);
    assert.equal(ctx.app.state, 'ready');
    assert.ok(callCount(ctx.ringtone.stop) >= 1);
    assert.equal(callCount(ctx.connections[0].end), 1);
  });

  it('cancels cleanly when the caller hangs up during ringing', async () => {
    const ctx = createApp();
    void ctx.app.startCall();
    await advance(1000, 50);
    await ctx.app.endCall();
    assert.equal(ctx.app.state, 'ready');
    assert.equal(callCount(ctx.connections[0].end), 1);
    ctx.connections[0].resolveOpen?.();
    await advance(10000, 100);
    assert.equal(ctx.app.state, 'ready');
    assert.equal(callCount(ctx.connections[0].begin), 0);
  });

  it('follows server states and handles interruptions', async () => {
    const ctx = createApp();
    const connection = await connectCall(ctx);
    connection.handlers.onAssistantState('speaking');
    assert.equal(ctx.app.state, 'speaking');
    connection.handlers.onAssistantText(1, 'Hello! Thank you for calling', false);
    connection.handlers.onInterrupted(1);
    assert.equal(ctx.app.state, 'interrupted');
    assert.notEqual(ctx.root.querySelector('.transcript__entry--interrupted'), null);
    connection.handlers.onAssistantState('listening');
    assert.equal(ctx.app.state, 'interrupted');
    await advance(450, 50);
    assert.equal(ctx.app.state, 'listening');
    connection.handlers.onCallerTranscript('Wait, what about', false);
    assert.notEqual(ctx.root.querySelector('.transcript__entry--live'), null);
    connection.handlers.onCallerTranscript('Wait, what about your biomedical projects?', true);
    assert.equal(ctx.root.querySelector('.transcript__entry--live'), null);
    connection.handlers.onAssistantState('thinking');
    assert.equal(ctx.app.state, 'thinking');
    assert.equal(
      ctx.root.querySelector('.transcript__entry--caller .transcript__text')?.textContent,
      'Wait, what about your biomedical projects?',
    );
  });

  it('mutes and unmutes the microphone', async () => {
    const ctx = createApp();
    const connection = await connectCall(ctx);
    ctx.app.toggleMute();
    assertCalledWith(connection.setMuted, true);
    assert.equal(ctx.root.querySelector('.control--round')?.getAttribute('aria-pressed'), 'true');
    ctx.app.toggleMute();
    assert.deepEqual(connection.setMuted.mock.calls.at(-1)?.arguments, [false]);
    assert.equal(ctx.app.isMuted, false);
  });

  it('ends the call, cleans up and returns to READY', async () => {
    const ctx = createApp();
    const connection = await connectCall(ctx);
    connection.handlers.onAssistantState('listening');
    await ctx.app.endCall();
    assert.equal(callCount(connection.end), 1);
    assert.equal(ctx.app.state, 'ended');
    assert.equal(ctx.app.screen.timer.running, false);
    await advance(1600, 50);
    assert.equal(ctx.app.state, 'ready');
    connection.handlers.onAssistantState('speaking'); // late event from the old call
    assert.equal(ctx.app.state, 'ready');
  });

  it('shows speech recognition problems and ends the call when they are fatal', async () => {
    const ctx = createApp();
    const connection = await connectCall(ctx);
    connection.handlers.onServiceError('speech_network', "Speech recognition had a problem. Keep talking; it's reconnecting.", true);
    assert.match(ctx.app.screen.noticeText ?? '', /reconnecting/);
    connection.handlers.onServiceError('speech_not-allowed', 'Microphone access is blocked.', false);
    await advance(0);
    assert.equal(ctx.app.state, 'ended');
    assert.match(ctx.app.screen.noticeText ?? '', /Microphone access is blocked/);
  });

  it('ends the call with a clear message when the connection drops', async () => {
    const ctx = createApp();
    const connection = await connectCall(ctx);
    connection.handlers.onEnded('connection_lost');
    await advance(0);
    assert.equal(ctx.app.state, 'ended');
    assert.match(ctx.app.screen.noticeText ?? '', /call dropped/);
  });

  it('opens the transcript automatically when the browser cannot speak', async () => {
    const ctx = createApp({ voice: false });
    const connection = await connectCall(ctx);
    connection.handlers.onReady(READY_MESSAGE as SessionReadyMessage);
    assert.equal(ctx.app.screen.transcript.visible, true);
    ctx.app.toggleTranscript();
    assert.equal(ctx.app.screen.transcript.visible, false);
  });

  it('removes the interface and releases audio on destroy', async () => {
    const ctx = createApp();
    await advance(0);
    ctx.app.destroy();
    apps.splice(apps.indexOf(ctx.app), 1);
    assert.equal(ctx.root.querySelector('.call-screen'), null);
    assert.equal(callCount(ctx.playback.close), 1);
  });
});
