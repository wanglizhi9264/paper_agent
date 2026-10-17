# 当前切片与来源信息

本文描述当前代码行为，不改变 parser、切片参数或索引。

## 表格没有统一塞进一个 chunk

PDF V2 先把表格解析为 Canonical Document IR，保存完整表格的行列、原始 cell 值、合并跨度及 provenance；再由 `app/chunking/table.py:chunk_table_element` 生成：

| 类型 | 内容 | 用途 |
|---|---|---|
| `table_parent` | 文档/章节/表标识、表头等父级信息 | 组织关联；`app/index/manager.py` 明确不将其加入索引；它不是整表所有数值的副本 |
| `table_row` | 一行的行标题、列标题和数值 | 可检索的完整行证据 |
| `table_group` | 一行内的一组列及其表头 | 多级表头、较长行等条件下生成，支持精确指标检索 |
| `table_raw_text` | 解析器提供的原始整表文本副本 | 可选补充召回，不声称具有可靠 cell/bbox；不是所有表都有 |

行和列组保存 `parent_chunk_index`，写入数据库后解析成真实 `parent_chunk_id`；另有 `element_id`、table fingerprint、row_indices、row_header_path、column_header_paths、cell_ids、physical_pages 和 bboxes。同表行块/列组可以重叠，不是互斥分区。

`raw_content` 保留原始值及用于引用的表格表达；`retrieval_content` 补充文档、章节、表名和规范化检索字段。两者分开保存和使用。表格不会和前后普通段落统一混成一个引用 chunk。

`read_table` 根据 chunk 的 element_id 回到已校验的 IR，读取选定行/cells，并附适用的列头和合并行头，最多 40 cells。同页解释段落返回各自的 chunk ID，不藏在表格 ID 后面。Agent 对同一个表的分次选行结果会合并，避免第二次覆盖第一次读到的数据。MCP 客户端负责跨调用汇总其证据。

## 普通文本与旧路径

`app/chunking/pipeline.py` 默认按句子、标题路径和字符阈值切片；当前默认 max_chunk_chars=800，属于字符阈值，不是 tokenizer 的 token 上限。小文档、代码和标题有自己的策略。

Markdown/DOCX 等 legacy paragraph 路径中的表格不完全等同于 PDF V2 的 cell-aware IR：较短表格可以是一个 chunk，长表按行拆分并重复表头。不能据此承诺所有格式都具有可验证的 cell IDs/bbox。`table_on=false` 会走文本回退。

## 页码、章节如何保存

ORM `app/models/chunk.py:Chunk` 存储 `page_start/page_end`、`section_path`、`document_id/document_version_id`、父块/章节块关联和 metadata。来源 API/MCP 会返回页码、章节路径、版本与表格 provenance。

- PDF 页码来自 parser 的 physical_page/provenance，通常为 1 起始的物理页，不保证等于论文印刷页号。
- PDF V2 的 section_path 由 parser 识别标题层级后传递给元素，再继承到 chunk；当前 PyMuPDF adapter 使用字体/行分类等启发式，其他 parser adapter 通过自己的 IR 映射。
- legacy 文本路径由 heading tree 构建标题祖先路径。没有可靠分页的格式可为空，未识别章节时路径可为空；不会由生成模型临时猜测补齐。
- 存有字段不代表解析结果必然正确。当前规则可能只识别到论文标题、目录或不完整层级，复杂版面/跨页结构仍有已知限制。

2026-10-17 对本机 active DocumentVersion 的只读审计：8724 个 chunk 均有 page_start；章节路径深度分布为 0 层 470、1 层 5133、2 层 1843、3 层 369、4 层 909。表格类型为 parent 151、row 1091、group 2410、raw_text 47。统计包括非索引父块，不能当作向量条目总数，也不等于 parser 质量达标。

## 阅读代码顺序

1. `app/loaders/pymupdf_adapter.py`：PDF 元素、物理页和标题路径。
2. `app/document_ir/models.py`：表格、cell、合并跨度、bbox 的来源契约。
3. `app/chunking/table.py` 与 `pipeline.py`：表格/普通文本切片。
4. `app/models/chunk.py`：实际数据库字段。
5. `app/index/manager.py`：排除 table_parent 等不入索引块。
6. `app/services/evidence_reader.py`：IR 表格读取；`app/agent/evidence.py`：同表选行合并。
