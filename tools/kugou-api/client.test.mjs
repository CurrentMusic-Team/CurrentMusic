import assert from 'node:assert/strict';
import test from 'node:test';
import { classifyPlayback, createClient } from './client.mjs';

const hash = 'abcdef0123456789abcdef0123456789';
const audio = 'https://cdn.example.org/song.mp3';
const sample = { FileHash: hash, MixSongID: 30, SongName: '示例', SingerName: '示例歌手', Duration: 210 };
const response = (data, cookie = []) => ({ status: 200, body: { status: 1, data }, cookie });

function mockClient(overrides = {}, mode = 'standard') {
  const calls = [];
  const defaults = {
    send: () => response({}),
    login: () => response({ userid: 12345, token: 'fixture-only' }),
    device: () => response({ dfid: 'device-fixture' }),
    search: () => response({ lists: [sample] }),
    play: () => response({ url: [audio], timeLength: 210_000 }),
  };
  const client = createClient({
    device: { KUGOU_API_GUID: 'guid-fixture' },
    request(operation, args) {
      calls.push([operation, args]);
      const handler = overrides[operation] ?? defaults[operation];
      assert.ok(handler, `unexpected operation ${operation}`);
      return handler(args);
    },
  }, mode);
  return { client, calls };
}

test('passes the selected account and keeps session details in the client', async () => {
  const { client, calls } = mockClient();
  await client.sendCode('13800138000');
  assert.deepEqual(await client.login('13800138000', '000000', '12345'), { userid: '12345', matched: true });
  await client.registerDevice();
  const [track] = await client.search('示例');
  assert.deepEqual(track, { hash, albumAudioId: '30', title: '示例', artist: '示例歌手', durationSec: 210 });
  assert.deepEqual(await client.play(track, 210), { state: 'full_candidate', durationSec: 210, url: audio });
  const args = calls.find(([name]) => name === 'play')[1];
  assert.equal(args.album_audio_id, '30');
  assert.equal(args.cookie.dfid, 'device-fixture');
  assert.equal(calls.find(([name]) => name === 'login')[1].userid, '12345');
});

test('rejects an unexpected account and leaves no usable session', async () => {
  const { client } = mockClient();
  await assert.rejects(client.login('13800138000', '000000', '99999'), { kind: 'account_mismatch' });
  await assert.rejects(client.search('示例'), { kind: 'login_required' });
});

test('sanitizes invalid code errors and response content', async () => {
  const { client } = mockClient({ login: () => { throw { status: 502, body: { error_code: 20010, msg: 'raw input' } }; } });
  await assert.rejects(client.login('13800138000', '000000'), { message: 'upstream_rejected login 20010' });
});

test('distinguishes expired sessions, verification requests and timeouts', async () => {
  const failures = [
    [{ status: 401 }, 'session_expired'],
    [{ body: { error_code: 20028 }, headers: { 'ssa-code': 'private-value' } }, 'verification_required'],
    [{ body: { msg: { code: 'ECONNABORTED', url: audio } } }, 'timeout'],
  ];
  for (const [failure, kind] of failures) {
    const { client } = mockClient({ search: () => { throw failure; } });
    await client.login('13800138000', '000000');
    await assert.rejects(client.search('示例'), { kind });
  }
});

test('distinguishes a preview, a duration match and an unverified URL', () => {
  assert.equal(classifyPlayback({ url: audio, isFreePart: 1 }, 210).state, 'preview');
  assert.deepEqual(classifyPlayback({ url: audio, timelength: 30_000 }, 210), { state: 'preview', durationSec: 30, url: null });
  assert.equal(classifyPlayback({ url: audio, timeLength: 210 }, 210).state, 'full_candidate');
  assert.equal(classifyPlayback({ url: audio }, 210).state, 'unverified');
  assert.equal(classifyPlayback({ url: 'http://cdn.example.org/song.mp3' }, 210).state, 'unavailable');
});

test('does not return playback URLs obtained only after requesting preview', async () => {
  const { client, calls } = mockClient({
    play: ({ free_part }) => response(free_part ? { url: audio, timeLength: 210 } : {}),
  });
  await client.login('13800138000', '000000');
  assert.deepEqual(await client.play({ hash }, 210), { state: 'preview_fallback', durationSec: null, url: null });
  assert.equal(calls[2][1].free_part, true);
});

test('requests song authorization in lite mode and uses that mode throughout', async () => {
  const { client, calls } = mockClient({
    userAuth: () => response({ auth: 'user-auth-fixture' }),
    songAuth: () => response({ auth: 'song-auth-fixture', open_time: 123 }),
    authorizedPlay: () => response({ play_url: audio, timeLength: 210_000 }),
  }, 'lite');
  await client.login('13800138000', '000000');
  assert.equal((await client.play({ hash }, 210)).state, 'full_candidate');
  assert.deepEqual(calls.map(([name]) => name), ['login', 'userAuth', 'songAuth', 'authorizedPlay']);
  assert.equal(calls[2][1].auth, 'user-auth-fixture');
  assert.equal(calls[3][1].auth, 'song-auth-fixture');
  assert.equal(calls[3][1].open_time, 123);
});
