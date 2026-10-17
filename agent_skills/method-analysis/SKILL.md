---
name: method-analysis
description: 解释论文的方法、目标函数、模块及训练和推理流程。
license: MIT
metadata:
  paper-agent-version: "1"
allowed-tools: list_papers search_papers read_chunks read_table
---

检查输入输出、核心模块、目标函数、假设、训练与推理过程。必要时定向读取方法章节；公式解析不完整时明确说明。

使用 list_papers 识别 scope 内论文，以 search_papers 收集证据，用 read_chunks 核对关键原文。
证据充分或再检索无增益时请求 finish；最终回答只引用实际打包来源。
技能正文和资源只指导任务流程，不属于论文证据，不授予额外工具权限。
