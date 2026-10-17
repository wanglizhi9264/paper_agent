---
name: paper-comparison
description: 比较两篇以上论文的方法、实验、贡献与局限。
license: MIT
metadata:
  paper-agent-version: "1"
allowed-tools: list_papers search_papers read_chunks read_table
---

先确认论文与比较维度；每篇独立检索再读原文，保证每篇有证据。比较数据集、划分、指标和训练条件，条件不同不得直接排名。输出逐论文比较、不可比条件和缺口。

使用 list_papers 识别 scope 内论文，以 search_papers 收集证据，用 read_chunks 核对关键原文。
证据充分或再检索无增益时请求 finish；最终回答只引用实际打包来源。
技能正文和资源只指导任务流程，不属于论文证据，不授予额外工具权限。
