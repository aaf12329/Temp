"""功能层（依赖共用层，不横向导入功能模块）。
结构导航：
├─ Policies
│  ├─ __init__
│  ├─ get
│  ├─ for_url
调用关系：
├─ for_url → self.get。
数据流：人工策略 JSON → SitePolicy → URL 访问判定。
"""

from urllib.parse import urlsplit

from app.common.file_io import read_json
from app.common.storage import Storage
from app.contracts.crawler import SitePolicy
from app.core.errors import ServiceError


class Policies:
    def __init__(self, storage: Storage):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        self.path = storage.paths.path("policy_file")

    def get(self, host: str) -> SitePolicy:
        """按主键读取所属功能的固定返回契约；由对应业务入口调用。"""
        host = host.lower()
        if any(c in host for c in "/\\@?#") or not host:
            raise ServiceError("E_POLICY_HOST", "host 必须是域名或主机名。", 422)
        try:
            policies = [SitePolicy.model_validate(x) for x in read_json(self.path)]
        except (OSError, ValueError) as exc:
            raise ServiceError("E_POLICY_INVALID", "站点策略文件无法读取或格式错误。", 503) from exc
        for policy in policies:
            if policy.host.lower() == host:
                return policy
        raise ServiceError("E_POLICY_NOT_FOUND", f"尚未人工声明 {host} 的站点策略。", 404)

    def for_url(self, url: str) -> SitePolicy:
        """检查人工来源策略、登录要求和允许路径；由采集入口调用。"""
        parts = urlsplit(url)
        policy = self.get(parts.hostname or "")
        if policy.requires_login:
            raise ServiceError("E_POLICY_LOGIN", "站点声明需要登录，禁止采集。", 403)
        path = parts.path or "/"
        if any(path.startswith(x) for x in policy.disallowed_paths) or not any(
            path.startswith(x) for x in policy.allowed_paths
        ):
            raise ServiceError("E_POLICY_PATH", "该路径未在人工声明的允许范围内。", 403)
        return policy
