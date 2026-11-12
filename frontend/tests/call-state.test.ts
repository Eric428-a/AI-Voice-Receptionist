import './setup.js';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { CallStateMachine, RING_DURATION_MS, RingCancelledError, RingingSequence } from '../src/state/call-state.js';
import { advance, useFakeTimers, useRealTimers } from './fakes.js';

describe('CallStateMachine', () => {
  it('follows the documented call lifecycle', () => {
    const machine = new CallStateMachine();
    const seen: string[] = [];
    machine.subscribe((state) => seen.push(state));
    const path = ['initiating', 'connected', 'speaking', 'listening', 'thinking', 'speaking', 'interrupted', 'listening', 'ended', 'ready'] as const;
    for (const state of path) assert.equal(machine.transition(state), true);
    assert.deepEqual(seen, [...path]);
  });

  it('rejects illegal transitions', () => {
    const machine = new CallStateMachine();
    assert.equal(machine.transition('speaking'), false);
    assert.equal(machine.transition('ended'), false);
    assert.equal(machine.state, 'ready');
    assert.equal(machine.inCall, false);
  });

  it('unsubscribes listeners', () => {
    const machine = new CallStateMachine();
    let calls = 0;
    const off = machine.subscribe(() => {
      calls += 1;
    });
    off();
    machine.transition('initiating');
    assert.equal(calls, 0);
  });
});

describe('RingingSequence', () => {
  beforeEach(useFakeTimers);
  afterEach(useRealTimers);

  it('rings for exactly five seconds', async () => {
    assert.equal(RING_DURATION_MS, 5000);
    const ticks: number[] = [];
    let done = false;
    void new RingingSequence(RING_DURATION_MS, (s) => ticks.push(s)).start().then(() => {
      done = true;
    });
    await advance(4999, 1);
    assert.equal(done, false);
    await advance(1, 1);
    assert.equal(done, true);
    assert.deepEqual(ticks, [5, 4, 3, 2, 1]);
  });

  it('can be cancelled and stops ticking', async () => {
    const ticks: number[] = [];
    const ring = new RingingSequence(5000, (s) => ticks.push(s));
    const promise = ring.start();
    ring.cancel();
    await assert.rejects(promise, RingCancelledError);
    await advance(6000, 100);
    assert.deepEqual(ticks, [5]);
  });
});
