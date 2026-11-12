import './setup.js';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { CallSetupError, ECHO_TAIL_MS, VoiceConnection, type VoiceConnectionHandlers } from '../src/services/voice-connection.js';
import {
  assertCalledWith, callCount, FakeCapture, FakeSocket, FakeSpeechInput, FakeSpeechOutput, fakeStream, jsonResponse,
  READY_MESSAGE, waitFor,
} from './fakes.js';

const CALL = {
  call_id: 'a'.repeat(32),
  session_token: 'token-123456789',
  websocket_path: `/api/ws/calls/${'a'.repeat(32)}`,
  created_at: '',
  setup_expires_at: '',
  max_duration_seconds: 900,
};

function handlers() {
  return {
    onReady: mock.fn(),
    onAssistantState: mock.fn(),
    onCallerSpeaking: mock.fn(),
    onCallerTranscript: mock.fn(),
    onAssistantText: mock.fn(),
    onInterrupted: mock.fn(),
    onServiceError: mock.fn(),
    onEnded: mock.fn(),
  } satisfies VoiceConnectionHandlers;
}

const open: VoiceConnection[] = [];

async function openConnection() {
  const fetchFn = mock.fn(async (url: string, init?: RequestInit): Promise<Response> => {
    if (url === '/api/calls' && init?.method === 'POST') return jsonResponse(CALL, 201);
    return jsonResponse({}, 200);
  });
  const clock = { now: 0 };
  const h = handlers();
  const capture = new FakeCapture();
  const input = new FakeSpeechInput();
  const output = new FakeSpeechOutput();
  const connection = new VoiceConnection(h, {
    fetchFn: fetchFn as unknown as typeof fetch,
    createSocket: (url) => new FakeSocket(url),
    capture,
    speechInput: input,
    speechOutput: output,
    location: { protocol: 'http:', host: 'localhost:5173' },
    now: () => clock.now,
  });
  open.push(connection);
  const opening = connection.open(fakeStream());
  await waitFor(() => assert.equal(FakeSocket.instances.length, 1));
  const socket = FakeSocket.instances[0];
  socket.open();
  await waitFor(() => assert.deepEqual(socket.jsonSent()[0], { type: 'session.auth', token: CALL.session_token }));
  socket.receive(READY_MESSAGE);
  await opening;
  return { connection, socket, capture, input, output, h, fetchFn, clock };
}

const sentOfType = (socket: FakeSocket, type: string) => socket.jsonSent().filter((m) => m.type === type);

