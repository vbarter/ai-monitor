# ai-monitor

每小时扫一遍主流实验室的模型发布，写进 SQLite，只把新的官方发布推到 Slack。进程本身不常驻，由 systemd timer 每小时拉起一次 `python -m model_radar scan`。

这不是 dsplugin 的关键词雷达。这里只看基础模型发布。

## MVP 里有什么

- Hugging Face 机构白名单：`GET https://huggingface.co/api/models?author=…&sort=createdAt&direction=-1`。不要改成 `sort=created`，Hub 会回 400。
- OpenRouter 目录 JSON，外加官方 RSS `https://openrouter.ai/api/v1/models?use_rss=true`。没有 key 也能扫。
- OpenAI / Anthropic / Gemini / DeepSeek 的官方 list-models。对应环境变量为空就跳过，不算失败。
- `config.yaml` 里的 RSS/Atom。
- 指纹 `sha256(normalize(provider)|normalize(model_id)|normalize(version))`。
- 每个源第一次成功只入库、不告警。量化、LoRA、adapter、`ft:` 微调不进即时告警。
- Slack incoming webhook 只发 `official_release`、`weights_drop`、以及第一方 `catalog_add`。没有新发布不发「本小时无更新」。
- 空 body、错误 body 不会覆盖上次成功的目录快照。Slack 失败把告警留在 `pending`/`failed`，下一小时重试。
- 同一源连续失败 3 次发一条来源告警，恢复前不重复刷。

## 还没做

这些模块在仓库里留了位置，扫描不会请求它们：

- Ark、DashScope、Moonshot、Zhipu 的 list-models（`model_radar/adapters/ark.py` 等）
- 百度千帆、腾讯混元、小米 MiMo、MiniMax、DeepSeek updates、百炼新模型的 HTML changelog（`html_changelog.py`，`config.yaml` 里 `enabled: false`）
- 每天 08:00 Asia/Shanghai 的定时 digest。`digest` 命令可以手动跑，timer 没挂它
- 别名图。`aliases` 表只记了 `provider:model_id`，不会把 OpenRouter slug 和 HF repo 并成一条

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
| `OPENAI_API_KEY` | 空则跳过 `https://api.openai.com/v1/models` |
| `ANTHROPIC_API_KEY` | 空则跳过 `https://api.anthropic.com/v1/models` |
| `GEMINI_API_KEY` | 空则跳过 `https://generativelanguage.googleapis.com/v1beta/models`。key 放 header，不进 URL |
| `DEEPSEEK_API_KEY` | 空则跳过 `https://api.deepseek.com/models` |
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
