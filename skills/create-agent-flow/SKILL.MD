---
name: create-agent-flow
description: 'Create and validate TinnyClaw AgentFlow YAML configurations. Use when: designing multi-agent workflows, creating agent_flow.yaml, configuring Agent LLM/MCP/skills, defining step pipelines with output variables, adding conditional branching (if/else), or validating flow configs. Triggered by: "create agent flow", "workflow", "multi-agent", "agent_flow.yaml", "flow config", or /flow- commands.'
argument-hint: '[flow-name] [brief description]'
---

# TinnyClaw AgentFlow 工作流创建

你是 TinnyClaw AgentFlow 配置专家。AgentFlow 是一个多 Agent 工作流编排引擎，通过 YAML 配置驱动多个 Agent 按步骤协作完成任务。工作流配置可通过代码运行，也可在 `tiny_claw_console` 中通过 `/flow-<name>` 命令执行。

## 完整配置参考

所有字段定义、优先级规则、条件表达式语法等详细规范，请按需读取：  
→ [`@skill/references/agent-flow-spec.md`](./references/agent-flow-spec.md)

---

## 创建流程

### 第 1 步：需求分析

向用户澄清：
- **任务目标**：工作流要完成什么？可否拆成独立阶段？
- **Agent 角色**：每个阶段由谁负责？需要什么能力？
- **数据流向**：哪些阶段的产出要给后续阶段？
- **条件分支**：是否有「如果 A 则走 X，否则走 Y」的场景？

### 第 2 步：定义 Agent 列表

为每个阶段的角色定义一个 Agent：

```yaml
agents:
  - name: "Agent名称"                    # 必填，唯一标识，步骤引用此名称
    description: "一句话角色描述"          # 可选
    workspace_dir: "flow_name/agent_name" # 工作目录，自动创建在 agent_workspaces/ 下
    llm_model: "deepseek-v4-flash"        # 模型名，匹配项目 configs.json 中的 ai_model
    mcp_servers: []                       # 需要的 MCP 服务 key 列表
    skills_dir: ""                        # 技能目录（默认 workspace_dir/skills）
    web_search: false                     # 是否启用网络搜索
    system_prompt_extra: "角色说明+工作准则" # 附加到系统提示词
```

> **配置自动加载**：`llm_model`、`mcp_servers`、`web_search` 的底层配置（api_key、api_url 等）不需要在 YAML 中写，会自动从项目根 `configs.json` 加载。只有需要覆盖时，才通过 `llm_override` 或 YAML 全局段定义。

### 第 3 步：设计 Steps 流程

定义步骤序列，串联数据流：

```yaml
steps:
  - id: "step_id"                     # 必填，唯一标识
    agent: "Agent名称"                 # 必填，引用 agents.name
    task: "任务描述 {{variable}}"       # 必填，{{variable}} 引用上游 output_var
    output_var: "result_var"          # 输出保存为上下文变量，供后续步骤引用
    retry: 0                          # 失败重试次数（可选）
    timeout: 300                      # 超时秒数（可选）
```

**数据传递规则**：上一步 `output_var: "xxx"` → 后续步骤中用 `{{xxx}}` 引用。初始变量（如 `{{user_request}}`）通过代码 `initial_context` 传入，或通过 `/flow-xxx` 命令参数传入。

### 第 4 步：添加条件分支（可选）

当需要根据上游结果走不同路径时，用 `if` 字段：

```yaml
- id: "success_path"
  agent: "报告员"
  if: "context.status == success"      # 条件满足才执行，否则跳过
  task: "生成成功报告"

- id: "failure_path"
  agent: "报告员"
  if: "not (context.status == success)" # 互补条件实现 if-else
  task: "生成失败分析报告"
```

常用条件表达式（大小写不敏感）：

| 表达式 | 示例 |
|--------|------|
| `context.var exists` / `not_exists` | `context.error exists` |
| `context.var == "value"` / `!=` | `context.status == "ok"` |
| `context.var contains "text"` | `context.output contains "ERROR"` |
| `context.var starts_with "pre"` / `ends_with "suf"` | `context.file ends_with ".py"` |
| `context.var > N` / `< N` | `context.count > 0` |
| `context.var >= N` / `<= N` | `context.score >= 60` |
| `not (expr)` / `(A) and (B)` / `(A) or (B)` | `(context.err) or (context.timeout)` |

### 第 5 步：组装完整 YAML

```yaml
flow:
  name: "flow_name"             # 必填
  description: "一句话描述"      # 可选
  version: "1.0"               # 可选

  agents:
    - name: "Agent1"
      workspace_dir: "flow_name/agent1"
      llm_model: "deepseek-v4-flash"
      system_prompt_extra: "你是..."

  steps:
    - id: "step1"
      agent: "Agent1"
      task: "处理：{{user_request}}"
      output_var: "result"

  output:
    dir: "./flow_output"
    save_logs: true
```

**保存位置**：放入项目的 `flows/` 目录，如 `flows/my_workflow.yaml`。

### 第 6 步：创建 Agent workspace（可选，自动创建）

Agent 启动时会自动创建 `agent_workspaces/<workspace_dir>/` 目录。建议额外创建 `AGENT.MD` 文件定义角色细节：

```
agent_workspaces/<flow_name>/<agent_name>/
├── AGENT.MD              # Agent 角色定义（可选，推荐）
├── skills/               # 技能目录
└── outputs/              # 输出记录（自动生成）
```

### 第 7 步：验证配置

```python
from tiny_claw_core.agent_flow import validate_flow

errors = validate_flow("flows/my_workflow.yaml")
if errors:
    print("配置错误:", errors)
```

---

## 常见工作流模式

### 模式 1：分析 → 执行 → 审查

```yaml
steps:
  - id: "analyze"
    agent: "分析师"
    task: "分析需求：{{user_request}}"
    output_var: "analysis"
  - id: "implement"
    agent: "开发者"
    task: "实现：{{analysis}}"
    output_var: "result"
  - id: "review"
    agent: "审查员"
    task: "审查：{{result}}"
    output_var: "report"
```

### 模式 2：条件路径

```yaml
steps:
  - id: "diagnose"
    agent: "诊断师"
    task: "诊断：{{issue}}"
    output_var: "diagnosis"
  - id: "quick"
    agent: "处理师"
    if: "context.diagnosis contains 简单"
    task: "快速处理：{{diagnosis}}"
  - id: "deep"
    agent: "处理师"
    if: "not (context.diagnosis contains 简单)"
    task: "深度处理：{{diagnosis}}"
```

### 模式 3：多源汇总

```yaml
steps:
  - id: "research_a"
    agent: "研究员A"
    task: "从A角度调研：{{topic}}"
    output_var: "view_a"
  - id: "research_b"
    agent: "研究员B"
    task: "从B角度调研：{{topic}}"
    output_var: "view_b"
  - id: "merge"
    agent: "汇总员"
    task: "汇总：A={{view_a}}, B={{view_b}}"
```

---

## 注意事项

1. **配置自动加载优先**：除非有特殊覆盖需求，不要写 YAML 全局 `llm_models`/`mcp_servers`/`web_search` 段，让 configs.json 自动加载
2. **变量名精确匹配**：`{{variable}}` 大小写敏感，必须与 `output_var` 名称完全一致
3. **workspace_dir 规范**：推荐 `{flow_name}/{agent_name}` 格式，统一管理在 `agent_workspaces/` 下
4. **步骤顺序**：按 YAML 中定义顺序串行执行
