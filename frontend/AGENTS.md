# 前端入口

先读../AGENTS.md、README.md、Agent.md。所有者已授权总装，主项目包含实际研究与设计预览两种模式；桌面独立设计交接包继续不接后端。

ResearchWorkspace、LiveResearch、ResearchPrototype是显式聚合层。共用契约在src/types，流程及浏览器I/O在src/lib，共用SVG在src/components，Node代理及地址簿在src/server。组件只接props与回调。主项目可以通过统一platform-client接同源接口，功能页不横向操作其他页面。

服务Bearer仅在Node进程，不使用NEXT_PUBLIC或客户端密钥。实际任务不能降级成演示成功。设计预览只用内存与明确合成fixture。用户先选是否训练GAN，再看t-SNE决定是否采用；5倍按真实训练数计算，不含验证/测试。

当前所有者要求暂停测试，仅搭建与文档收口。不要再运行测试、训练或冒烟验收，也不要把此前的通过结果写成最后版本已验收。生成物与缓存不入库。人类负责最终设计。
