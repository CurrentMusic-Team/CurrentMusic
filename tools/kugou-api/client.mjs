export class ProbeError extends Error {
  constructor(kind, operation, code = null) {
    super([kind, operation, code].filter((part) => part !== null).join(' '));
    this.kind = kind;
  }
}

function failure(operation, response) {
  const body = response?.body;
  const code = Number(body?.error_code) || null;
  if (body?.msg?.code === 'ECONNABORTED' || body?.msg?.code === 'ETIMEDOUT') {
    return new ProbeError('timeout', operation);
  }
  if (code === 20028 || response?.headers?.['ssa-code']) {
    return new ProbeError('verification_required', operation, code);
  }
  if (code === 101 || response?.status === 401) {
    return new ProbeError('session_expired', operation, code);
  }
  return new ProbeError(code ? 'upstream_rejected' : 'upstream_unavailable', operation, code);
}

function recordCookies(cookie, response) {
  for (const item of response?.cookie ?? []) {
    const separator = item.indexOf('=');
    if (separator < 1) continue;
    const name = item.slice(0, separator);
    const value = item.slice(separator + 1);
    if (['dfid', 'token', 'userid', 'vip_type', 'vip_token', 't1', 'auth'].includes(name) && value !== 'undefined') {
      cookie[name] = value;
    }
  }
}

function normalizeTrack(item) {
  const hash = item.FileHash ?? item.Hash ?? item.hash;
  if (typeof hash !== 'string' || !/^[a-f\d]{32}$/i.test(hash)) return null;
  return {
    hash: hash.toLowerCase(),
    albumAudioId: String(item.MixSongID ?? item.AlbumAudioID ?? item.album_audio_id ?? ''),
    title: item.SongName ?? item.songname ?? item.FileName ?? '',
    artist: item.SingerName ?? item.AuthorName ?? item.singername ?? '',
    durationSec: Number(item.Duration ?? item.duration) || null,
  };
}

export function classifyPlayback(data, expectedSec) {
  const reportedDuration = Number(data?.timeLength ?? data?.timelength ?? data?.time_length);
  let durationSec = null;
  if (Number.isFinite(reportedDuration) && reportedDuration > 0) {
    durationSec = reportedDuration >= 10_000 ? reportedDuration / 1000 : reportedDuration;
  }
  const preview = [data?.is_free_part, data?.isFreePart, data?.is_preview].some((value) => value === true || Number(value) === 1);
  const values = [data?.url, data?.play_url].flat();
  const url = values.find((value) => typeof value === 'string' && URL.canParse(value) && new URL(value).protocol === 'https:') ?? null;
  if (preview || (durationSec && expectedSec && durationSec < expectedSec - 5)) {
    return { state: 'preview', durationSec, url: null };
  }
  if (!url) return { state: 'unavailable', durationSec, url: null };
  const state = durationSec && expectedSec && Math.abs(durationSec - expectedSec) <= 5 ? 'full_candidate' : 'unverified';
  return { state, durationSec, url };
}

export function createClient(upstream, mode) {
  let cookie = { ...upstream.device };
  let account = null;

  async function request(operation, args = {}) {
    let response;
    try {
      response = await upstream.request(operation, { ...args, cookie: { ...cookie } });
    } catch (error) {
      throw failure(operation, error);
    }
    const body = response?.body;
    if (response?.status !== 200 || !body || typeof body !== 'object' || body.status === 0 || Number(body.error_code)) {
      throw failure(operation, response);
    }
    return response;
  }

  function requireLogin() {
    if (!account) throw new ProbeError('login_required', 'account');
  }

  return {
    async sendCode(mobile) {
      const response = await request('send', { mobile });
      if (Number(response.body.status) !== 1) throw failure('send', response);
    },

    async login(mobile, code, expectedId) {
      account = null;
      cookie = { ...upstream.device };
      const response = await request('login', { mobile, code, userid: expectedId });
      const data = response.body.data;
      recordCookies(cookie, response);
      const userid = String(data?.userid ?? cookie.userid ?? '');
      const token = data?.token ?? cookie.token;
      if (!/^[1-9]\d*$/.test(userid) || !token) throw new ProbeError('no_session', 'login');
      if (expectedId && userid !== expectedId) throw new ProbeError('account_mismatch', 'login');
      cookie = { ...cookie, token, userid };
      account = { userid, matched: Boolean(expectedId) };
      return account;
    },

    async registerDevice() {
      requireLogin();
      const response = await request('device');
      const dfid = response.body.data?.dfid;
      if (!dfid) throw failure('device', response);
      cookie.dfid = dfid;
    },

    async search(keywords) {
      requireLogin();
      const { body } = await request('search', { keywords, type: 'song', pagesize: 10 });
      const items = body.data?.lists ?? body.data?.info;
      if (!Array.isArray(items)) throw failure('search', { body });
      return items.map(normalizeTrack).filter(Boolean);
    },

    async play(track, expectedSec) {
      requireLogin();
      let params = { hash: track.hash, quality: 128 };
      if (track.albumAudioId) params.album_audio_id = track.albumAudioId;
      let operation = 'play';
      if (mode === 'lite') {
        const user = (await request('userAuth')).body.data;
        if (!user?.auth) throw new ProbeError('authorization_unavailable', 'userAuth');
        const song = (await request('songAuth', { ...params, auth: user.auth })).body.data;
        if (!song?.auth || !song?.open_time) throw new ProbeError('authorization_unavailable', 'songAuth');
        params = { ...params, auth: song.auth, open_time: song.open_time };
        operation = 'authorizedPlay';
      }
      const { body } = await request(operation, params);
      return classifyPlayback(body.data ?? body, expectedSec);
    },
  };
}
