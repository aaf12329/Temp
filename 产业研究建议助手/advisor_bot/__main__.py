"""python -m advisor_bot 的独立入口。

结构/调用：模块入口 → cli.main；数据流：命令参数 → CLI 结果/退出码。
导入时不运行对话，也不访问文件或网络。

函数结构树（AST 核对）：
└─ 包标识，无执行函数
项目内调用关系：
└─ 无项目内函数调用；入口由 Python/pytest 调度
"""

from advisor_bot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
