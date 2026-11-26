import { call } from './api.js';

export function socialCall(method, path, body) {
  return call(method, '/social' + path, { auth: true, body });
}

export async function openDirect(userId) {
  const { conversation } = await socialCall('POST', '/conversations', { kind: 'direct', userId });
  location.hash = socialURL(conversation.id);
}

export function socialURL(id) {
  return '#/square?tab=social' + (id ? '&conversation=' + id : '');
}
