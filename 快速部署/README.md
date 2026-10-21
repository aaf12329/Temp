# 快速部署与一键启动

在 Windows 双击 `启动脚本/01_平台/一键启动.bat`。首次自动配置环境并启动 **FastAPI + 数据库队列 Worker + Next.js前端**；以后依赖未变时复用环境。npm识别并安装锁定依赖，当前标准前端位于frontend。网页“实际研究”已接本机服务；“设计预览”保留独立演示。其他按钮按用途集中在 [启动脚本](../启动脚本/README.md)，根目录旧按钮已移动。

现在首先展示本机 CPU、内存、显卡、NVIDIA 显存和驱动，然后让用户选择 **CPU / NVIDIA GPU / 按硬件自动选择**。高级选项提供 TensorFlow CPU、保留现有框架、暂不安装 GAN。成功读取驱动只代表候选 GPU 可用，实际安装 CUDA 版后还会执行一次小型 CUDA 运算；失败就停止并给出检查驱动或改选 CPU 的提示。Intel/AMD 显卡没有接入对应加速后端，不放进 GPU 菜单。

选择会写入本机 config.local.json；下次回车沿用，改变编号才切换。CPU/GPU 是 WGAN 的 PyTorch 安装版本；当前检索和 XGBoost 仍按原 CPU 配置，最终助手继续调用 API，不改原 JSON 的 5000 轮训练参数。选择暂不安装只跳过安装，不禁用或卸载已经存在的后端。

当前 GPU 依赖是 PyTorch 2.9.1+cu128（CUDA 12.8 版本）。安装对应运行依赖，**不安装显卡驱动或完整系统 CUDA Toolkit**；驱动需要用户系统预先具备。CPU 选项安装独立的 CPU 版 PyTorch，两者在同一环境中按选择切换。

需要先安装 Python 3.12（建议包含 Windows Python Launcher）；没有 Launcher 时可指定 Python 路径。当前独立前端使用 Node.js 24 或更高版本（含 npm）。脚本不自动安装系统级 Python、Node、Conda、GPU 驱动、Docker，也不会绕过网络失败。

## 默认流程

```text
启动脚本/01_平台/一键启动.bat
└─ 快速部署/Start.ps1
   ├─ Hardware.ps1：展示配置，接受运行版本选择
   ├─ 读取 config.example.json + config.local.json
   ├─ Resolve-Python：创建/复用根 .venv（Python 3.12）
   ├─ Install-Python → scripts/Setup.ps1
   │  ├─ requirement.txt → 锁定依赖 + 主服务 + 独立助手
   │  ├─ 可选 GAN 后端（auto 保留已有 CPU/CUDA/TF；全新环境默认 PyTorch CPU）
   │  └─ 保留已有 .env，缺失时生成；执行数据库迁移
   ├─ 可选准备固定版本本地向量模型（首次可能下载）
   ├─ Install-Frontend：找到 package.json 才安装 npm 依赖
   └─ python -m app.local_service
      ├─ Uvicorn：127.0.0.1:8000
      ├─ Worker：共用所选 Python、根 .env 与数据库
      └─ Next.js：127.0.0.1:3000，同源转发API，密钥不进入浏览器
```

输入 JSON 是一个对象；依赖输入是根 requirement.txt/锁文件、Python 项目描述和可选 package-lock.json。输出为根 `.venv`（或明确指定的环境）、前端目录的 node_modules、`work/quick-deploy` 的依赖哈希及运行日志。哈希只控制是否重装，不保存解释器绝对地址；移动目录、切换解释器、修改依赖或安装脚本会重新检查安装。

启动后打开 `http://127.0.0.1:3000/`；API文档仍在 `http://127.0.0.1:8000/docs`。入口等待API及网页就绪并监管三个进程；按 **Ctrl+C** 回收自己创建的进程。日志在 `work/quick-deploy/api.log`、`worker.log`、`frontend.log`，每次启动覆盖。已有3000/8000服务时先正常关闭；入口不会接管或结束其他服务。

已有依赖时可直接运行 `python -m app.local_service --with-frontend`；自定义端口用 `--port 8001 --frontend-port 3001`。不加with-frontend仍是后端入口，便于单独调试。自动总装针对本仓库标准frontend目录；其他frontend_dir仅用于依赖识别，不能据此宣称任意项目可集成。

首次安装失败会停止并显示原因，下一次可重试，不把失败写成安装成功。依赖未变时仍检查 pip 依赖完整性、初始化配置并执行迁移。需要重新修复安装时加 `-Reinstall`。

