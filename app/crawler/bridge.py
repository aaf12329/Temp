"""旧爬虫/研究 API 的显式聚合入口，在隔离子进程运行。

结构：bootstrap → crawl/probe/analyze 分支 → main；调用：run_component → main → 组件 API。
数据流：服务校验后的载荷 → 受限网络/离线研究 → 隔离产物与 JSON 响应。
源码 import 与纯路径初始化只发生在子进程，不污染 FastAPI 和 Worker 的模块命名空间。
"""

import json
import sys
from pathlib import Path

from app.common.file_io import write_json
from app.common.network import GuardedTransport, guarded_dns
from app.common.paths import CRAWLER_SOURCE, WGAN_SOURCE


def execute(message: dict) -> dict:
    """装配组件运行边界；由 main 或离线桥接测试调用。"""
    sys.path.insert(0, str(CRAWLER_SOURCE))
    if message["action"] in {"cli-analyze", "cli-finalize", "cli-model"}:
        sys.path.insert(0, str(WGAN_SOURCE))
        from app.pipeline.workflow import analyze, finalize, model

        action = {"cli-analyze": analyze, "cli-finalize": finalize, "cli-model": model}[message["action"]]
        try:
            return {"data": action(message), "error": None}
        except (ValueError, ImportError) as exc:
            # 本分支只运行本地模型，载荷没有 API 凭据。
            return {"data": None, "error": {"code": "E_CLI_DATA",
                    "message": str(exc), "retryable": False}}
    if message["action"] == "model-setup":
        from research.assets import prepare_model

        return {"data": prepare_model(), "error": None}
    if message["action"] == "analyze":
        import research_api

        response = research_api.analyze(message["request"])
        if response["error"]:
            return {"error": {"code": "E_ANALYSIS", "message": "研究失败，请核对模型缓存和输入材料。",
                              "retryable": False}}
        response["data"]["paths"] = {name: "data/" + Path(path).relative_to(
                                      Path(message["data_root"])).as_posix()
                                      for name, path in response["data"]["paths"].items()}
        return {"data": response["data"], "error": None}

    from webcrawl import api, net

    transport = GuardedTransport(message["policies"], Path(message["data_root"]),
                                 delay=message.get("delay", 0), decoder=net._decode_body)
    net.fetch = transport.fetch
    # 子进程内统一出站出口；TLS/浏览器档未适配闸门，因此服务暂不开放。
    api.route.FALLBACK_OF = {key: "skip" for key in api.route.FALLBACK_OF}
    with guarded_dns():
        if message["action"] == "probe":
            response = api.probe(message["request"])
        else:
            request = {**message["request"], "save_job": False, "max_workers": 1,
                       "discover": False, "dedupe": False, "incremental": False,
                       "proxies": {}, "allow_render": False, "allow_tls_impersonate": False}
            response = api.crawl(request)
    if transport.last_error and message["action"] == "crawl" and response.get("results"):
        result = response["results"][0]
        result.update(error=transport.last_error, status="failed", verdict="error")
    return {"data": response, "error": response.get("error")}


def main() -> None:
    """读取标准输入并保存固定形状响应；仅由组件子进程入口调用。"""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    message = json.load(sys.stdin)
    try:
        result = execute(message)
    except Exception:
        result = {"data": None, "error": {"code": "E_COMPONENT_FAILED",
                  "message": "组件异常，请运行离线检查并核对依赖与数据格式。", "retryable": False}}
    write_json(Path(message["result_file"]), result)


if __name__ == "__main__":
    main()
