# 酷狗个人播放 API

在普通版和概念版接口上核对酷狗账号、搜索歌曲，并验证免费版的畅听曲目能否播放到曲尾。每次运行只使用一种模式。酷狗手机号、验证码和会话在本机交互输入或保存在进程内存中。

## 启动

需要 Node.js 22 和 pnpm。把测试所用的 [KuGouMusicApi](https://github.com/MakcRe/KuGouMusicApi) 版本放在 CurrentMusic 工作树外。

```sh
git clone https://github.com/MakcRe/KuGouMusicApi.git ../KuGouMusicApi
git -C ../KuGouMusicApi checkout --detach 443d4a416dd344ccf7b022b9fb932e324afc77b4
pnpm --dir ../KuGouMusicApi install --prod --frozen-lockfile --ignore-scripts
node tools/kugou-api/probe.mjs --upstream ../KuGouMusicApi
```

优先试普通版。需要验证概念版时，把 `--mode lite` 加到启动命令末尾，并在新会话里完成短信登录。免费版设置页能查看酷狗 ID 时，在提示处填写该 ID，便于选中同一手机号关联的正确账号。

## 本地 HTTP API

API 固定使用概念版的已验证播放路径，默认监听 `127.0.0.1:4017`。把自选的本地密钥放在 `KUGOU_LOCAL_API_KEY` 环境变量中，至少 24 个字符。启动后在终端完成短信登录，重启时重新登录。

```sh
node tools/kugou-api/probe.mjs --upstream ../KuGouMusicApi --serve
```

调用方在 `Authorization` 请求头中带上同一个本地密钥。

```sh
curl --get --data-urlencode 'q=周杰伦 晴天' \
  -H "Authorization: Bearer $KUGOU_LOCAL_API_KEY" \
  http://127.0.0.1:4017/v1/search
```

- `GET /health` 检查进程状态。
- `GET /v1/search?q=…` 返回 `tracks` 数组，每项包括 `hash`、`albumAudioId`、`title`、`artist` 和 `durationSec`。
- `GET /v1/tracks/{hash}/play?albumAudioId=…&durationSec=…` 使用搜索结果中的录音标识和时长，返回 `url` 与 `durationSec`。仅试听或时长无法确认时返回 `422`，会话失效时返回 `401`。

酷狗可能下发只能通过 HTTP 访问的临时音频地址。API 将地址交给本地调用方，地址不包含接口使用的酷狗登录 Cookie。该路径已实测能播放完整曲目，365 天活动权益是否影响播放仍取决于酷狗的授权逻辑。

## 判断播放结果

挑一首普通免费歌和两首免费版依靠畅听权益才能播全曲的歌。用歌手、歌名、歌曲时长确认是同一录音。工具请求标准音质，输入免费版显示的全曲时长可以直接比较。

| 结果 | 含义 |
| --- | --- |
| `preview` | 上游标记为试听，或返回时长明显短于全曲 |
| `preview_fallback` | 普通播放无地址，启用试听参数后才有返回 |
| `unavailable` | 没有可识别的播放地址 |
| `http_only` | 上游只给出 HTTP 播放地址 |
| `unverified` | 有播放地址，返回时长未知或与对照不一致 |
| `full_candidate` | 有播放地址，返回时长与对照一致 |
| `full_confirmed` | 操作者确认在本地播放器中播放到了曲尾 |

有地址的曲目可在本机终端选择显示临时 URL，再用播放器验证。验证 365 天权益本身需要上游明确标识该权益，或在同一接口、歌曲版本和音质下用无权益账号对照。出现 `verification_required` 时，按酷狗正常的验证流程操作。

```sh
node --test tools/kugou-api/client.test.mjs
```

上游 KuGouMusicApi 由 MakcRe 维护，使用 [MIT 许可](https://github.com/MakcRe/KuGouMusicApi/blob/443d4a416dd344ccf7b022b9fb932e324afc77b4/LICENSE)。