## 本机配置

第一次运行自动复制 `config.example.json` 为 `config.local.json`。修改 **config.local.json**；它不进入 Git，可以填写本机解释器路径。也可以先手动复制模板再修改。

| 字段 | 默认值与含义 |
| --- | --- |
| environment_mode | `venv`：创建/复用根 .venv；`existing`：直接使用已有环境 |
| python_path | 默认为空；venv 模式下是创建环境用的 Python 3.12；existing 模式下是必须填写的最终解释器 |
| gan_backend | `auto` 保留已有框架，新环境安装 PyTorch CPU；可明确选择 `pytorch` / `pytorch-cuda` / `tensorflow` / `none` |
| choose_backend | `true`：普通一键启动时显示菜单，回车沿用配置；false 则直接按 gan_backend 部署 |
| frontend_dir | `auto` 顺序查找根目录、frontend、web、apps/web；也可填写项目内相对目录 |
| install_npm | `true`；有 lock 使用 npm ci，无 lock 使用 npm install；重复启动复用 node_modules |
| prepare_vector_model | `true`；准备/核对本地向量缓存。只启动关键词服务可设 false |
| port | `8000`；只监听本机 |

`gan_backend=none` 只跳过安装，不卸载已有框架。GPU 版与 CPU 版 PyTorch 使用同一个环境，不能并列安装；明确切换后端才替换版本。原 WGAN 训练 JSON 不因此改变。

npm 负责安装依赖，标准 frontend 会随 API/Worker 一同启动；独立设计预览见 ../frontend/README.md。未找到 package.json 时跳过 npm。此入口用于本机 database 队列；现有 `.env` 若设为 celery，会提示改用 Compose，不悄悄改写配置。

## 手动 Conda 路径

先自行创建和激活 Python 3.12 环境，在项目根目录安装：

```powershell
conda create -n industry python=3.12
conda activate industry
python -m pip install -r requirement.txt
# 按需选择一个后端：
python -m pip install -r requirements/gan-pytorch.txt
```

把 config.local.json 的相关字段改为：

```json
{
  "environment_mode": "existing",
  "python_path": "../conda-envs/industry/python.exe",
  "gan_backend": "auto"
}
```

上述路径是占位示例，相对项目根目录解析：仅当环境确实位于相邻 conda-envs/industry 时可直接使用。请按实际位置填写 python.exe，不写 conda.exe；外部环境也可显式使用绝对路径，这属于本机配置而非源码路径。配置支持空格和中文。再双击一键启动即可，不依赖终端是否执行过 conda activate，也不会另外创建 .venv；安装与启动共用所选解释器。

`启动脚本`中的命令行、单服务与检查按钮通过 scripts/Launch.ps1 复用本机部署配置所选解释器，默认根 .venv。底层 scripts/Start-Api.ps1 与 Start-Worker.ps1 仍固定根环境。手动 CLI 可以使用所选环境的 `python -m app.pipeline_cli`。

## 可选命令

```powershell
# 只配置环境，不启动服务
./快速部署/Start.ps1 -SetupOnly
# 强制重新安装依赖
./快速部署/Start.ps1 -Reinstall -SetupOnly
# 只检查配置与启动计划，不创建环境或安装
./快速部署/Start.ps1 -Plan
# 展示并选择版本后只查看计划，选择不保存、不安装
./快速部署/Start.ps1 -ChooseBackend -Plan
# 选择版本并安装，安装后不启动服务
./快速部署/Start.ps1 -ChooseBackend -SetupOnly
# 无人值守时禁止交互，按本机配置部署
./快速部署/Start.ps1 -NonInteractive
# 指定另一份本地配置，路径相对项目根
./快速部署/Start.ps1 -ConfigPath 快速部署/config.local.json
```

首次部署需要网络获取 Python 包、可选向量权重与前端依赖，不是离线安装包。已有 `.env` 和数据不覆盖；API 模型密钥仍由根 `.env` 单独配置，启动器不会从历史聊天提取或打印它。

SetupOnly / Plan 默认不弹菜单，避免自动检查等待输入；需要交互时加 ChooseBackend。NonInteractive 与 ChooseBackend 不能同时使用。无人值守配置中的 gan_backend=auto 仍沿用已有框架，不等同菜单里的“按硬件自动选择”；菜单的硬件自动选择会解析并记住实际 CPU/GPU 版本。
