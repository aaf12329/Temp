"""有界研究消息状态机，不执行网络或数据库操作。

结构：ResearchProgress.phase/crawl → _publish → 调用方注入的持久化回调。
调用：网页管线聚合入口 → 本状态机；数据流：真实阶段/网址结果 → 有界JSON进度快照。
回调返回False表示执行令牌失效，立即停止旧执行者继续处理。
"""

from datetime import datetime, timezone


class ResearchProgress:
    """将真实采集事件和阶段说明组织成页面消息，最多保留400条。"""

    def __init__(self, persist, total: int = 0):
        """保存持久化回调和本轮总网址数；由管线入口构造。"""
        self.persist = persist
        self.state = {"stage": "starting", "message": "任务开始", "total": total,
                      "completed": 0, "succeeded": 0, "failed": 0, "events": []}
        self.sequence = 0

    def _publish(self, event):
        """追加真实事件并保存独立快照；由phase/crawl调用。"""
        self.sequence += 1
        event = {**event, "id": self.sequence,
                 "created_at": datetime.now(timezone.utc).isoformat()}
        self.state["events"] = [*self.state["events"], event][-400:]
        snapshot = {**self.state, "events": list(self.state["events"])}
        if not self.persist(snapshot):
            raise RuntimeError("任务执行权已变更，停止旧任务的后续处理。")

    def phase(self, stage: str, message: str):
        """更新真实阶段并保留消息；由网页管线显式步骤调用。"""
        self.state.update(stage=stage, message=message)
        self._publish({"kind": "phase", "message": message})

    def crawl(self, event):
        """记录collect回调中的开始/结果及准确原因；由采集入口调用。"""
        index, total = event["index"], event["total"]
        status = event["status"]
        reason = str(event.get("reason") or "")[:600]
        if status == "fetching":
            text = "正在检查并爬取网页"
        elif status == "ok":
            text = "爬取成功"
        elif "robots" in reason.lower():
            text = "爬取失败：Robots 协议不允许爬取"
        elif "非公网地址" in reason:
            text = "爬取失败：域名解析为非公网地址，请检查代理 DNS"
        elif event.get("http_status") in {401, 403}:
            text = "爬取失败：网页拒绝访问或需要登录"
        else:
            text = "爬取失败：" + (reason or "未取得可用正文")
        message = f"({index}/{total}) {text}"
        self.state.update(stage="collecting", message=message, total=total)
        if status != "fetching":
            self.state["completed"] += 1
            self.state["succeeded" if status == "ok" else "failed"] += 1
        self._publish({"kind": "crawl", "index": index, "total": total,
                       "status": status, "url": event["url"],
                       "message": message, "reason": reason})
