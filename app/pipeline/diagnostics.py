"""研究数据门槛诊断，纯逻辑，不训练模型或操作文件。

结构：REASONS → diagnose → no_model_reasons / gan_gate；workflow 编排调用并负责导出。
数据流：真实阶段计数/排除理由 → 瓶颈代码、可操作说明、拒绝原因统计。
诊断不填补样本，也不将 WGAN 未启动标成训练失败。
"""

from collections import Counter

REASONS = {
    "missing_subject": "缺少明确产业主体", "missing_region": "缺少明确地域",
    "missing_measurement_year": "缺少明确测量年度", "missing_publication_date": "缺少明确发布时间",
    "qualified_scope_requires_review": "限额/规模等统计范围需要复核",
    "enterprise_scope_requires_review": "企业口径不能直接视为行业口径",
    "nonannual_period": "月度、季度或活动数据尚不支持年度建模",
    "context_period_requires_review": "上文时间范围需要复核",
    "document_subject_requires_review": "仅标题提供产业主体，需要复核具体指标归属",
    "context_claim_requires_review": "标题或上文含计划/预测，需要复核是否为实测数据",
    "relative_period_requires_review": "相对年份不能直接使用标题年度，需要复核",
    "conflicting_scope_values": "同年同口径存在不同数值",
    "inherited_metric": "指标跨分句继承，需要复核",
    "bounded_or_approximate": "约数或上下界不能当精确观测",
    "ambiguous_subject": "同句存在多个产业主体", "ambiguous_region": "同句存在多个地域",
    "llm_metric_unverified": "LLM指标缺少可核对的原文名称",
    "llm_unit_unverified": "LLM单位缺少原文支持",
    "llm_unit_dimension_mismatch": "指标与单位维度不一致",
    "llm_scope_requires_review": "LLM识别为局部或不明统计范围",
    "llm_scope_unverified": "LLM统计范围引句未通过原文校验",
    "llm_requested_review": "LLM标记需要人工复核",
    "llm_not_selected": "该批清洗未将此规则候选选为相关明确观测，保留待复核",
    "price_basis_requires_review": "价格口径需要复核，不能混用现价和不变价",
    "publication_before_period_end": "发布时间早于年度结束，需核对测量期",
}


def diagnose(counts, rejected, selection):
    """定位采集、召回、抽取、口径或年度序列瓶颈，由 analyze 调用。"""
    steps = [("documents", "no_documents", "没有可用正文，请查看访问记录中的网络、证书或Robots原因。"),
             ("selected_passages", "no_relevant_passages", "正文已采到但没有相关段落，请明确产业/指标并检查关键词。"),
             ("candidate_facts", "no_numeric_candidates", "匹配内容没有抽出带单位的已支持指标，请检查原文及表格格式。"),
             ("cleaned_candidates", "unresolved_scope", "已抽出数字，但年份、地域或统计口径尚未通过检查。"),
             ("series_rows", "no_annual_series", "候选尚不能组成有明确发布时间的同口径年度序列。")]
    code, message = "ready_for_gate", "已形成年度候选，继续检查连续年份和真实训练/验证/测试数量。"
    for field, candidate_code, candidate_message in steps:
        if counts.get(field, 0) == 0:
            code, message = candidate_code, candidate_message
            break
    reasons = Counter(reason for row in rejected for reason in set(row.get("cleaning_reasons") or []))
    return {"code": code, "message": message, "selection": selection,
            "rejection_reasons": [{"code": reason, "message": REASONS.get(reason, reason), "count": count}
                                  for reason, count in reasons.most_common(12)],
            "interpretation": "网页数、段落数和候选数都不等于合格训练样本数；合成数据不能补真实门槛。"}


def no_model_reasons(analysis):
    """无可比序列时给出实际瓶颈，兼容没有新诊断字段的历史产物。"""
    diagnostics = analysis.get("diagnostics") or {}
    reasons = [diagnostics.get("message") or "没有口径、发布时间、年份均明确且无冲突的同口径年度候选。"]
    reasons.extend(f"{item['message']}：{item['count']}条" for item in diagnostics.get("rejection_reasons", [])[:5])
    return reasons


def gan_gate(reports, minimum, mode, *, planning=True):
    """根据实际阶段区分未启动、可选择、已生成和训练失败，不控制训练决策。"""
    states = {item.get("gan", {}).get("status") for item in reports}
    status, reason = "not_started", f"未启动WGAN：须先满足建模条件，且每条序列真实训练集至少{minimum}条。"
    if mode == "off":
        reason = "本轮未启用WGAN；满足建模条件时仅使用真实数据进行XGBoost。"
    elif "failed" in states:
        status, reason = "failed", "WGAN训练或生成后检查发生错误，详见对应序列的GAN原因。"
    elif "dependency_missing" in states:
        status, reason = "dependency_missing", "所选WGAN框架不可用，请检查统一Python环境中的依赖。"
    elif "accepted" in states or "rejected" in states:
        status, reason = "generated", "WGAN已有生成记录；质量结果及是否采用见各序列，本状态不代表最终模型使用了增强数据。"
    elif any(item.get("needs_gan") for item in reports):
        status, reason = (("available", "真实训练量已达到WGAN门槛，请选择是否训练。") if planning else
                          ("not_used", "本轮未训练WGAN，XGBoost仅使用真实数据。"))
    elif any(item.get("split") for item in reports):
        maximum = max(item.get("split", {}).get("train", 0) for item in reports)
        reason = f"未启动WGAN：本批单条序列最多{maximum}条真实训练样本，门槛为{minimum}条；可直接选择真实数据XGBoost。"
    return {"status": status, "reason": reason, "min_real_train_samples": minimum,
            "sample_definition": "每条独立统计口径经时间划分后的真实训练行，不是网页数，也不包含验证/测试或合成行。"}
