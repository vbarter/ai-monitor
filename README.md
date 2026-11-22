# ai-monitor

每小时扫一遍主流实验室的模型发布，写进 SQLite，只把新的官方发布推到 Slack。进程本身不常驻，由 systemd timer 每小时拉起一次 `python -m model_radar scan`。

这不是 dsplugin 的关键词雷达。这里只看基础模型发布。

## 主路径

开源权重看 Hugging Face 机构白名单：`GET https://huggingface.co/api/models?author=…&sort=createdAt&direction=-1`。不要改成 `sort=created`，Hub 会回 400。

闭源和官方发布看博客 / RSS / changelog，跟 HF 走同一次 `scan`，systemd timer 不用改。

已接上的 RSS（用这些 URL，不要再加一份）：

- `https://huggingface.co/blog/feed.xml`
- `https://qwenlm.github.io/blog/index.xml`
- `https://openai.com/news/rss.xml`（`openai.com/blog/rss.xml` 会跳到这里，配置里不重复写）
- `https://docs.z.ai/release-notes/new-released/rss.xml`
- `https://deepmind.google/blog/rss.xml`（这是还活着的那条。`https://deepmind.google/discover/blog/rss.xml` 是 404，不要加）
- OpenRouter 官方 RSS，默认只入库

已接上的公开页面（无登录，403 / 地区墙不绕）：

- `https://www.deepseek.com/en/news/`
- `https://api-docs.deepseek.com/updates.md`（给人看的 HTML 是 `https://api-docs.deepseek.com/updates`）
- `https://www.kimi.ai/blog/`
- `https://www.minimax.io/news`
- `https://www.anthropic.com/news`
- `https://ai.meta.com/blog/`

`https://mimo.xiaomi.com/blog` 关着。2026-11-22 拉下来是当前旗舰的单篇落地页，标题下面是 “Benchmark Comparison” 这种章节，不是文章列表。硬解析会把章节当发布。小米开源权重继续走 HF `XiaomiMiMo`。

OpenAI / Anthropic / Gemini / DeepSeek 的 list-models 代码还在，`config.yaml` 里 `enabled: false`。这不是主路径。要开的话自己改配置，并且 key 为空仍然跳过。OpenRouter 目录还在，默认不推 Slack。

指纹 `sha256(normalize(provider)|normalize(model_id)|normalize(version))`。每个源第一次成功只入库（`notified=baseline`）。量化、LoRA、adapter、`ft:` 不进即时告警。Slack 只发 `official_release`、`weights_drop`、第一方 `catalog_add`。没有新发布不发「本小时无更新」。空 body / 错误 body 不覆盖上次快照。Slack 失败留到下一小时。同一源连续失败 3 次发一条来源告警。

## X 不在这个仓库里

官方 X 账号由 Grok Bot 的每小时任务在仓库外面盯。这个 Python 服务没有 X/Twitter 客户端，不带 bearer token，不抓 x.com，也不走 Nitter 或第三方推文 RSS。

只认这些号（2026-11-22 用 X user lookup 对过）。旁边的仿号不要加：

| 实验室 | 账号 |
| --- | --- |
| OpenAI | @OpenAI、@OpenAIDevs |
| Anthropic | @AnthropicAI、@claudeai |
| Gemini / Google | @GoogleDeepMind 为主。@GoogleAI、@GeminiApp 是更吵的产品和品牌号 |
| Meta | 只认 @AIatMeta。@metaai 是迁走后的空壳 |
| Hugging Face | @huggingface，可选的新闻补充 |
| DeepSeek | @deepseek_ai，他们自己说这是唯一官方号 |
| Kimi | @Kimi_Moonshot |
| Qwen | @Alibaba_Qwen |
| 小米 MiMo | @XiaomiMiMo；API 动态看 @XiaomiMiMoDevs |
| Z.ai / GLM | 只认 @Zai_org |
| MiniMax | @MiniMax_AI（简介写了 official，未认证） |
| ByteDance Seed | @ByteDanceSeed_（官方号，帖子还很少；开源权重仍以 HF `ByteDance-Seed` 为准） |
| 百度 | @Baidu_ERNIE |
| 腾讯混元 | @TencentHunyuan |

明确不收：@Anthropic（无关个人）、@ZhipuAI、@ChatGLM、@metaai、@MoonshotAi、@minimaxAI、@bytedanceSeed。

## 还没做

