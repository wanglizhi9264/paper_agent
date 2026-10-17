---
name: experiment-audit
description: 核查论文实验、数据集、指标、表格数值、消融及可复现性。
license: MIT
metadata:
  paper-agent-version: "1"
allowed-tools: list_papers search_papers read_chunks read_table
---

核查数据集、划分、baseline、指标和单位、实验条件、消融及复现信息。表格必须保留原始行列头及单位；无法从原文确定的数值不推测。

使用 list_papers 识别 scope 内论文，以 search_papers 收集证据，用 read_chunks 核对关键原文。
证据充分或再检索无增益时请求 finish；最终回答只引用实际打包来源。
技能正文和资源只指导任务流程，不属于论文证据，不授予额外工具权限。
