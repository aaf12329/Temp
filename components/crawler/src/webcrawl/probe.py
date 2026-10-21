# -*- coding: utf-8 -*-
"""探测判定 —— 每个 URL 产出一份 profile（8 选 1 的 verdict），落盘后供 route.py 选工具。

本文件是「判定层」：只判定、不采集。合规（robots）与渲染（静态 / 动态）复用
web_check 现成实现，HTTP 只走 net.fetch，落盘只走 io_utils，本文件零 print。
判定结果 profile 的结构与判定优先级以 docs\\抓取策略表.md 为唯一契约。

================================ 函数结构树 ================================
① 纯逻辑（只吃参数，不碰网络 / 文件，可离线单测）
    classify(status, content_type, text_chars, signals)
                                 判定入口：8 个 verdict 按契约 §2 的优先级短路
    _dynamic_evidence(signals)   把动态特征翻译成中文证据列表
    _count_text_chars(text)      去标签后的中文字符数（契约 §3 的 text_chars）
    _content_kind(url, headers, text)
                                 Content-Type + URL 后缀 → html / pdf / other / None
    _is_pdf_url(url)             URL 路径是否以 .pdf 结尾
    _has_login_form(html)        HTML 里是否有登录表单 / "请先登录"类提示
    _is_login_url(url)           最终地址是否落在登录页（重定向到 login 也算登录墙）
    _cf_header(headers, html)    是否命中 Cloudflare / 风控特征头
    _page_facts(html, headers, final_url)
                                 HTML → (text_chars, 固定 6 键的 signals)
    _empty_profile(url, time)    完整 profile 骨架（所有键齐全）
    _empty_signals()             固定 6 键的 signals 骨架
    _site_of(url) / _now_iso() / _normalize_target(url)
                                 hostname / ISO8601 本地时间 / 裸域名补 https
    _lower_headers(headers)      响应头键统一小写（net.fetch 本来已小写，假响应未必）
    _sample_text(response, cap)  只取前 sample_bytes 字节做判定

② 网络 + 落盘
    probe(url, ...)              单个 URL：robots 闸门 → 抓一次页面 → 组 profile
    probe_many(urls, ...)        顺序探测多条，带间隔与进度回调
    merge_profiles(old, new)     重探合并：新的覆盖旧的，checked_at 写入历史列表
    save_profiles(profiles, path)  走 io_utils.write_json（dict 或 list 都收）
    load_profiles(path)          以 url 为键读回；文件不存在 / 坏 JSON 返回 {}
③ 自测
    _self_test()                 零网络自检：8 个 verdict 各覆盖一次 + 合并 / 往返

================================ 调用关系总览 ==============================
入口层（main.py）──▶ probe_many ──▶ probe ──▶ web_check.check_robots（robots 闸门）
                                │         │      └─▶ net.fetch   （唯一 HTTP 出口）
                                │         ├─▶ fetch（默认 net.fetch） 取页面一次
                                │         ├─▶ web_check.classify_render（现成渲染判定）
                                │         ├─▶ html_utils.to_text     （现成正文提取）
                                │         └─▶ classify               （纯判定）
                                └─▶ net.sleep_between                （多条之间间隔）
              save_profiles ──▶ io_utils.write_json
              load_profiles ──▶ io_utils.read_json

⚠️ 注入 fetch 时（离线单测 / 自检），为了让 robots 闸门也走同一个假实现，
   只在 check_robots 调用期间临时把 net.fetch 指向它，用完 finally 还原。
   本项目按契约 §7 串行执行，不存在并发写这个全局的窗口。

================================ 数据流单链路 ==============================
URL → check_robots（robots.txt → 解析 → allowed）
    ├─ allowed is False → robots_disallow（不取页面，合规红线）
    └─ 其他 → net.fetch 抓一次页面
              → 响应头 / 状态码 / 正文 → content_type
              → html：classify_render + to_text → text_chars + signals
              → classify → verdict + reason → profile → JSON（route.py 消费）

================================ 对外返回契约 =============================
probe(url, *, timeout=8.0, sample_bytes=200_000, user_agent=None, fetch=None) -> dict
    # 返回契约 §3 的完整 profile（14 个顶层键，signals 固定 6 键）；
    # **永不抛异常**：超时 / DNS / 解析失败 / 任何异常都返回 verdict="error" 的完整 profile。

probe_many(urls, *, progress=None, sleep_s=0.0, **kw) -> list[dict]
    # 顺序探测；progress(index, total, profile)，index 从 1 开始（与 page.crawl_many 一致）。

merge_profiles(old, new) -> dict
    # 新的覆盖旧的；旧 checked_at 写进 checked_at_history（去重、按时间先后追加）。
    # 该键只为"保留历史"存在，route.py 不需要读它。

save_profiles(profiles, path) -> None
    # profiles 可以是 {url: profile}、[profile, ...] 或单条 profile；一律写成以 url 为键的 JSON。
load_profiles(path) -> dict[str, dict]
    # 文件不存在 / 内容损坏 → {}（工具层不因为缓存文件坏了就让入口层崩）。

classify(*, status, content_type, text_chars, signals) -> tuple[str, str]
    # 纯函数，返回 (verdict, reason)。8 个 verdict 按契约 §2 的优先级判：
    # error > robots_disallow > blocked > login_wall > pdf > dynamic > thin_static > static
    # signals 里除契约的 6 个固定键外，额外识别两个**内部键**（由 probe 注入、只用于判定，
    # 绝不写进 profile.signals）：
    #   `robots_allowed` —— 合规事实，区分 robots 禁止 与 探测失败
    #   `login_url`      —— 是否被重定向到登录页；登录表单只是弱证据，
    #                       正文够长时不算登录墙（媒体页普遍内嵌隐藏登录弹窗）
    # 无网络、无文件、无全局状态。

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from Path_Store import Path_Store as ps
    from webcrawl import html_utils, io_utils, net, web_check
except ImportError:                     # 直接运行本文件（人工自检）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import html_utils, io_utils, net, web_check

# ---------------------------------------------------------------- 常量区
DEFAULT_TIMEOUT = 8.0                   # 契约 §5 的默认超时
DEFAULT_SAMPLE_BYTES = 200_000          # 只取前 200 KB 做判定，别把 3 MB 页面全解析一遍
STATIC_TEXT_MIN = 300                   # 契约 §2：正文 < 300 中文字符 = thin_static
BLOCKED_STATUS = frozenset({403, 503})  # 契约 §2：403 / 503 视为拦截
SERVER_ERROR_MIN = 500                  # 5xx 服务端错误 → error（503 已被 blocked 认领）
PROFILE_HISTORY_KEY = "checked_at_history"   # merge_profiles 保留的历史时间戳

# 契约 §3 的固定键：profile 顶层 / robots / signals 三处都不能少键
PROFILE_KEYS = ("url", "site", "checked_at", "http_status", "final_url", "robots",
                "content_type", "encoding", "bytes", "text_chars", "verdict",
                "reason", "signals", "error")
ROBOTS_KEYS = ("found", "allowed", "crawl_delay", "error")
SIGNAL_KEYS = ("spa_mount", "noscript_hint", "login_form", "cookie_script",
               "cf_header", "script_ratio")

# 中文字符（CJK 统一表意文字 + 扩展 A + 兼容表意文字）；契约 §3 要求"中文字符数"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 登录表单 / 登录墙提示
LOGIN_FORM_RE = re.compile(r"<form\b[^>]*>(.*?)</form\s*>", re.S | re.I)
PASSWORD_INPUT_RE = re.compile(r"""<input\b[^>]*\btype\s*=\s*["']?password""", re.I)
LOGIN_WORD_RE = re.compile(r"登录|登陆|sign\s*in|log\s*in", re.I)
LOGIN_WALL_TEXT_RE = re.compile(r"请先登录|登录后(?:查看|继续|阅读)|需要登录|登录才能")
# 最终地址落在这些路径 / 主机上 = 登录墙
LOGIN_PATH_RE = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|sign_in|passport|sso|oauth)(?:[/.?#]|$)", re.I)
LOGIN_HOST_PREFIXES = ("passport.", "login.", "sso.")
# Cloudflare / 风控特征：响应头优先，正文只作辅助证据
CF_HEADER_KEYS = ("cf-mitigated", "cf-chl-bypass", "cf-challenge")
CF_BODY_MARKERS = ("cf-chl", "challenge-platform", "cf_chl_opt",
                   "just a moment", "attention required")


# ================================= ① 纯逻辑（不碰网络 / 文件）
def _dynamic_evidence(signals: dict[str, Any]) -> list[str]:
    """把动态特征翻译成中文证据；没命中就返回空列表（顺序固定，方便日志对照）。

    只认契约 §2 点名的三类证据：setCookie 脚本 / 空骨架（SPA 挂载点）/ noscript 提示。
    `script_ratio` 只记录在 profile 里当辅助证据，**不单独触发 dynamic**：
    正文很薄但脚本多的静态页，判 thin_static 去换编码重试比误上浏览器更划算。
    """
    evidence: list[str] = []
    if signals.get("cookie_script"):
        evidence.append("setCookie / JS 跳转脚本")
    if signals.get("noscript_hint"):
        evidence.append("<noscript> 提示需开启 JS")
    if signals.get("spa_mount"):
        evidence.append(f"SPA 挂载点 {signals['spa_mount']}")
    return evidence


def classify(*, status: int | None, content_type: str | None,
             text_chars: int | None, signals: dict[str, Any]) -> tuple[str, str]:
    """判定一个页面的 verdict，返回 `(verdict, reason)`；纯函数，不联网、不读文件。

    严格按契约 §2 的优先级短路（前面的命中就不再看后面）：
        error > robots_disallow > blocked > login_wall
              > pdf > dynamic > thin_static > static

    参数：
        status:       HTTP 状态码；None = 没有拿到响应（超时 / 连接失败 / 未发请求）
        content_type: "html" | "pdf" | "other" | None
        text_chars:   去标签后的中文字符数；拿不到为 None
        signals:      契约 §3 的 6 个固定键；额外允许内部键 `robots_allowed`
                      （True / False / None，由 probe 注入，不写进 profile）

    注意：error 最高，因为探测失败时其余判断都不可信；robots 明确禁止时
    根本不取页面（status=None 是"按约定没发请求"，不是探测失败，靠
    `robots_allowed is False` 区分）。
    """
    signals = signals or {}
    robots_allowed = signals.get("robots_allowed")

    # ① error —— 探测本身失败
    if status is None:
        if robots_allowed is not False:
            return "error", "探测失败：没有拿到页面响应（超时 / 连接失败）"
    elif status >= SERVER_ERROR_MIN and status != 503:
        return "error", f"探测失败：服务端错误 HTTP {status}"
    elif status not in (200, *BLOCKED_STATUS):
        return "error", f"探测失败：HTTP {status}，页面不可用"

    # ② robots 明确禁止（合规红线，不取页面）
    if robots_allowed is False:
        return "robots_disallow", "robots.txt 明确禁止抓取该路径"

    # ③ blocked —— 状态码或 Cloudflare / 风控特征头
    if status in BLOCKED_STATUS:
        if signals.get("cf_header"):
            return "blocked", f"HTTP {status} 且命中 Cloudflare/风控特征头，被站点拦截"
        return "blocked", f"HTTP {status}，被站点拦截（风控 / 拒绝服务）"
    if signals.get("cf_header"):
        return "blocked", "命中 Cloudflare/风控特征头，页面被拦截"

    # ④ login_wall —— 只在"确实没内容"或"确实被送到登录页"时成立。
    #    真踩过：媒体页普遍内嵌隐藏登录弹窗，只看表单会把正常文章页误判成登录墙而整站跳过。
    if signals.get("login_url"):
        return "login_wall", "重定向后落在登录页，需登录才能看内容"
    if signals.get("login_form") and (text_chars or 0) < STATIC_TEXT_MIN:
        return "login_wall", f"页面含登录表单且正文仅 {text_chars or 0} 字，需登录才能看内容"

    # ⑤ pdf —— 非网页，直接按文件存
    if content_type == "pdf":
        return "pdf", "Content-Type 为 PDF，按文件直接保存，不按网页判定"

    # ⑥ 内容类型兜底：拿不到 HTML 就没法评正文
    if content_type == "other":
        return "error", "探测失败：返回的不是网页（Content-Type 非 HTML / PDF）"
    if content_type is None:
        return "error", "探测失败：响应缺少 Content-Type，无法判断内容类型"
    if text_chars is None:
        return "error", "探测失败：正文解析失败，拿不到可见文字数"

    # ⑦ dynamic —— 正文没到 300 字且命中动态特征；正文够长就不改判
    #    （真踩过：长文章里带 document.cookie 被误判动态，详见 web_check 同类注释）
    evidence = _dynamic_evidence(signals)
    if text_chars < STATIC_TEXT_MIN and evidence:
        return "dynamic", (f"正文仅 {text_chars} 字且命中 " + "、".join(evidence)
                           + "，需 JS 渲染才拿得到正文")

    # ⑧ thin_static / static —— 只看契约 §2 的 300 字分界
    if text_chars < STATIC_TEXT_MIN:
        return "thin_static", (f"正文仅 {text_chars} 字（少于 {STATIC_TEXT_MIN}），"
                               "静态但内容太少，可换编码再试")
    return "static", f"正文 {text_chars} 字且无动态特征，静态 HTML 直出"


def _count_text_chars(text: str) -> int:
    """统计去标签后可见正文里的中文字符数（契约 §3 对 text_chars 的定义）。"""
    return len(CJK_RE.findall(text or ""))


def _is_pdf_url(url: str) -> bool:
    """URL 路径是否以 .pdf 结尾（Content-Type 缺失 / octet-stream 时才用它兜底）。"""
    try:
        path = urlsplit(_normalize_target(url)).path.lower()
    except ValueError:                  # 畸形 URL（如未闭合的 IPv6 方括号）不许抛出去
        return False
    return path.endswith(".pdf")


def _content_kind(url: str, headers: dict[str, str], text: str) -> str | None:
    """由 Content-Type（优先）与 URL 后缀（兜底）判断内容类型。"""
    mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if "pdf" in mime:
        return "pdf"
    if "html" in mime or "xhtml" in mime:
        return "html"
    if _is_pdf_url(url) and (not mime or "octet-stream" in mime):
        return "pdf"
    if not mime:
        # 没有响应头时，靠正文首字符兜底：HTML 一定以标签开头
        return "html" if (text or "").lstrip().startswith("<") else None
    return "other"


def _has_login_form(html: str) -> bool:
    """页面是否含登录表单（密码框 / 带登录字样的输入表单），或"请先登录"提示。

    只认**表单级**证据，不认页头那个"登录"链接：导航里几乎每个站都有登录入口，
    认链接会把所有文章页误判成登录墙。
    """
    html = html or ""
    for block in LOGIN_FORM_RE.findall(html):
        if PASSWORD_INPUT_RE.search(block):
            return True
        if "<input" in block.lower() and LOGIN_WORD_RE.search(block):
            return True
    return bool(LOGIN_WALL_TEXT_RE.search(html))


def _is_login_url(url: str | None) -> bool:
    """最终地址是否落在登录页（重定向到 /login、passport 子域等）。"""
    if not url:
        return False
    try:
        parts = urlsplit(_normalize_target(url))
        host = (parts.hostname or "").lower()
        path = parts.path or ""
    except ValueError:                  # 畸形 URL 不参与登录墙判定，也不许抛异常
        return False
    if host.startswith(LOGIN_HOST_PREFIXES):
        return True
    return bool(LOGIN_PATH_RE.search(path))


def _cf_header(headers: dict[str, str], html: str) -> bool:
    """是否命中 Cloudflare / 风控特征。

    判定材料分两档：明确的 `cf-mitigated` 头直接算；其余要求"正文有挑战页标记"
    且"响应头确实来自 Cloudflare"，避免把正常用 CF 的站一概判成被拦截。
    注意：net.fetch 走 HTTPError 分支时拿不到错误响应的头，那种情况靠 403/503
    状态码兜底（classify 里先判状态码）。
    """
    if any(headers.get(key) for key in CF_HEADER_KEYS):
        return True
    body = (html or "").lower()
    if not any(marker in body for marker in CF_BODY_MARKERS):
        return False
    server = str(headers.get("server") or "").lower()
    return bool("cf-ray" in headers or "cf-cache-status" in headers
                or "cloudflare" in server)


def _page_facts(html: str, headers: dict[str, str],
                final_url: str | None) -> tuple[int, dict[str, Any]]:
    """HTML → (中文字符数, 固定 6 键的 signals)。渲染特征复用 web_check 的现成判定。"""
    render = web_check.classify_render(html)
    render_signals = render.get("signals") or {}
    text = html_utils.to_text(html)
    signals: dict[str, Any] = {
        "spa_mount": render_signals.get("spa_mount"),
        "noscript_hint": bool(render_signals.get("noscript_hint")),
        # 页面含登录表单 —— 是"登录墙"的**弱**证据：正文够长时不算登录墙（见 classify ④）
        "login_form": _has_login_form(html),
        # 复用 web_check 的 js_redirect 证据（document.cookie / location 跳转等）
        "cookie_script": bool(render_signals.get("js_redirect")),
        "cf_header": _cf_header(headers, html),
        "script_ratio": render.get("script_ratio"),
    }
    return _count_text_chars(text), signals


def _empty_signals() -> dict[str, Any]:
    """固定 6 键的 signals 骨架（契约 §3：没命中也要给 None / False）。"""
    return {"spa_mount": None, "noscript_hint": False, "login_form": False,
            "cookie_script": False, "cf_header": False, "script_ratio": None}


def _empty_profile(url: str, checked_at: str) -> dict[str, Any]:
    """完整 profile 骨架：所有键齐全，失败时直接改 verdict / reason 就能返回。"""
    return {
        "url": str(url or ""),
        "site": _site_of(url),
        "checked_at": checked_at,
        "http_status": None,
        "final_url": None,
        "robots": {"found": False, "allowed": None, "crawl_delay": None, "error": None},
        "content_type": None,
        "encoding": None,
        "bytes": None,
        "text_chars": None,
        "verdict": "error",
        "reason": "尚未探测",
        "signals": _empty_signals(),
        "error": None,
    }


def _site_of(url: str | None) -> str:
    """取主机名（小写、不含端口），如 `tjj.guiyang.gov.cn`；畸形 URL 返回空串。"""
    try:
        parts = urlsplit(_normalize_target(url))
        return (parts.hostname or "").lower()
    except ValueError:                  # 本函数在 probe 的 try 之外调用，绝不能抛
        return ""


def _now_iso() -> str:
    """本地时间的 ISO8601 字符串（带时区偏移，便于不同机器对照）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_target(url: str | None) -> str:
    """裸域名补 https://（与 net.fetch / web_check 的容忍度一致）。"""
    target = str(url or "").strip()
    return target if "://" in target else "https://" + target


def _lower_headers(headers: Any) -> dict[str, str]:
    """响应头键统一小写、值转字符串；假响应不是 dict 时按空处理。"""
    if not isinstance(headers, dict):
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _sample_text(response: dict[str, Any], sample_bytes: int) -> str:
    """只取前 sample_bytes 字节做判定（超长页面没必要整篇解析）。

    优先按原始字节截断后按实际编码解码；拿不到原始字节时退回字符截断。
    """
    text = str(response.get("text") or "")
    if not sample_bytes or sample_bytes <= 0:
        return text
    raw = response.get("raw")
    if isinstance(raw, (bytes, bytearray)) and len(raw) > sample_bytes:
        encoding = str(response.get("encoding") or "utf-8").replace("(replace)", "")
        try:
            return bytes(raw[:sample_bytes]).decode(encoding, errors="ignore")
        except (LookupError, TypeError):
            return bytes(raw[:sample_bytes]).decode("utf-8", errors="ignore")
    if len(text.encode("utf-8", "ignore")) <= sample_bytes:
        return text
    return text[:sample_bytes]


# ================================= ② 网络 + 落盘
def _robots_gate(url: str, timeout: float, user_agent: str | None,
                 fetch: Any = None) -> dict[str, Any]:
    """调现成的 check_robots 取合规判定；**不**在这里解析 robots.txt。

    注入假 fetch 时临时替换 net.fetch，让 robots 请求也走同一个假实现
    （否则离线单测会真的去抓 robots.txt）。用完 finally 还原，串行执行无并发窗口。
    """
    kwargs: dict[str, Any] = {"path": web_check.judge_path_of(url), "timeout": timeout,
                              "probe_render": False}
    if user_agent:
        kwargs["user_agent"] = user_agent
    if fetch is None:
        return web_check.check_robots(url, **kwargs)
    original = net.fetch
    net.fetch = fetch
    try:
        return web_check.check_robots(url, **kwargs)
    finally:
        net.fetch = original


def probe(url: str, *, timeout: float = DEFAULT_TIMEOUT,
          sample_bytes: int = DEFAULT_SAMPLE_BYTES, user_agent: str | None = None,
          fetch: Any = None) -> dict[str, Any]:
    """探测单个 URL，返回契约 §3 的完整 profile。**任何失败都不抛异常**。

    流程（顺序与合规相关，不要随意调）：
        ① check_robots 判 robots；`allowed is False` → robots_disallow，**不取页面**
        ② allowed 为 True / None 时抓一次页面（robots 没抓到也照常探测正文，
           与 web_check.detect_render_mode 的语义一致；页面请求只发这一次）
        ③ HTML → classify_render + to_text → text_chars / signals → classify
        ④ 组 profile 返回

    fetch 参数用于离线单测替换网络实现，要求与 net.fetch 同签名
    （`fetch(url, timeout=..., user_agent=...)` 或 `fetch(url, **kwargs)`）。
    """
    profile = _empty_profile(url, _now_iso())
    try:
        gate = _robots_gate(url, timeout, user_agent, fetch)
        rules = gate.get("rules") or {}
        profile["robots"] = {
            "found": bool(gate.get("found")),
            "allowed": gate.get("allowed"),
            "crawl_delay": rules.get("crawl_delay"),
            "error": gate.get("error"),
        }

        # ① robots 明确禁止：连页面请求都不发（合规红线）
        if gate.get("allowed") is False:
            verdict, reason = classify(
                status=None, content_type=None, text_chars=None,
                signals={**_empty_signals(), "robots_allowed": False})
            profile.update({"verdict": verdict, "reason": reason})
            return profile

        # ② 抓页面（robots 为 True / None 都探测正文；None 的合规不确定性在 robots 字段里）
        target = _normalize_target(url)
        fetcher = fetch or net.fetch
        ua_kwargs: dict[str, Any] = {"user_agent": user_agent} if user_agent else {}
        response = fetcher(target, timeout=timeout, **ua_kwargs)
        if not isinstance(response, dict):
            raise TypeError(f"fetch 返回值不是 dict：{type(response).__name__}")

        status = response.get("status")
        headers = _lower_headers(response.get("headers"))
        final_url = response.get("final_url") or target
        text_all = str(response.get("text") or "")

        # ③ 按内容类型分流：PDF / 非网页不解析正文，HTML 才判静态 / 动态
        kind = _content_kind(target, headers, text_all)
        text_chars: int | None = None
        signals = _empty_signals()
        if kind == "html":
            sample = _sample_text(response, sample_bytes)
            text_chars, signals = _page_facts(sample, headers, final_url)
        else:
            signals["cf_header"] = _cf_header(headers, "")

        raw = response.get("raw")
        if isinstance(raw, (bytes, bytearray)):
            size: int = len(raw)
        else:
            size = len(text_all.encode("utf-8", "replace"))
        profile.update({
            "http_status": status,
            "final_url": str(final_url),
            "content_type": kind,
            "encoding": response.get("encoding"),
            "bytes": size,
            "text_chars": text_chars,
            "signals": signals,
        })

        verdict, reason = classify(
            status=status, content_type=kind, text_chars=text_chars,
            signals={**signals,
                     "robots_allowed": profile["robots"]["allowed"],
                     "login_url": _is_login_url(final_url)})
        profile.update({"verdict": verdict, "reason": reason})

        # error 才写 error 字段（blocked 也是抓取失败，但契约要求 error 专指 verdict=error）
        if verdict == "error":
            fetch_error = response.get("error")
            if fetch_error:
                reason = f"{reason}（{fetch_error}）"
                profile["reason"] = reason
                profile["error"] = str(fetch_error)
            elif status not in (None, 200):
                profile["error"] = f"HTTP {status}"
            else:
                profile["error"] = reason
    except Exception as exc:                    # 单条探测失败不拖垮整批，也不向上抛
        detail = f"{type(exc).__name__}: {exc}"
        profile.update({"verdict": "error", "reason": f"探测异常：{detail}",
                        "error": detail})
    return profile


def probe_many(urls: list[str], *, progress: Any = None, sleep_s: float = 0.0,
               **kw: Any) -> list[dict[str, Any]]:
    """顺序探测多条；每条之间按 sleep_s 等待，进度用 `progress(index, total, profile)`。

    本函数**不打印**（工具层零 print）；单条失败不中断整批（probe 本身不抛异常）。
    crawl-delay 只记录在 profile.robots 里，由采集层按自己的节奏执行，本层不自动睡。
    """
    items = list(urls or [])
    total = len(items)
    results: list[dict[str, Any]] = []
    for index, url in enumerate(items, 1):
        profile = probe(url, **kw)
        results.append(profile)
        if progress is not None:
            progress(index, total, profile)
        if index < total:
            net.sleep_between(sleep_s)
    return results


def merge_profiles(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """同一 URL 重探时合并：新的覆盖旧的，但把 checked_at 历史留下来。

    - 顶层键：new 覆盖 old；old 独有的键保留。
    - `robots` / `signals`：逐键合并（new 的键覆盖 old，缺失的键保留）。
    - `checked_at_history`：按时间先后追加旧 / 新的 checked_at，重复不追加。
    """
    old = dict(old or {})
    new = dict(new or {})
    merged = dict(old)
    merged.update(new)
    for key in ("robots", "signals"):
        before, after = old.get(key), new.get(key)
        if isinstance(before, dict) and isinstance(after, dict):
            merged[key] = {**before, **after}

    history = old.get(PROFILE_HISTORY_KEY)
    history = list(history) if isinstance(history, list) else []
    for stamp in (old.get("checked_at"), new.get("checked_at")):
        if isinstance(stamp, str) and stamp and stamp not in history:
            history.append(stamp)
    if history:
        merged[PROFILE_HISTORY_KEY] = history
    return merged


def save_profiles(profiles: Any, path: str | Path) -> None:
    """把 profile 集合写成以 url 为键的 JSON（走 io_utils.write_json）。

    profiles 可以是 {url: profile}、[profile, ...]，或直接一条 profile。
    """
    payload: dict[str, Any] = {}
    if isinstance(profiles, dict):
        if "url" in profiles and "verdict" in profiles:     # 单条 profile
            payload[str(profiles["url"])] = profiles
        else:
            payload = {str(key): value for key, value in profiles.items()}
    else:
        for profile in profiles or []:
            if isinstance(profile, dict) and profile.get("url"):
                payload[str(profile["url"])] = profile
    io_utils.write_json(path, payload)


def load_profiles(path: str | Path) -> dict[str, dict[str, Any]]:
    """读回 profile 集合（以 url 为键）；文件不存在 / 坏 JSON 一律返回 {}。"""
    try:
        target = Path(path)
        if not target.is_file():
            return {}
        data = io_utils.read_json(target)
    except (OSError, ValueError, TypeError):
        return {}

    if isinstance(data, dict):
        if "url" in data and "verdict" in data:             # 单条 profile
            return {str(data["url"]): data}
        return {str(key): value for key, value in data.items()
                if isinstance(value, dict)}
    if isinstance(data, list):
        result: dict[str, dict[str, Any]] = {}
        for item in data:
            if isinstance(item, dict) and item.get("url"):
                result[str(item["url"])] = item
        return result
    return {}


# ================================= ③ 自测（零网络，不碰真实数据）
def _self_test() -> None:
    """用构造的 HTML + 假参数把 8 个 verdict 各跑一次，再验合并 / 存取 / 合规红线。

    ⚠️ 本函数是人工自检入口，是**唯一**允许向终端输出的地方；工具代码本身零 print，
       这里用 sys.stdout.write 输出，避免与"零 print"约束冲突。
    """
    write = sys.stdout.write

    def judge(name: str, **kwargs: Any) -> str:
        verdict, reason = classify(**kwargs)
        write(f"  {name:<8} → {verdict:<15} {reason}\n")
        return verdict

    # —— 8 种 verdict 的构造参数（HTML 一律走现有的 classify_render / to_text）
    long_html = ("<html><body><article>" + "凯里酸汤鱼产业调查正文。" * 40
                 + "</article></body></html>")
    static_chars, static_signals = _page_facts(
        long_html, {}, "https://e.example/a.html")

    short_html = "<html><body><p>首页 新闻 联系我们</p></body></html>"
    short_chars, short_signals = _page_facts(
        short_html, {}, "https://e.example/a.html")

    spa_html = ('<html><body><div id="app"></div>'
                '<noscript>请开启 JavaScript 后访问</noscript>'
                '<script>document.cookie="s=1";</script></body></html>')
    spa_chars, spa_signals = _page_facts(spa_html, {}, "https://e.example/a.html")

    login_html = ('<html><body><form action="/login"><input name="u">'
                  '<input type="password" name="p"></form></body></html>')
    login_chars, login_signals = _page_facts(
        login_html, {}, "https://e.example/a.html")

    results = {
        "static": judge("静态长文", status=200, content_type="html",
                        text_chars=static_chars, signals=static_signals),
        "thin_static": judge("静态短文", status=200, content_type="html",
                             text_chars=short_chars, signals=short_signals),
        "dynamic": judge("SPA 空壳", status=200, content_type="html",
                         text_chars=spa_chars, signals=spa_signals),
        "login_wall": judge("登录表单", status=200, content_type="html",
                            text_chars=login_chars, signals=login_signals),
        "blocked": judge("403 拦截", status=403, content_type=None,
                         text_chars=None, signals=_empty_signals()),
        "pdf": judge("PDF 文件", status=200, content_type="pdf",
                     text_chars=None, signals=_empty_signals()),
        "robots_disallow": judge(
            "robots 禁止", status=None, content_type=None, text_chars=None,
            signals={**_empty_signals(), "robots_allowed": False}),
        "error": judge("请求失败", status=None, content_type=None,
                       text_chars=None, signals=_empty_signals()),
    }
    expected = {"static", "thin_static", "dynamic", "login_wall", "blocked",
                "pdf", "robots_disallow", "error"}
    assert set(results.values()) == expected, results

    # 回归：长文章里内嵌隐藏登录弹窗（有表单、但正文够长）**不许**判成登录墙。
    # 真踩过：媒体页普遍带登录浮层，只看表单会把整站文章误判成需要登录。
    embedded = judge("长文+登录弹窗", status=200, content_type="html",
                     text_chars=static_chars,
                     signals={**static_signals, "login_form": True})
    assert embedded == "static", embedded
    # 回归：被重定向到登录页时，正文再长也判登录墙
    redirected = judge("被送去登录页", status=200, content_type="html",
                       text_chars=static_chars,
                       signals={**static_signals, "login_url": True})
    assert redirected == "login_wall", redirected
    assert set(static_signals) == set(SIGNAL_KEYS)
    assert static_chars >= STATIC_TEXT_MIN
    assert short_chars < STATIC_TEXT_MIN
    assert spa_signals["spa_mount"] and spa_signals["noscript_hint"]
    assert spa_signals["cookie_script"]
    assert login_signals["login_form"]

    # 状态码正常时，Cloudflare 风控头也必须能单独触发 blocked
    cf_verdict, _ = classify(status=200, content_type="html", text_chars=static_chars,
                             signals={**static_signals, "cf_header": True})
    assert cf_verdict == "blocked"

    # —— 合并：新值覆盖旧值，checked_at 历史保留且重复合并不重复追加
    old = _empty_profile("https://e.example/a.html", "2026-10-19T10:00:00+08:00")
    new = _empty_profile("https://e.example/a.html", "2026-10-20T10:00:00+08:00")
    new.update({"verdict": "static", "reason": "正文 400 字，静态直出"})
    merged = merge_profiles(merge_profiles(old, new), new)
    assert merged["verdict"] == "static"
    assert merged["checked_at"] == new["checked_at"]
    assert merged[PROFILE_HISTORY_KEY] == [old["checked_at"], new["checked_at"]]
    write(f"  merge    → checked_at_history={merged[PROFILE_HISTORY_KEY]}\n")

    # —— 落盘 / 读取往返：只在临时目录里发生，零网络
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "probe.json")
        save_profiles([old, new], path)
        loaded = load_profiles(path)
        assert set(loaded) == {old["url"], new["url"]}
        assert load_profiles(str(Path(tmp) / "missing.json")) == {}
    write("  save/load → 往返一致；缺失文件返回 {}\n")

    # —— 整条 probe 链路（假 robots + 假 fetch，零网络）：robots 禁止时不取页面
    def fake_response(target: str, text: str) -> dict[str, Any]:
        return {"url": target, "final_url": target, "status": 200, "found": True,
                "text": text, "raw": text.encode("utf-8"), "encoding": "utf-8",
                "headers": {"content-type": "text/html; charset=utf-8"},
                "error": None, "transient": False}

    calls: list[str] = []

    def fake_disallow(target: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(target)
        return fake_response(target, "User-agent: *\nDisallow: /\n")

    denied = probe("https://e.example/a.html", fetch=fake_disallow)
    assert denied["verdict"] == "robots_disallow", denied
    assert calls == ["https://e.example/robots.txt"], calls   # 页面请求根本没发
    write("  probe    → robots 禁止时只请求了 robots.txt\n")

    def fake_allow(target: str, **kwargs: Any) -> dict[str, Any]:
        if target.endswith("/robots.txt"):
            return fake_response(target, "User-agent: *\nDisallow: /private\n")
        return fake_response(target, long_html)

    ok = probe("https://e.example/a.html", fetch=fake_allow)
    assert ok["verdict"] == "static", ok
    assert set(ok) == set(PROFILE_KEYS)
    assert set(ok["signals"]) == set(SIGNAL_KEYS)
    assert set(ok["robots"]) == set(ROBOTS_KEYS)
    write(f"  probe    → verdict={ok['verdict']} text_chars={ok['text_chars']} "
          f"signals={sorted(ok['signals'])}\n")

    # —— 异常兜底：fetch 直接抛异常也必须返回完整 error profile
    def fake_boom(target: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("offline")

    broken = probe("https://e.example/x", fetch=fake_boom)
    assert broken["verdict"] == "error" and set(broken) == set(PROFILE_KEYS)
    write(f"  probe    → 异常兜底 {broken['verdict']}：{broken['error']}\n")

    write("[OK] 自检通过：8 个 verdict 全覆盖 / signals 键固定 / merge 保留历史 / "
          "save-load 往返 / robots 禁止不发页面请求（零网络）\n")


if __name__ == "__main__":
    _self_test()
