# AgentFlow 工作流配置规范

## 概述
AgentFlow (`tiny_claw_core/agent_flow.py`) 是一个多 Agent 工作流编排引擎，这里主要通过编写“工作流配置文件”定义多个 Agent 的协作流程来执行工作流。  
该“工作流配置文件”可以通过编写代码执行，也可以通过tiny_claw_console、tiny_claw_tui应用中通过命令执行。


## 工作流配置文件-YAML 结构

> **配置来源**：`llm_models`、`mcp_servers`、`web_search` 均可自动从项目根目录的
> [`configs.json`](../configs.json) 加载，无需在 YAML 中重复定义。
> 详见下方「[配置来源与优先级](#配置来源与优先级)」。

```yaml
flow:
  name: "工作流名称"           # 必填
  description: "描述"          # 可选
  version: "1.0"              # 可选

  # ====== 全局资源（可选，不定义则从 configs.json 自动加载）======
  llm_models:                 # LLM 模型池（可选）
    model_key:                # 引用 key
      api_style: OPENAI
      ai_channel: Deepseek
      ai_model: deepseek-v4-flash
      ai_api_url: "..."
      ai_provider: "DOBA_LLM"
      api_key: "..."
      support_stream: true
      support_tool_call: true
      support_thinking: [true, "off"]
      support_multimodal: false
      api_proxy: null

  mcp_servers:                # MCP 服务池（可选）
    server_key:               # 引用 key
      transport: stdio|sse|streamable-http
      command: "..."          # stdio 用
      args: [...]            # stdio 用
      url: "..."             # sse/streamable-http 用
      env: {KEY: VAL}        # 环境变量
      when_to_use: "描述"
      disabled: false

  web_search:                 # 搜索引擎配置（可选）
    api_key: "..."
    disabled: false

  # ---------- Agent 定义 ----------
  agents:
    - name: "Agent名称"               # 必填，唯一标识
      description: "描述"              # 可选
      workspace_dir: "./agent_workspaces/xxx"  # 工作目录（相对路径基于 yaml 位置）
      llm_model: "deepseek-v4-flash"   # 模型名，匹配 configs.json 或 YAML llm_models 的 ai_model
      llm_override: {}                 # 直接覆盖 LLM 配置（与 llm_model 二选一）
      mcp_servers: ["server_key"]      # 引用 mcp_servers 中的 key
      skills_dir: ""                   # 技能目录（默认 workspace_dir/skills）
      web_search: false                # 是否启用搜索
      system_prompt_extra: "额外提示"   # 附加到系统提示词

  # ---------- 工作流步骤 ----------
  steps:
    - id: "step_id"                   # 必填，唯一标识
      agent: "Agent名称"               # 必填，引用 agents.name
      task: "任务描述模板"              # 必填，支持 {{variable}} 占位符
      mode: "sequential"              # sequential(默认) | parallel(暂未实现)
      output_var: "var_name"          # 输出保存到上下文变量
      retry: 0                        # 失败重试次数
      timeout: 300                    # 超时秒数

  # ---------- 输出配置 ----------
  output:
    dir: "./flow_output"              # 输出目录
    save_logs: true                   # 是否保存步骤日志
```

## 配置来源与优先级

AgentFlow 的 LLM、MCP、Web Search 配置按以下优先级加载：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（最高） | YAML 内联 `llm_override` | agent 中直接写完整 LLM 配置 |
| 2 | YAML 全局 `llm_models` 段 | `llm_model: "key"` 引用 YAML 内定义的模型 |
| 3（默认） | 项目根 `configs.json` | `llm_model: "deepseek-v4-flash"` 按 `ai_model` 匹配 |

> 同名配置 YAML 内定义优先于 `configs.json`。不写 YAML 全局段时，全部自动从
> `configs.json` 加载。推荐的简化 YAML：全局段留空，agent 直接用模型名。

### 简化示例（无全局段）

```yaml
flow:
  name: "travel_planning_flow"
  description: "旅游规划工作流"
  # llm_models / mcp_servers / web_search 均不定义，自动从 configs.json 加载

  agents:
    - name: "旅游规划师"
      workspace_dir: "./agent_workspaces/travel_planner"
      llm_model: "deepseek-v4-flash"        # 直接写 configs.json 中的 ai_model 名
      mcp_servers: []
      web_search: true                       # 启用搜索，api_key 从 configs.json 获取
      system_prompt_extra: "你是资深旅游规划师。"

    - name: "预算审计员"
      workspace_dir: "./agent_workspaces/budget_auditor"
      llm_model: "deepseek-v4-flash"
      mcp_servers: []
      web_search: false
      system_prompt_extra: "你是严谨的预算审计员。"

  steps:
    - id: "plan"
      agent: "旅游规划师"
      task: "为 {{user_request}} 制定行程"
      output_var: "itinerary"

    - id: "audit"
      agent: "预算审计员"
      task: "审计行程预算：{{itinerary}}"
      output_var: "budget_report"
```

## 条件分支（If/Else）

每个步骤可通过 `if` 字段设置条件表达式。条件不满足时步骤自动跳过（标记为 `[SKIPPED]`）。

### 语法

| 表达式 | 说明 | 示例 |
|--------|------|------|
| `context.var exists` | var 存在且非空 | `context.error_log exists` |
| `context.var not_exists` | var 不存在或为空 | `context.error_log not_exists` |
| `context.var == "value"` | 等于 | `context.status == "success"` |
| `context.var != "value"` | 不等于 | `context.status != "failed"` |
| `context.var contains "text"` | 包含子串 | `context.output contains "ERROR"` |
| `context.var starts_with "pre"` | 前缀匹配 | `context.type starts_with "urgent"` |
| `context.var ends_with "suf"` | 后缀匹配 | `context.file ends_with ".py"` |
| `context.var > 10` | 数值大于 | `context.count > 0` |
| `context.var < 10` | 数值小于 | `context.retries < 3` |
| `context.var >= 10` | 数值大于等于 | `context.score >= 60` |
| `context.var <= 10` | 数值小于等于 | `context.retries <= 3` |
| `not (表达式)` | 取反 | `not (context.status == "failed")` |
| `(A) and (B)` | 与（优先级高于 or） | `(context.code exists) and (context.test exists)` |
| `(A) or (B)` | 或（优先级低于 and） | `(context.status == "error") or (context.status == "timeout")` |
| `true` / `false` | 字面量 | `true` |

> 条件表达式**大小写不敏感**。字符串值需要用双引号或单引号包裹。
> 逻辑优先级：`not` > `and` > `or`，可用括号 `()` 改变优先级。

### If-Else 组合模式

用两个互补条件的步骤即可实现 if-else：

```yaml
steps:
  # if: 测试全部通过 → 快速报告
  - id: "quick_report"
    agent: "测试报告员"
    if: "context.test_results contains ✅ 全部通过"
    task: "输出简明通过报告"
    output_var: "final_report"

  # else: 有失败 → 详细报告
  - id: "detailed_report"
    agent: "测试报告员"
    if: "context.test_results not_exists or context.test_results contains ❌"
    task: "输出详细失败分析报告"
    output_var: "final_report"
```

### 条件 + 上下文的完整示例

```yaml
steps:
  - id: "analyze"
    agent: "分析师"
    task: "分析：{{user_request}}"
    output_var: "analysis"

  - id: "quick_fix"
    agent: "开发者"
    if: "context.analysis contains 简单修复"
    task: "执行快速修复：{{analysis}}"

  - id: "full_dev"
    agent: "开发者"
    if: "not (context.analysis contains 简单修复)"
    task: "执行完整开发流程：{{analysis}}"
```

## 上下文传递

工作流上下文 (`context`) 在步骤间传递：

1. **初始上下文**: 通过 `run_flow(path, initial_context={...})` 传入
2. **步骤输出**: 设置 `output_var` 后将步骤输出存入上下文
3. **模板解析**: `{{variable}}` 在 task 中自动替换为上下文中的值

示例：
```yaml
steps:
  - id: step1
    agent: "分析师"
    task: "分析：{{user_request}}"
    output_var: "analysis_result"

  - id: step2
    agent: "开发者"
    task: "根据分析实现：{{analysis_result}}"
```

## Agent 工作目录结构

每个 Agent 的 `workspace_dir` 在工作流 YAML 中配置，路径**基于项目根目录下 `agent_workspaces/` 文件夹**解析。
Agent 启动时目录和文件会自动创建，无需手动准备。

例如 `workspace_dir: "travel_planning_flow/travel_planner"` 解析为：

```
agent_workspaces/travel_planning_flow/travel_planner/
├── AGENT.MD              # Agent 角色定义、话术习惯、执行准则
├── skills/               # 技能目录
│   └── <skill_name>/
│       ├── SKILL.md      # 技能定义（含 YAML frontmatter）
│       └── references/   # 参考文件
├── outputs/              # 对话输出记录（自动生成）
└── ...                   # 其他工作文件
```

> **规范**: 推荐的 `workspace_dir` 格式为 `{flow_name}/{agent_name}`，所有工作流文件统一放在 `agent_workspaces/` 下。

> 如不指定 `workspace_dir`，默认为项目根目录下的 `agent_workspaces/<agent_name>/`。

## 编程接口

### 代码运行工作流程文件()
```python
import asyncio
from tiny_claw_core.agent_flow import run_flow, validate_flow

# 验证配置
errors = validate_flow("tiny_claw_workspace/agent_flow.yaml")
if errors:
    print("配置错误:", errors)
else:
    # 运行工作流
    result = asyncio.run(run_flow(
        "tiny_claw_workspace/agent_flow.yaml",
        initial_context={"user_request": "我的需求..."}
    ))
    print(result["success"])  # True/False
    print(result["outputs"])  # 各步骤输出
```

### Agent 独立使用

也可以单独使用 Agent 类：

```python
from tiny_claw_tui.agent import Agent

agent = Agent(
    name="my_agent",
    workspace_dir="./my_workspace",
)
agent.configure_llm(llm_config_dict)
await agent.start()
result = await agent.process_message("Hello")
await agent.stop()
```

## 在tiny_claw_console、tiny_claw_tui应用中执行工作流配置文件
前提：将工作流配置文件放入应用的flows文件夹中，比如：“flows/agent_flow_travel.yaml”
那么输入命令 `/flow-agent_flow_travel <用户需求>`即可调用该工作流了