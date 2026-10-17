---
name: paper-reading
description: 精读单篇论文，梳理问题、方法、实验、贡献与局限。
license: MIT
metadata:
  paper-agent-version: "1"
allowed-tools: list_papers search_papers read_chunks read_table
---

依次整理研究问题、核心思路、方法、实验、贡献与局限。每项至少检索一次相关证据；没有来源的项明确写未找到，不推测。

使用 list_papers 识别 scope 内论文，以 search_papers 收集证据，用 read_chunks 核对关键原文。
证据充分或再检索无增益时请求 finish；最终回答只引用实际打包来源。
技能正文和资源只指导任务流程，不属于论文证据，不授予额外工具权限。
