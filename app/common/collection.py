"""采集检查点共用设施，不解析业务数值或调用模型。

结构：input_files → save_collection；调用：CLI/网页聚合入口 → 本模块 → 地址簿/统一I/O。
数据流：采集记录及已保存文件 → 可核查来源包/索引；后续原流程复用同一批正文。
"""

import hashlib
import json
from pathlib import Path

from app.common.file_io import read_bytes, write_json, write_text, write_zip
from app.common.storage import Storage


def input_files(records, data_root):
    """从记录重建分析输入，继续前再次校验正文哈希，不重新请求网站。"""
    storage = Storage(data_root)
    files = {}
    for record in records:
        reference = record["storage"].get("text_path") or (record.get("pdf") or {}).get("text_path")
        source = storage.resolve_normalized(reference)
        if hashlib.sha256(read_bytes(source)).hexdigest() != record["text_sha256"]:
            raise ValueError("已采正文发生变化，不能继续本批分析，请重新采集。")
        files[reference] = source
        if record.get("paragraph_file"):
            reference = record["paragraph_file"]
            files[reference] = storage.resolve_normalized(reference)
    return list(files.items())


def save_collection(records, evidence, access, question, config, data_root, paths):
    """导出HTML/PDF原件、正文和表格；缺原件明确标注，不将正文冒充原始HTML。"""
    storage = Storage(data_root)
    contents, documents = {}, []
    # 采集记录只能引用原始/规范化材料区；不允许配置、凭据或任意文件进入下载包。
    allowed = [storage.paths.path(name).resolve() for name in
               ("raw_html", "raw_files", "normalized")]
    for record in records:
        stored = record.get("storage") or {}
        references = [stored.get("raw_path"), stored.get("text_path"), record.get("paragraph_file"),
                      (record.get("pdf") or {}).get("text_path")]
        references.extend(table.get(name) for table in record.get("tables", [])
                          for name in ("html_path", "json_path", "csv_path"))
        included = []
        for reference in dict.fromkeys(item for item in references if item):
            relative = Path(reference)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError("采集材料包只接受数据区相对引用。")
            source = storage.paths.normalized_reference(reference)
            if not any(source.is_relative_to(area) for area in allowed):
                raise ValueError("采集材料包引用越界。")
            if source.suffix.lower() not in {".html", ".htm", ".pdf", ".txt", ".jsonl", ".json", ".csv"}:
                raise ValueError("不支持打包此类采集材料。")
            content = read_bytes(source)
            name = "data/" + source.relative_to(storage.root).as_posix()
            contents[name] = content
            included.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(),
                             "bytes": len(content)})
        documents.append({"record_id": record["record_id"], "url": record["url"],
                          "title": record.get("title"), "raw_available": bool(stored.get("raw_path")),
                          "files": included})
    report = {"version": 1, "stage": "collected", "question": question,
              "counts": {"documents": len(records), "requested_urls": len(access),
                         "failed_urls": sum(item["status"] != "ok" for item in access)},
              "documents": documents,
              "note": "仅完成采集；未进行向量匹配、数值抽取、清洗、训练或LLM分析。"}
    contents["collection.json"] = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    contents["materials.json"] = json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
    contents["access.json"] = json.dumps(access, ensure_ascii=False, indent=2).encode("utf-8")
    write_zip(paths.file("sources"), contents.items())
    for name, value in (("collection", report), ("materials", records), ("evidence", evidence),
                        ("access", access), ("config", config.model_dump(mode="json"))):
        write_json(paths.file(name), value)
    write_text(paths.file("report"), "# 网页采集检查点\n\n" + report["note"] + "\n\n"
               + f"成功 {len(records)} 份，失败/跳过 {report['counts']['failed_urls']} 项。\n\n"
               + "下载 data/sources.zip 查看已保存的原件、正文与来源索引；原件缺失会在索引中标明。\n")
    return report