describe('VoiceConnection', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
  });
  afterEach(async () => {
    await Promise.all(open.splice(0).map((c) => c.end()));
  });

  it('authenticates, applies the speech settings and never sends audio', async () => {
    const { connection, socket, capture, output, input, h } = await openConnection();
    assert.equal(socket.url, `ws://localhost:5173/api/ws/calls/${CALL.call_id}`);
    assert.equal(callCount(h.onReady), 1);
    assertCalledWith(output.configure, 'en-GB', 1.1);
    assert.equal(callCount(capture.start), 1);
    connection.begin();
    assert.deepEqual(sentOfType(socket, 'session.start'), [{ type: 'session.start' }]);
    assert.equal(input.language, 'en-GB');
    input.handlers?.onFinal('Hello there');
    assert.equal(socket.sent.filter((d) => typeof d !== 'string').length, 0);
  });

  it('ignores caller speech before the conversation begins', async () => {
    const { input, socket } = await openConnection();
    input.handlers?.onFinal('too early');
    assert.equal(sentOfType(socket, 'user.text').length, 0);
  });

  it('sends the final transcript as text and shows live words', async () => {
    const { connection, input, socket, h } = await openConnection();
    connection.begin();
    input.handlers?.onInterim('what does');
    assert.deepEqual(sentOfType(socket, 'user.speaking'), [{ type: 'user.speaking' }]);
    assertCalledWith(h.onCallerTranscript, 'what does', false);
    input.handlers?.onFinal('what does Haumex Labs do');
    assert.deepEqual(sentOfType(socket, 'user.text'), [{ type: 'user.text', text: 'what does Haumex Labs do' }]);
    assertCalledWith(h.onCallerTranscript, 'what does Haumex Labs do', true);
    assertCalledWith(h.onCallerSpeaking, false);
  });

  it('speaks reply sentences and reports when speaking finished', async () => {
    const { connection, socket, output, h } = await openConnection();
    connection.begin();
    socket.receive({ type: 'response.start', response_id: 4, kind: 'answer' });
    assertCalledWith(output.beginResponse, 4);
    socket.receive({ type: 'response.text.delta', response_id: 4, text: 'Hi.' });
    assertCalledWith(h.onAssistantText, 4, 'Hi.', false);
    socket.receive({ type: 'response.sentence', response_id: 4, text: 'Hi.' });
    assert.deepEqual(output.spoken, [{ id: 4, text: 'Hi.' }]);
    socket.receive({ type: 'response.text.done', response_id: 4, text: 'Hi.' });
    assertCalledWith(output.completeResponse, 4);
    output.drained?.(4);
    assert.deepEqual(sentOfType(socket, 'playback.finished'), [{ type: 'playback.finished', response_id: 4 }]);
  });

  it('barges in instantly when the caller talks over the assistant', async () => {
    const { connection, socket, output, input } = await openConnection();
    connection.begin();
    socket.receive({ type: 'response.start', response_id: 2, kind: 'answer' });
    socket.receive({ type: 'response.sentence', response_id: 2, text: 'Haumex Labs works on artificial intelligence.' });
    input.handlers?.onInterim('wait'); // one word while the assistant talks: not yet an interruption
    assert.equal(sentOfType(socket, 'user.speaking').length, 0);
    input.handlers?.onInterim('wait what about');
    assert.equal(sentOfType(socket, 'user.speaking').length, 1);
    assert.equal(output.isSpeaking(), false); // stopped locally without waiting for the server
  });

  it('does not treat its own voice (speaker echo) as the caller', async () => {
    const { connection, socket, output, input, clock } = await openConnection();
    connection.begin();
    socket.receive({ type: 'response.start', response_id: 2, kind: 'answer' });
    socket.receive({ type: 'response.sentence', response_id: 2, text: 'Haumex Labs works on artificial intelligence.' });
    input.handlers?.onInterim('works on artificial intelligence');
    input.handlers?.onFinal('Haumex Labs works on artificial intelligence');
    assert.equal(sentOfType(socket, 'user.speaking').length, 0);
    assert.equal(sentOfType(socket, 'user.text').length, 0);

    // Recognizer lag: the echo can arrive just after speaking ends.
    output.speaking = false;
    output.drained?.(2);
    input.handlers?.onFinal('artificial intelligence');
    assert.equal(sentOfType(socket, 'user.text').length, 0);
    clock.now += ECHO_TAIL_MS + 1;
    input.handlers?.onFinal('artificial intelligence please');
    assert.equal(sentOfType(socket, 'user.text').length, 1);
  });

  it('stops speaking when the server confirms an interruption', async () => {
    const { connection, socket, output, h } = await openConnection();
    connection.begin();
    socket.receive({ type: 'response.interrupted', response_id: 4, reason: 'barge_in' });
    assert.ok(callCount(output.stop) >= 1);
    assertCalledWith(h.onInterrupted, 4);
  });

  it('mutes recognition and the level meter', async () => {
    const { connection, capture, input, socket } = await openConnection();
    connection.begin();
    connection.setMuted(true);
    assertCalledWith(capture.setMuted, true);
    assertCalledWith(input.setMuted, true);
    input.handlers?.onFinal('should not be sent');
    assert.equal(sentOfType(socket, 'user.text').length, 0);
  });

  it('reports speech recognition errors', async () => {
    const { connection, input, h } = await openConnection();
    connection.begin();
    input.handlers?.onError('not-allowed', 'Microphone access is blocked.', true);
    assertCalledWith(h.onServiceError, 'speech_not-allowed', 'Microphone access is blocked.', false);
  });

  it('cleans up everything when the call ends', async () => {
    const { connection, socket, capture, input, output, fetchFn } = await openConnection();
    connection.begin();
    await connection.end();
    assert.ok(socket.jsonSent().some((m) => m.type === 'session.end'));
    assert.equal(socket.closedWith, 1000);
    assert.equal(callCount(input.stop), 1);
    assert.ok(callCount(output.stop) >= 1);
    assert.equal(callCount(capture.stop), 1);
    assertCalledWith(fetchFn, `/api/calls/${CALL.call_id}`, { method: 'DELETE', keepalive: true });
    await connection.end(); // idempotent
    assert.equal(callCount(capture.stop), 1);
  });

  it('reports a dropped connection', async () => {
    const { socket, h } = await openConnection();
    socket.drop();
    assertCalledWith(h.onEnded, 'connection_lost');
  });

  it('maps setup failures to clear errors', async () => {
    const deps = {
      capture: new FakeCapture(),
      speechInput: new FakeSpeechInput(),
      speechOutput: new FakeSpeechOutput(),
      location: { protocol: 'http:', host: 'x' },
    };
    const busy = new VoiceConnection(handlers(), {
      ...deps,
      fetchFn: (async () => jsonResponse({ detail: 'busy' }, 503)) as unknown as typeof fetch,
      createSocket: (url) => new FakeSocket(url),
    });
    await assert.rejects(busy.open(fakeStream()), (e: unknown) => e instanceof CallSetupError && e.error.kind === 'lines-busy');

    const offline = new VoiceConnection(handlers(), {
      ...deps,
      fetchFn: (async () => {
        throw new TypeError('Failed to fetch');
      }) as unknown as typeof fetch,
    });
    await assert.rejects(offline.open(fakeStream()), (e: unknown) => e instanceof CallSetupError && e.error.kind === 'server-unreachable');
  });
});
