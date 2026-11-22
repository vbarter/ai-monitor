"""Public HTML changelogs. Later slice: not called.

Targets: Baidu Qianfan update log, Tencent Hunyuan open-source model page,
Xiaomi MiMo news, MiniMax model release notes, DeepSeek updates, Alibaba
Bailian newly-released models. One request per page per hour, honor
robots.txt, no login, no cookie jar, no bypass of 403 or geo-blocks.
"""

SLICE = "later"
ADAPTER = "html_changelog"
IMPLEMENTED = False

PAGES = (
    "https://cloud.baidu.com/doc/qianfan/s/Kmh4stnjp",
    "https://hunyuan.tencent.com/model/open-source-model",
    "https://mimo.mi.com/docs/zh-CN/news",
    "https://platform.minimax.io/docs/release-notes/models",
    "https://api-docs.deepseek.com/zh-cn/updates/",
    "https://help.aliyun.com/zh/model-studio/newly-released-models",
    "https://platform.kimi.ai/docs/platform-changelog",
    "https://docs.z.ai/release-notes/new-released",
)
