"""DashScope / Model Studio list models. Later slice: not called.

GET https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/models
Authorization: Bearer $DASHSCOPE_API_KEY

Do not guess a workspace host. `workspace_id` in config has to be set first.
"""

SLICE = "later"
ADAPTER = "dashscope"
IMPLEMENTED = False
OFFICIAL_URL = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/models"