- Ark、DashScope、Moonshot、Zhipu 的 list-models
- 千帆、混元、百炼、MiMo 文档站这些还没验证成稳定文章索引的 HTML，配置里关着
- 每天 08:00 Asia/Shanghai 的定时 digest。`digest` 可以手动跑
- 别名图。同一发布出现在 HF 和博客上，现在可能各报一次

## 安装

Python 3.12。

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，至少填 SLACK_WEBHOOK_URL。webhook 自己决定频道，配置里的 #自媒体 只是记录。
.venv/bin/python -m model_radar init-db --config config.yaml
```

密钥只放 `.env` 或 systemd `EnvironmentFile`。`config.yaml` 可以提交，里面没有 webhook，也没有 key。

| 变量 | 作用 |
| --- | --- |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook。空着则告警留在库里，扫描照常退出 0 |
| `HF_TOKEN` | 可选。公开机构不填也能扫 |
| `OPENAI_API_KEY` | list-models 默认关。打开 `providers.openai.enabled` 后，空 key 仍跳过 `https://api.openai.com/v1/models` |
| `ANTHROPIC_API_KEY` | 同上，`https://api.anthropic.com/v1/models` |
| `GEMINI_API_KEY` | 同上，`https://generativelanguage.googleapis.com/v1beta/models`。key 放 header，不进 URL |
| `DEEPSEEK_API_KEY` | 同上，`https://api.deepseek.com/models`。DeepSeek 的主路径是新闻页和 `updates.md` |
| `OPENROUTER_API_KEY` | 可选。不填也扫公开目录和 RSS |
| `ARK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` / `ZHIPU_API_KEY` | 后续切片，MVP 不读 |

数据库默认 `data/model_radar.sqlite`。`--db` 优先于 `MODEL_RADAR_DB`，再优先于配置。删掉这个文件再扫，会重新做安静基线。

## 命令

```bash
.venv/bin/python -m model_radar scan --config config.yaml
.venv/bin/python -m model_radar scan --only hf:Qwen
.venv/bin/python -m model_radar.scan --config config.yaml
.venv/bin/python -m model_radar digest --config config.yaml
.venv/bin/python -m model_radar digest --send --config config.yaml
```

`scan` 某个源失败也退出 0，除非配置或数据库本身坏了（退出 2）。第一次成功的源会写很多 `notified=baseline` 的行，Slack 不会响。

OpenRouter 上新出现的模型记成 `catalog_add`，默认不推 Slack（路由器不是第一方）。要推的话把 `alerts.router_catalog_immediate` 设成 `true`。

`digest` 不带 `--send` 只打印还没通知的 changelog、非第一方目录新增、价格/上下文变更和来源恢复。没有内容时不打印、不请求 Slack。

## systemd

`deploy/model-radar.timer` 每小时跑的就是 `python -m model_radar scan`。上面的 RSS 和页面已经写在 `config.yaml` 里，部署后这条 timer 会一起扫，不用再加单元。

宿主时区设成上海，timer 的整点才和展示时间一致：

```bash
sudo timedatectl set-timezone Asia/Shanghai
sudo useradd --system --create-home --home-dir /opt/ai-monitor ai-monitor || true
sudo mkdir -p /opt/ai-monitor
sudo rsync -a --exclude .venv --exclude data ./ /opt/ai-monitor/
sudo python3 -m venv /opt/ai-monitor/.venv
sudo /opt/ai-monitor/.venv/bin/pip install /opt/ai-monitor
sudo cp /opt/ai-monitor/.env.example /opt/ai-monitor/.env
sudo chown -R ai-monitor:ai-monitor /opt/ai-monitor
# 填 /opt/ai-monitor/.env
sudo cp deploy/model-radar.service deploy/model-radar.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now model-radar.timer
sudo systemctl start model-radar.service   # 先跑一遍基线
```

单元文件假定代码在 `/opt/ai-monitor`，虚拟环境是 `/opt/ai-monitor/.venv`。`TimeoutStartSec=600`。

不想用 systemd 时，看 `deploy/crontab.example`（`TZ=Asia/Shanghai`，每小时第 0 分）。

## 测试

不访问网络，也不需要真 key。

```bash
.venv/bin/pytest
```

## 许可

MIT。见 `LICENSE`。

安静基线、机构白名单、量化/adapter 排除、空目录不覆盖快照，这些是公开监控器里常见的做法，这里的代码是按这个仓库的设计自己写的，没有从 ai-model-radar、ModelMonitor、dsh-plugin-model-news-radar、ModelWatcher 拷代码。ai-model-watch（MIT）只当作目录 diff 的参考，不是依赖。
