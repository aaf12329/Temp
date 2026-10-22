# 前端入口

2026-10-22最新交互：LiveResearch先提交collect，采集完成后LiveResults显示branch_choice的两个按钮；route(llm)复用本批正文继续原流程，route(textbook)返回materials_ready，仅供查看/下载材料，不实现教材后续解析。分支选择不再出现在采集前；材料阶段不显示尚未判定的建模失败或LLM结果。旧确定性正则接口仍兼容保留，与新教材暂停分支区分。详见../docs/采集后分支.md。

2026-10-22资料融合：DataQualityPanel为只接props的数据质量叶子，LiveResults注入实际data_quality。按不同阶段各自分母显示缺失与格式异常，空集显示无候选；data_quality.png是处理统计图，不是模型性能图。桌面设计包未修改。

2026-10-22最新：主项目实际模式使用“LLM数据清洗与趋势分析”选项，同时控制两阶段API。CSV先落盘，中间分批清洗，最终单独趋势请求；LiveResults显示llm_cleaning实际状态。不要再宣称只有最终阶段远程调用。桌面设计交接包未同步此次主项目更改。

先读../AGENTS.md、README.md、Agent.md。所有者已授权总装，主项目包含实际研究与设计预览两种模式；桌面独立设计交接包继续不接后端。

ResearchWorkspace、LiveResearch、ResearchPrototype是显式聚合层。共用契约在src/types，流程及浏览器I/O在src/lib，共用SVG在src/components，Node代理及地址簿在src/server。组件只接props与回调。主项目可以通过统一platform-client接同源接口，功能页不横向操作其他页面。

服务Bearer仅在Node进程，不使用NEXT_PUBLIC或客户端密钥。实际任务不能降级成演示成功。设计预览只用内存与明确合成fixture。用户先选是否训练GAN，再看t-SNE决定是否采用；5倍按真实训练数计算，不含验证/测试。

2026-10-22所有者已恢复本轮测试：主前端类型检查及10项交互测试通过，本轮未做生产构建或浏览器视觉验收；不得把合成演示成功说成真实数据建模成功。生成物与缓存不入库。人类负责最终设计。最新记录见../docs/验证记录_20261022.md。
