import './setup.js';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { WebSocketClient } from '../src/services/websocket-client.js';
import { parseServerMessage } from '../src/types/websocket.js';
import { advance, assertCalledWith, callCount, FakeSocket, useFakeTimers, useRealTimers } from './fakes.js';

describe('protocol parsing', () => {
  it('accepts every well-formed server message', () => {
    assert.deepEqual(parseServerMessage('{"type":"state","state":"thinking","response_id":2}'), {
      type: 'state', state: 'thinking', response_id: 2,
    });
    assert.equal(parseServerMessage('{"type":"response.interrupted","response_id":3,"reason":"barge_in"}')?.type, 'response.interrupted');
    assert.deepEqual(parseServerMessage('{"type":"pong"}'), { type: 'pong' });
    assert.deepEqual(parseServerMessage('{"type":"response.sentence","response_id":1,"text":"Hi there."}'), {
      type: 'response.sentence', response_id: 1, text: 'Hi there.',
    });
  });

  it('rejects malformed or unknown messages', () => {
    assert.equal(parseServerMessage('not json'), null);
    assert.equal(parseServerMessage('{"type":"unknown"}'), null);
    assert.equal(parseServerMessage('{"type":"state","state":"dancing"}'), null);
    assert.equal(parseServerMessage('{"type":"vad","speaking":"yes"}'), null);
    assert.equal(parseServerMessage('[]'), null);
  });
});

describe('WebSocketClient', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    useFakeTimers();
  });
  afterEach(useRealTimers);

  function setup() {
    const handlers = { onMessage: mock.fn(), onBinary: mock.fn(), onClose: mock.fn() };
    const client = new WebSocketClient('ws://test/api/ws/calls/x', handlers, (url) => new FakeSocket(url));
    return { client, handlers };
  }

  const pings = (socket: FakeSocket): number => socket.jsonSent().filter((m) => m.type === 'ping').length;

  it('connects, dispatches text and binary frames, and sends heartbeats', async () => {
    const { client, handlers } = setup();
    const connected = client.connect();
    const socket = FakeSocket.instances[0];
    assert.equal(socket.binaryType, 'arraybuffer');
    socket.open();
    await connected;
    socket.receive({ type: 'state', state: 'listening', response_id: null });
    socket.receive({ type: 'bogus' });
    socket.receiveBinary(new ArrayBuffer(4));
    assert.equal(callCount(handlers.onMessage), 1);
    assert.equal(callCount(handlers.onBinary), 1);
    await advance(15000, 500);
    assert.equal(pings(socket), 1);
    client.close();
  });

  it('reports unexpected drops but not intentional closes', async () => {
    const first = setup();
    const p1 = first.client.connect();
    FakeSocket.instances[0].open();
    await p1;
    FakeSocket.instances[0].drop();
    assertCalledWith(first.handlers.onClose, 1006, false);

    const second = setup();
    const p2 = second.client.connect();
    const socket = FakeSocket.instances[1];
    socket.open();
    await p2;
    second.client.close();
    assert.equal(callCount(second.handlers.onClose), 0);
    assert.equal(second.client.send({ type: 'ping' }), false);
    await advance(40000, 1000);
    assert.equal(pings(socket), 0); // heartbeat stopped
  });

  it('rejects when the connection fails or times out', async () => {
    const failing = setup();
    const p1 = failing.client.connect();
    FakeSocket.instances[0].onerror?.(new Event('error'));
    await assert.rejects(p1, /failed/);

    const slow = setup();
    const p2 = slow.client.connect(1000);
    const assertion = assert.rejects(p2, /timed out/);
    await advance(1000, 100);
    await assertion;
  });
});
