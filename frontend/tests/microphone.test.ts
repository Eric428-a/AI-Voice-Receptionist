import './setup.js';
import assert from 'node:assert/strict';
import { describe, it, mock } from 'node:test';
import { MICROPHONE_CONSTRAINTS, MicrophoneError, requestMicrophone } from '../src/services/microphone-permissions.js';
import { assertCalledWith, fakeStream } from './fakes.js';

function nav(getUserMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>): Navigator {
  return { mediaDevices: getUserMedia ? { getUserMedia } : undefined } as unknown as Navigator;
}

describe('microphone permissions', () => {
  it('requests audio with echo cancellation enabled', async () => {
    const stream = fakeStream();
    const getUserMedia = mock.fn(async (_c: MediaStreamConstraints) => stream);
    assert.equal(await requestMicrophone(nav(getUserMedia)), stream);
    assertCalledWith(getUserMedia, MICROPHONE_CONSTRAINTS);
    const audio = MICROPHONE_CONSTRAINTS.audio as MediaTrackConstraints;
    assert.equal(audio.echoCancellation, true);
    assert.equal(audio.noiseSuppression, true);
  });

  it('explains how to fix a denied permission', async () => {
    const denied = async (): Promise<MediaStream> => {
      throw new DOMException('denied', 'NotAllowedError');
    };
    await assert.rejects(requestMicrophone(nav(denied)), (error: unknown) => {
      assert.ok(error instanceof MicrophoneError);
      assert.equal(error.kind, 'microphone-denied');
      assert.match(error.message, /site settings/);
      return true;
    });
  });

  it('handles a missing microphone and unsupported browsers', async () => {
    const missing = async (): Promise<MediaStream> => {
      throw new DOMException('none', 'NotFoundError');
    };
    await assert.rejects(requestMicrophone(nav(missing)), (e: unknown) => (e as MicrophoneError).kind === 'microphone-unavailable');
    await assert.rejects(requestMicrophone(nav()), (e: unknown) => (e as MicrophoneError).kind === 'unsupported-browser');
  });
});
