# 多论文阶段 1：PaperDocument v2

阶段 1 已实现论文解析路由、PaperDocument v2、学术元数据补充边界、独立
DocStore 和稳定标识。该阶段只生成旁路候选，不创建向量索引、不写 MySQL，
也不切换阶段 0 的活动 release。

## 实现边界

```text
PDF bytes
  -> 轻量 PDF 探针（页数、可提取文本、疑似 OCR 页）
  -> ParserRouter
       -> DoclingPaperLoader（主路径，可选本地依赖）
       -> MarkItDownDocumentLoader（显式回退）
       -> GrobidMetadataEnricher（仅补充，不覆盖冲突字段）
  -> PaperDocumentNormalizer
  -> paper_docstore_v2/<paper_document_id>.json
  -> 阶段 1 候选摘要（不发布）
```

供应商类型只存在于 `docling_loader.py` 和 `grobid.py`。`core`、稳定模型和
存储契约不导入 Docling/GROBID 类型。Docling 使用官方 `DocumentConverter`
和 `DocumentStream` 边界；GROBID 使用官方
`/api/processFulltextDocument` multipart TEI 接口，并关闭 CrossRef/biblio-glutton
consolidation，避免元数据补充产生隐式外部调用。

## PaperDocument v2

- `document_id`：schema、文件名、原 PDF SHA256 和 ingestion schema 的规范化哈希。
- `block_id`：document、块类型、序号、章节路径、页码/坐标和正文哈希。
- `bibliographic_metadata`：标题、作者、机构、年份、venue、语言、关键词、摘要、
  DOI、外部 ID 和参考文献，并记录字段来源及冲突。
- `blocks`：阶段 1 的 section/paragraph/table/formula/caption/reference 结构块。
- `relations`：稳定的前后关系和 section-parent 关系；父子检索节点留到阶段 2。
- `diagnostics`：解析器、回退、页数、OCR、结构计数、碎片率、冲突、警告、
  人工复核和发布资格。

现有 `unified-document-v1` 与 `paper-document-v2` 使用不同目录。旧 API、非论文
格式、阶段 0 DocStore 和 974 节点在线索引不受影响。

## 真实候选结果

冻结摘要位于 `config/rag_phase1_paper_document_v2.json`：

- 候选 ID：`f03d6b8f0eb934a3ee9cb559c36349a7`
- 15 篇 PaperDocument，226 个结构块，15 篇可继续处理，0 篇阻断。
- 活动 release 前后均为 `b17672e25ed44ee793a8799def2d968e`。
- 当前机器未安装 Docling/模型权重且未配置 GROBID，因此 15 篇均明确标记
  `degraded`，有效正文来自 MarkItDown 回退；这不是 Docling 质量评测结果。
- 《半监督自训练方法综述》视觉抽查确认内嵌字体映射异常，保留
  `manual_review_required`，不得把乱码文本当成可靠语义证据。
- 双栏中文铝材论文的回退文本曾产生 2496 个微片段；稳定微片段合并后为
  45 个结构块，并增加每页块密度与微块比例诊断。

磁盘仅剩约 2.7 GiB，本次没有下载 Docling 模型。需要启用主路径时先准备足够
空间，再安装 `requirements-paper-rag.txt` 并预取 Docling PDF 模型；GROBID
通过 `AI_RAG_GROBID_URL` 指向受控服务。

## 验证命令

```bash
python3 scripts/build_rag_paper_documents_v2.py \
  --source-dir "/Users/xiaohao/Desktop/杂物/Papers"
python3 scripts/check_rag_phase1.py
python3 scripts/check_rag_phase1.py --verify-runtime
python3 -m unittest tests.test_rag_paper_document_v2 tests.test_rag_phase1
```

阶段 1 的“完成”指实现、旁路候选、稳定重建、兼容和降级验证完成；候选状态仍是
`validated_not_published_degraded`。只有在具备 Docling/GROBID 运行条件并完成
主路径解析质量评测后，才应将其用于阶段 2 多粒度索引实验。
