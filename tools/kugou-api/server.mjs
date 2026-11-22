import { timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { ProbeError } from './client.mjs';

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(body));
}

export async function startServer(client, secret, port = 4017) {
  const expected = Buffer.from(`Bearer ${secret}`);
  const host = '127.0.0.1';
  const server = createServer(async (request, response) => {
    if (request.headers.host !== `${host}:${server.address().port}` || request.headers.origin) {
      return json(response, 403, { error: 'local_requests_only' });
    }
    if (request.method !== 'GET') {
      response.setHeader('Allow', 'GET');
      return json(response, 405, { error: 'method_not_allowed' });
    }
    if (!request.url?.startsWith('/') || request.url.startsWith('//')) {
      return json(response, 400, { error: 'invalid_request_target' });
    }
    const url = new URL(request.url, `http://${host}`);
    if (url.pathname === '/health') return json(response, 200, { status: 'ok' });

    const supplied = Buffer.from(request.headers.authorization ?? '');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      return json(response, 401, { error: 'local_auth_required' });
    }

    try {
      if (url.pathname === '/v1/search') {
        const query = url.searchParams.get('q')?.trim();
        if (!query || query.length > 200) return json(response, 400, { error: 'invalid_query' });
        return json(response, 200, { tracks: await client.search(query) });
      }

      const match = /^\/v1\/tracks\/([a-f\d]{32})\/play$/i.exec(url.pathname);
      if (!match) return json(response, 404, { error: 'not_found' });
      const albumAudioId = url.searchParams.get('albumAudioId');
      const durationSec = Number(url.searchParams.get('durationSec'));
      if (!/^[1-9]\d{0,15}$/.test(albumAudioId ?? '') || !Number.isFinite(durationSec) || durationSec <= 0) {
        return json(response, 400, { error: 'track_metadata_required' });
      }
      const playback = await client.play({ hash: match[1].toLowerCase(), albumAudioId }, durationSec);
      if (playback.state !== 'full_candidate') return json(response, 422, { error: playback.state });
      return json(response, 200, { url: playback.url, durationSec: playback.durationSec });
    } catch (error) {
      const kind = error instanceof ProbeError ? error.kind : 'upstream_unavailable';
      const status = kind === 'session_expired' ? 401 : kind === 'timeout' ? 504 : 502;
      return json(response, status, { error: kind });
    }
  });
  server.listen(port, host);
  await once(server, 'listening');
  return server;
}
