import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const operations = {
  send: 'captcha_sent',
  login: 'login_cellphone',
  device: 'register_dev',
  search: 'search',
  userAuth: 'user_verify',
  songAuth: 'song_auth',
  authorizedPlay: 'song_url_auth',
  play: 'song_url',
};

export function loadUpstream(directory, mode) {
  const require = createRequire(resolve(directory, 'package.json'));
  process.env.platform = mode === 'lite' ? 'lite' : '';
  const axios = require('axios');
  const { createRequest } = require('./util/request');
  const { getGuid, calculateMid, randomString } = require('./util/util');
  const guid = getGuid();
  const device = { KUGOU_API_GUID: guid, KUGOU_API_MID: calculateMid(guid), KUGOU_API_DEV: randomString(10) };

  axios.defaults.timeout = 8000;
  axios.defaults.maxRedirects = 0;
  axios.interceptors.request.use((config) => {
    const target = new URL(config.url, config.baseURL);
    if (target.protocol !== 'https:' || !target.hostname.endsWith('.kugou.com')) {
      throw new Error('Unexpected upstream destination');
    }
    config.proxy = false;
    return config;
  });

  return {
    device,
    request(operation, parameters) {
      const handler = require(`./module/${operations[operation]}.js`);
      return handler(parameters, (options) => {
        if (operation === 'send') {
          options = { ...options, baseURL: 'https://loginserviceretry.kugou.com' };
        } else if (/^http:\/\/gateway\.kugou\.com\/?$/.test(options.baseURL ?? '')) {
          options = { ...options, baseURL: 'https://gateway.kugou.com' };
        }
        return createRequest(options);
      });
    },
  };
}
