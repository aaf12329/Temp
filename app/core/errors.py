"""共用契约/基础设施层。
结构导航：
├─ ErrorInfo
│  └─ 契约字段
├─ ErrorEnvelope
│  └─ 契约字段
├─ ServiceError
│  ├─ __init__
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：错误码/提示/重试标记 → 固定错误模型或异常。
"""

from pydantic import BaseModel


class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorEnvelope(BaseModel):
    error: ErrorInfo


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        self.error = ErrorInfo(code=code, message=message, retryable=retryable)
        self.status = status
        super().__init__(message)
