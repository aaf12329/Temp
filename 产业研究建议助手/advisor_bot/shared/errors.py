"""独立模块错误契约，不依赖 FastAPI。

结构/调用：功能层抛出 BotError → CLI/未来调用者读取 code/message/retryable。
数据流：错误码 + 可操作提示 → 固定 error 字典；不包含密钥或原始响应体。

函数结构树（AST 核对）：
├─ BotError
│  ├─ __init__
│  ├─ as_dict
项目内调用关系：
└─ 无项目内函数调用；入口由 Python/pytest 调度
"""


class BotError(Exception):
    """独立错误封装；由功能层构造，调用者决定显示或处理方式。"""

    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        """保存错误字段；status 仅为未来 HTTP 适配提供粗分类。"""
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable

    def as_dict(self) -> dict:
        """返回稳定错误体；由 CLI 或未来适配器调用。"""
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}
