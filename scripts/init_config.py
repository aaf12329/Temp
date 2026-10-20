"""配置初始化入口，导入时不创建文件。

结构/调用：__main__ → main → 共用文本 I/O；数据流：配置模板 → 随机密钥 → 新 .env。
约束：已有配置不覆盖；位置只从路径地址簿取得。
"""

import secrets

from filelock import FileLock

from app.common.file_io import read_text, write_text
from app.common.paths import ENV_FILE, ENV_LOCK, ENV_TEMPLATE


def main() -> None:
    """创建缺失配置并保留已有配置；由 Setup 脚本或直接运行调用。"""
    with FileLock(str(ENV_LOCK)):
        if ENV_FILE.exists():
            print("Keeping existing .env.")
            return
        template = read_text(ENV_TEMPLATE).replace(
            "replace-with-a-random-token-at-least-32-characters", secrets.token_urlsafe(36)
        )
        write_text(ENV_FILE, template)
        print("Created local .env with a random API token; existing config is never overwritten.")


if __name__ == "__main__":
    main()
