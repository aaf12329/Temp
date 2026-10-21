# -*- coding: utf-8 -*-
"""URL 归一化 —— 把「同一个页面」的不同写法收敛成一个键。

⚠️ **本阶段只建不调用**：抓取流程仍然直接使用原始 URL。
   归一化规则先写在这里定好，等「同一 URL 覆盖」这条规范真正接到采集流程时再启用。

============================== 函数结构树 ==============================
    normalize_url(url)       归一化：砍 #锚点、协议统一 https、域名小写、去掉默认端口
    url_key(url)             归一化结果直接当去重键用（就是 normalize_url）

============================== 调用关系总览 ==============================
（本阶段无调用方）

============================= 归一化规则（只做这些，不多洗）=============================
做的：
    ① 去掉 `#` 及其后内容（锚点不发给服务器，永远不是不同页面）
    ② 协议统一成 https（http / 无协议 都算 https）
    ③ 主机名小写
    ④ 去掉默认端口（:80 / :443）

不做的（可能真的是不同页面，洗了会误伤）：
    ✗ 不改路径（`/a.html` 与 `/a.html/` 视为不同）
    ✗ 不动 query（`?id=1` 保留，`?utm_source=` 也不删）
    ✗ 不补/删末尾 `/`

已知风险：有的站只有 http，归一化成 https 后请求会失败。
    → 所以启用时**抓取失败必须回退原始 URL 重试一次**，不能拿归一化地址硬顶。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """把 URL 归一化成去重键；无法解析的原样返回（宁可留重复，也不误合并、不抛异常）。"""
    if not url:
        return ""
    target = url.strip()
    if "://" not in target:
        target = "https://" + target
    try:
        parts = urlsplit(target)
        port = parts.port                              # 非法端口在这里就抛 ValueError
        host = (parts.hostname or "").lower()          # ③ 主机名小写
    except ValueError:
        return url.strip()                             # 解析不了就原样返回，绝不抛给调用方

    scheme = "https"                                  # ② 协议统一 https
    netloc = host
    if port and port != DEFAULT_PORTS.get(scheme) and port != DEFAULT_PORTS.get(parts.scheme):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))   # ① 丢锚点


def url_key(url: str) -> str:
    """去重键 = 归一化后的 URL。单独给个名字，调用方语义更清楚。"""
    return normalize_url(url)
