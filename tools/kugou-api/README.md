# 酷狗个人播放探测

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

## 判断播放结果

挑一首普通免费歌和两首免费版依靠畅听权益才能播全曲的歌。用歌手、歌名、歌曲时长确认是同一录音。工具请求标准音质，输入免费版显示的全曲时长可以直接比较。

| 结果 | 含义 |
| --- | --- |
| `preview` | 上游标记为试听，或返回时长明显短于全曲 |
| `unavailable` | 没有可用的 HTTPS 播放地址 |
| `unverified` | 有播放地址，返回时长未知或与对照不一致 |
| `full_candidate` | 有播放地址，返回时长与对照一致 |
| `full_confirmed` | 操作者确认在本地播放器中播放到了曲尾 |

有地址的曲目可在本机终端选择显示临时 URL，再用播放器验证。验证 365 天权益本身需要上游明确标识该权益，或在同一接口、歌曲版本和音质下用无权益账号对照。出现 `verification_required` 时，按酷狗正常的验证流程操作。

```sh
node --test tools/kugou-api/client.test.mjs
```

上游 KuGouMusicApi 由 MakcRe 维护，使用 [MIT 许可](https://github.com/MakcRe/KuGouMusicApi/blob/443d4a416dd344ccf7b022b9fb932e324afc77b4/LICENSE)。
