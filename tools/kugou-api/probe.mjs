import { createInterface } from 'node:readline/promises';
import { Writable } from 'node:stream';
import { parseArgs } from 'node:util';
import { createClient, ProbeError } from './client.mjs';
import { loadUpstream } from './upstream.mjs';

const usage = '用法：node tools/kugou-api/probe.mjs --upstream /path/to/KuGouMusicApi [--mode standard|lite]';
const stdout = process.stdout;

function terminal() {
  let hidden = false;
  const output = new Writable({
    write(chunk, encoding, done) {
      if (!hidden) stdout.write(chunk, encoding);
      done();
    },
  });
  const input = createInterface({ input: process.stdin, output, terminal: true });
  return {
    async question(message, secret = false) {
      hidden = secret;
      if (hidden) stdout.write(message);
      try {
        return (await input.question(secret ? '' : message)).trim();
      } finally {
        if (hidden) stdout.write('\n');
        hidden = false;
      }
    },
    close: () => input.close(),
  };
}

async function checkTrack(client, prompt, label) {
  const keywords = await prompt.question(`${label}，输入歌手和歌名，留空跳过：`);
  if (!keywords) return;
  let tracks;
  try {
    tracks = await client.search(keywords);
    if (!tracks.length) {
      console.log('搜索无匹配结果。');
      return;
    }
    tracks.forEach((track, index) => {
      console.log(`${index + 1}. ${track.artist} ${track.title} (${track.durationSec ?? '?'} 秒) [${track.hash}]`);
    });
    const track = tracks[Number(await prompt.question('输入同一录音版本的编号：')) - 1];
    if (!track) return;
    const duration = await prompt.question('免费版显示的全曲时长（秒，留空用搜索结果）：');
    const expectedSec = Number(duration || track.durationSec);
    if (!Number.isFinite(expectedSec) || expectedSec <= 0) {
      console.log('缺少全曲时长，跳过该曲。');
      return;
    }
    const result = await client.play(track, expectedSec);
    console.log(`${result.state}，返回时长 ${result.durationSec ?? '未知'} 秒，标准音质。`);
    if (result.url && (await prompt.question('在本地终端显示临时 URL，供手动播放到曲尾？ [y/N] ')).toLowerCase() === 'y') {
      console.log(result.url);
      const played = await prompt.question('实际听到曲尾并且时长一致？ [y/N] ');
      if (played.toLowerCase() === 'y') console.log('full_confirmed，本次全曲已听到曲尾。');
    }
  } catch (error) {
    if (!(error instanceof ProbeError) || error.kind === 'session_expired') throw error;
    console.error(error.message);
  }
}

async function main() {
  const { values } = parseArgs({ options: {
    upstream: { type: 'string' },
    mode: { type: 'string', default: 'standard' },
    help: { type: 'boolean' },
  } });
  if (values.help) {
    console.log(usage);
    return;
  }
  if (!values.upstream || !['standard', 'lite'].includes(values.mode) || !process.stdin.isTTY) {
    throw new ProbeError('invalid_arguments', usage);
  }

  let upstream;
  try {
    upstream = loadUpstream(values.upstream, values.mode);
  } catch {
    throw new ProbeError('upstream_setup_failed', 'KuGouMusicApi');
  }
  const client = createClient(upstream, values.mode);
  const prompt = terminal();
  try {
    const mobile = await prompt.question('手机号：', true);
    if (!/^1\d{10}$/.test(mobile)) throw new ProbeError('invalid_mobile', 'login');
    const expectedId = await prompt.question('免费版的酷狗 ID（可留空）：', true);
    if (expectedId && !/^[1-9]\d*$/.test(expectedId)) throw new ProbeError('invalid_account_id', 'login');
    await client.sendCode(mobile);
    const code = await prompt.question('酷狗短信验证码：', true);
    if (!/^\d{4,8}$/.test(code)) throw new ProbeError('invalid_sms_code', 'login');
    const account = await client.login(mobile, code, expectedId);
    if (account.matched) console.log('账号 ID 已核对。');
    else console.log(`已登录，账号末四位 ${account.userid.slice(-4)}，免费版身份待核对。`);

    await client.registerDevice();
    await checkTrack(client, prompt, '普通免费歌');
    await checkTrack(client, prompt, '权益歌曲 1');
    await checkTrack(client, prompt, '权益歌曲 2');
    console.log('完整播放结果只记录本次账号和曲目。权益归因需与同接口的无权益账号对照，或取得上游权益信息。');
  } finally {
    prompt.close();
  }
}

main().catch((error) => {
  console.error(error instanceof ProbeError ? error.message : '探测中止，请检查运行环境或输入。');
  process.exitCode = 1;
});
