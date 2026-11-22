"""Volcengine Ark list models. Later slice: not called.

Official endpoint, Bearer token, do not scrape the console:
GET https://ark.cn-beijing.volces.com/api/v3/models
Authorization: Bearer $ARK_API_KEY

The signed RPC ListFoundationModels is the fallback if the Bearer list fails.
"""

SLICE = "later"
ADAPTER = "ark"
IMPLEMENTED = False
OFFICIAL_URL = "https://ark.cn-beijing.volces.com/api/v3/models"
