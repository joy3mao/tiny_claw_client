# coding:utf-8
"""AgentFlow — 多Agent工作流编排引擎。

通过 `agent_flow.yaml` 配置文件定义多个 Agent 的协作流程，
支持顺序执行、条件分支、上下文传递、并行执行等模式。

YAML 规范详见 AGENT_FLOW_SPEC.md 或 example agent_flow.yaml。
"""
import asyncio, json, os, sys, re, traceback
from datetime import datetime
from typing import Any, Dict, Optional, List, Union
from dataclasses import dataclass, field
import yaml

from .agent import Agent, EventType, SessionEvent, BASE_TOOLS

# 项目根目录（configs.json 所在位置）
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIGS_JSON_PATH = os.path.join(_PROJECT_DIR, "configs.json")
# Agent 工作区根目录（workspace_dir / output.dir 均默认相对于此目录）
AGENT_WORKSPACES_DIR = os.path.join(_PROJECT_DIR, "agent_workspaces")


def _load_configs_json() -> dict:
    """加载项目根目录的 configs.json"""
    if os.path.isfile(_CONFIGS_JSON_PATH):
        with open(_CONFIGS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ====================== Data Models ======================
@dataclass
class FlowAgentConfig:
    """单个 Agent 在 workflow 中的定义"""
    name: str
    description: str = ""
    workspace_dir: str = ""
    llm_model: str = ""                     # 引用 global llm_models 中的 key
    llm_override: dict = field(default_factory=dict)  # 直接覆盖 LLM 配置
    mcp_servers: list = field(default_factory=list)    # 引用 global mcp_servers 中的 key
    skills_dir: str = ""
    web_search: bool = False
    system_prompt_extra: str = ""           # 额外的系统提示词


@dataclass
class FlowStep:
    """工作流中的一个步骤"""
    id: str
    agent: str                              # 引用 agent name
    task: str                               # 任务描述模板
    mode: str = "sequential"                # sequential | parallel
    output_var: str = ""                    # 保存输出到上下文变量名
    if_condition: str = ""                  # 可选的条件表达式
    retry: int = 0                          # 失败重试次数
    timeout: int = 300                      # 超时秒数


@dataclass
class AgentFlowDefinition:
    """完整的 workflow 定义"""
    name: str
    description: str = ""
    version: str = "1.0"

    # 全局资源
    llm_models: Dict[str, dict] = field(default_factory=dict)
    mcp_servers: Dict[str, dict] = field(default_factory=dict)
    web_search: dict = field(default_factory=dict)

    # Agent 定义
    agents: List[FlowAgentConfig] = field(default_factory=list)

    # 步骤定义
    steps: List[FlowStep] = field(default_factory=list)

    # 输出配置
    output: dict = field(default_factory=lambda: {"dir": "./flow_output", "save_logs": True})


# ====================== Flow Runner ======================
class AgentFlowRunner:
    """AgentFlow 工作流执行器

    用法:
        runner = AgentFlowRunner("path/to/agent_flow.yaml")
        result = await runner.run()
    """

    def __init__(self, flow_path: str, event_callback=None, console_stream: bool = False):
        self.flow_path = os.path.abspath(flow_path)
        self.flow_dir = os.path.dirname(self.flow_path)
        self.definition: Optional[AgentFlowDefinition] = None
        self._agents: Dict[str, Agent] = {}           # name -> Agent instance
        self._context: Dict[str, Any] = {}            # 工作流上下文（跨 Agent 传递）
        self._cancel_event = asyncio.Event()
        self._configs_json: dict = {}

        # 统一事件回调：用户回调 + 控制台流式输出
        self._event_callback = self._make_event_callback(
            event_callback, console_stream)

    @staticmethod
    def _make_event_callback(user_callback, console_stream: bool):
        """创建一个统一的事件回调，同时处理用户回调和控制台输出"""
        if not user_callback and not console_stream:
            return None
        elif user_callback and not console_stream:
            return user_callback
        elif not user_callback and console_stream:
            return AgentFlowRunner._console_event_handler
        else:
            def combined(event):
                user_callback(event)
                AgentFlowRunner._console_event_handler(event)
            return combined

    def _emit(self, event_type: EventType, data=None, **extra):
        if self._event_callback:
            self._event_callback(SessionEvent(type=event_type, data=data, extra=extra))

    def _find_llm_from_configs(self, model_name: str) -> Optional[dict]:
        """从 configs.json llm_models 数组查找第一个非 disabled 的匹配配置"""
        for m in self._configs_json.get("llm_models", []):
            if not m.get("disabled", False) and m.get("ai_model") == model_name:
                return {k: v for k, v in m.items() if k != "disabled"}
        return None

    @staticmethod
    def _console_event_handler(event: SessionEvent):
        """控制台流式输出处理器"""
        import sys
        et = event.type
        d = event.data or {}

        if et == EventType.STREAMING:
            sys.stdout.write(d.get("content", ""))
            sys.stdout.flush()
        elif et == EventType.THINKING:
            content = d.get("content", "")
            sys.stdout.write(f"\r💭 {content}\033[K")
            sys.stdout.flush()
        elif et == EventType.STREAM_DONE:
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif et == EventType.TOOL_CALL:
            name = d.get("tool_name", "")
            args = d.get("arguments", {})
            sys.stdout.write(f"\n🔧 工具调用: {name}")
            if args:
                import json
                sys.stdout.write(f"({json.dumps(args, ensure_ascii=False)})")
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif et == EventType.TOOL_RESULT:
            status = d.get("status", "")
            result = str(d.get("result", ""))[:300]
            icon = "✅" if status == "success" else "❌"
            sys.stdout.write(f"{icon} 工具结果: {result}\n")
            sys.stdout.flush()
        elif et == EventType.SYSTEM_INFO:
            msg = d.get("message", "")
            sys.stdout.write(f"  {msg}\n")
            sys.stdout.flush()
        elif et == EventType.ERROR:
            msg = d.get("message", "")
            sys.stdout.write(f"  ❌ {msg}\n")
            sys.stdout.flush()

    def cancel(self):
        """取消当前运行的工作流"""
        self._cancel_event.set()
        for agent in self._agents.values():
            agent.cancel_current()

    # ====================== YAML Parsing ======================
    def _resolve_path(self, path: str) -> str:
        """解析路径（相对路径基于 agent_workspaces 目录）"""
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(AGENT_WORKSPACES_DIR, path))

    def _resolve_placeholder(self, text: str) -> str:
        """解析模板中的 {{variable}} 占位符"""
        import re
        def _replacer(m):
            var_name = m.group(1).strip()
            return str(self._context.get(var_name, m.group(0)))
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replacer, text)

    def load(self) -> 'AgentFlowRunner':
        """加载并解析 YAML 文件"""
        # 加载 configs.json，YAML 中的定义可覆盖
        self._configs_json = _load_configs_json()

        with open(self.flow_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not raw:
            raise ValueError("空的 YAML 配置")

        flow = raw.get("flow", raw)
        flow_name = flow.get("name", os.path.splitext(os.path.basename(self.flow_path))[0])
        self.definition = AgentFlowDefinition(
            name=flow_name,
            description=flow.get("description", ""),
            version=flow.get("version", "1.0"),
            llm_models=flow.get("llm_models", {}),
            mcp_servers=flow.get("mcp_servers", {}),
            web_search=flow.get("web_search", {}),
            output=flow.get("output", {"dir": flow_name, "save_logs": True}),
        )

        # 解析 agents
        for a in flow.get("agents", []):
            self.definition.agents.append(FlowAgentConfig(
                name=a["name"],
                description=a.get("description", ""),
                workspace_dir=self._resolve_path(a.get("workspace_dir", f"{flow_name}/{a['name']}")),
                llm_model=a.get("llm_model", ""),
                llm_override=a.get("llm_override", {}),
                mcp_servers=a.get("mcp_servers", []),
                skills_dir=self._resolve_path(a.get("skills_dir", "")),
                web_search=a.get("web_search", False),
                system_prompt_extra=a.get("system_prompt_extra", ""),
            ))

        # 解析 steps
        for s in flow.get("steps", []):
            self.definition.steps.append(FlowStep(
                id=s.get("id", f"step_{len(self.definition.steps) + 1}"),
                agent=s["agent"],
                task=s["task"],
                mode=s.get("mode", "sequential"),
                output_var=s.get("output_var", ""),
                if_condition=s.get("if", ""),
                retry=s.get("retry", 0),
                timeout=s.get("timeout", 300),
            ))

        return self

    # ====================== Agent Management ======================
    async def _create_agents(self):
        """根据定义创建所有 Agent 实例"""
        # 确定 MCP/WebSearch 数据源：优先 YAML 定义，否则从 configs.json 获取
        mcp_source = (self.definition.mcp_servers
                       if self.definition.mcp_servers
                       else self._configs_json.get("mcp_servers", {}))
        ws_source = (self.definition.web_search
                      if self.definition.web_search
                      else self._configs_json.get("web_search", {}))

        # 工作流无人值守，不能提供需要用户临场交互的工具/行为：
        # - ask_user 会让 Agent 停下来等人回答选择题，这里没有人能回答，永远等不到；
        # - 危险命令确认同理，没人能点确认/取消——改为遇到危险命令直接自动拒绝执行
        #   （见 Agent(auto_reject_dangerous_command=True) 及 execute_bash 里的判断）。
        flow_base_tools = [t for t in BASE_TOOLS if t["function"]["name"] != "ask_user"]

        for cfg in self.definition.agents:
            agent = Agent(
                name=cfg.name,
                workspace_dir=cfg.workspace_dir,
                skills_dir=cfg.skills_dir or os.path.join(cfg.workspace_dir, "skills"),
                event_callback=self._event_callback,
                base_tools_override=flow_base_tools.copy(),
                auto_reject_dangerous_command=True,
            )

            # 配置 LLM — 优先 llm_override，其次 YAML 定义，最后 configs.json
            llm_config = None
            if cfg.llm_override:
                llm_config = cfg.llm_override
            elif cfg.llm_model:
                if cfg.llm_model in self.definition.llm_models:
                    llm_config = self.definition.llm_models[cfg.llm_model]
                else:
                    llm_config = self._find_llm_from_configs(cfg.llm_model)
            if llm_config:
                agent.configure_llm(llm_config)

            # 配置 MCP
            agent_mcp = {}
            for mcp_key in cfg.mcp_servers:
                if mcp_key in mcp_source:
                    agent_mcp[mcp_key] = mcp_source[mcp_key]
            if agent_mcp:
                agent.mcp_configs = agent_mcp

            # Web search
            if cfg.web_search and ws_source:
                if WEB_SEARCH_TOOL not in agent._base_tools:
                    agent._base_tools.append(WEB_SEARCH_TOOL)
                    agent.tool_handlers["web_search"] = lambda _a=agent, **kw: _a.web_search(kw["query"])
                # 将 api_key 注入 agent.llm_config，供 agent.web_search() 使用
                if isinstance(ws_source, dict) and "api_key" in ws_source:
                    agent.llm_config["web_search_api_key"] = ws_source["api_key"]

            self._agents[cfg.name] = agent

    async def _start_agents(self):
        """启动所有 Agent"""
        for name, agent in self._agents.items():
            try:
                await agent.start()
            except Exception as e:
                self._emit(EventType.ERROR, {"message": f"Agent [{name}] 启动失败: {e}"})

    async def _stop_agents(self):
        """停止所有 Agent"""
        for name, agent in self._agents.items():
            try:
                await agent.stop()
            except Exception:
                pass

    # ====================== Condition Evaluation ======================
    def _evaluate_condition(self, condition: str) -> bool:
        """求值条件表达式，返回 True/False。

        支持的语法（大小写不敏感）:
            context.var exists              → var 存在且非空
            context.var not_exists          → var 不存在或为空
            context.var == "value"          → 相等
            context.var != "value"          → 不等
            context.var contains "text"     → 包含子串
            context.var starts_with "pre"   → 前缀匹配
            context.var ends_with "suf"     → 后缀匹配
            context.var > 10                → 数值大于
            context.var < 10                → 数值小于
            not (condition)                 → 取反
            (cond1) and (cond2)             → 与
            (cond1) or (cond2)              → 或
            true / false                    → 字面量
        """
        expr = condition.strip()

        # 字面量
        if expr.lower() == "true":
            return True
        if expr.lower() == "false":
            return False

        # not (...)  — 递归取反
        not_match = re.match(r"^not\s+\((.+)\)$", expr, re.IGNORECASE)
        if not_match:
            return not self._evaluate_condition(not_match.group(1))

        # (...) or (...) — 用 or 分割（不在括号内），or 优先级低于 and
        or_parts = self._split_logical(expr, "or")
        if len(or_parts) > 1:
            return any(self._evaluate_condition(p.strip()) for p in or_parts)

        # (...) and (...) — 用 and 分割（不在括号内），and 优先级高于 or
        and_parts = self._split_logical(expr, "and")
        if len(and_parts) > 1:
            return all(self._evaluate_condition(p.strip()) for p in and_parts)

        # 去掉外层括号
        while expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1].strip()

        # context.var <op> <value>
        # 支持的操作符（长到短排列，避免贪婪匹配问题）
        operators = [
            (" exists", self._op_exists, False),
            (" not_exists", self._op_not_exists, False),
            (" != ", self._op_ne, True),
            (" == ", self._op_eq, True),
            (" contains ", self._op_contains, True),
            (" starts_with ", self._op_starts_with, True),
            (" ends_with ", self._op_ends_with, True),
            (" >= ", self._op_ge, True),
            (" <= ", self._op_le, True),
            (" > ", self._op_gt, True),
            (" < ", self._op_lt, True),
        ]

        for op_name, op_func, has_rhs in operators:
            idx = expr.lower().find(op_name)
            if idx < 0:
                continue
            lhs = expr[:idx].strip()
            rhs = expr[idx + len(op_name):].strip() if has_rhs else ""
            return op_func(lhs, rhs)

        # 未知条件表达式 → 视为 false（安全降级）
        return False

    # ---- 内部：操作符实现 ----

    def _get_context_value(self, key: str):
        """从 context 中取值。支持 'context.xxx' 或简单 'xxx'。"""
        k = key.removeprefix("context.").strip()
        return self._context.get(k)

    def _op_exists(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        return val is not None and str(val).strip() != ""

    def _op_not_exists(self, lhs: str, rhs: str) -> bool:
        return not self._op_exists(lhs, rhs)

    def _op_eq(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        expected = rhs.strip("\"'")
        return str(val) == expected

    def _op_ne(self, lhs: str, rhs: str) -> bool:
        return not self._op_eq(lhs, rhs)

    def _op_contains(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        substr = rhs.strip("\"'")
        return substr.lower() in str(val).lower()

    def _op_starts_with(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        prefix = rhs.strip("\"'")
        return str(val).lower().startswith(prefix.lower())

    def _op_ends_with(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        suffix = rhs.strip("\"'")
        return str(val).lower().endswith(suffix.lower())

    def _op_gt(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        try:
            return float(val) > float(rhs)
        except (ValueError, TypeError):
            return False

    def _op_ge(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        try:
            return float(val) >= float(rhs)
        except (ValueError, TypeError):
            return False

    def _op_lt(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        try:
            return float(val) < float(rhs)
        except (ValueError, TypeError):
            return False

    def _op_le(self, lhs: str, rhs: str) -> bool:
        val = self._get_context_value(lhs)
        try:
            return float(val) <= float(rhs)
        except (ValueError, TypeError):
            return False

    def _split_logical(self, expr: str, op: str) -> List[str]:
        """按逻辑操作符分割（跳过括号内的内容）"""
        parts = []
        depth = 0
        current = []
        op_lower = op.lower()
        # 按单词边界匹配操作符
        pattern = re.compile(rf'\b{op_lower}\b', re.IGNORECASE)

        i = 0
        while i < len(expr):
            ch = expr[i]
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif depth == 0:
                # 检查当前位置是否是操作符
                remaining = expr[i:]
                m = pattern.match(remaining)
                if m:
                    parts.append("".join(current).strip())
                    current = []
                    i += m.end()
                    continue
                current.append(ch)
            else:
                current.append(ch)
            i += 1

        last = "".join(current).strip()
        if last:
            parts.append(last)
        return parts

    # ====================== Step Execution ======================
    async def _execute_step(self, step: FlowStep) -> str:
        """执行单个工作流步骤"""
        agent = self._agents.get(step.agent)
        if not agent:
            raise ValueError(f"步骤 '{step.id}' 引用了不存在的 Agent: '{step.agent}'")

        # 解析任务模板（含上下文变量替换）
        task_prompt = self._resolve_placeholder(step.task)

        # 构建上下文（上游输出）
        context = dict(self._context)

        # 执行
        last_error = None
        for attempt in range(max(1, step.retry + 1)):
            if self._cancel_event.is_set():
                return "[CANCELLED]"

            try:
                self._emit(EventType.SYSTEM_INFO, {
                    "message": f"[{step.id}] Agent '{step.agent}' 执行中 (尝试 {attempt + 1})..."
                })

                result = await asyncio.wait_for(
                    agent.process_message(task_prompt, context=context if attempt == 0 else None),
                    timeout=step.timeout
                )
                return result
            except asyncio.TimeoutError:
                last_error = f"超时 ({step.timeout}s)"
                self._emit(EventType.ERROR, {
                    "message": f"[{step.id}] Agent '{step.agent}' {last_error}"
                })
            except Exception as e:
                last_error = str(e)
                self._emit(EventType.ERROR, {
                    "message": f"[{step.id}] Agent '{step.agent}' 执行失败: {e}"
                })
                traceback.print_exc()

        return f"[ERROR] 步骤 '{step.id}' 执行失败: {last_error}"

    async def run(self, initial_context: dict = None) -> Dict[str, Any]:
        """运行完整工作流

        Args:
            initial_context: 初始上下文（例如 {"user_request": "..."}）

        Returns:
            Dict with keys:
                - flow_name: str
                - success: bool
                - outputs: Dict[str, str]  # step_id -> result
                - context: Dict[str, Any]   # final context
                - errors: List[str]
                - skipped: List[str]        # 被条件跳过的 step id
        """
        if not self.definition:
            self.load()

        self._context = initial_context or {}
        outputs = {}
        errors = []
        skipped = []

        try:
            # 1. 创建并启动 Agent
            self._emit(EventType.SYSTEM_INFO, {"message": f"🔄 启动工作流: {self.definition.name}"})
            await self._create_agents()
            await self._start_agents()

            # 2. 按顺序执行步骤
            for step in self.definition.steps:
                if self._cancel_event.is_set():
                    errors.append("工作流被用户中断")
                    break

                # ---- 条件分支判断 ----
                if step.if_condition:
                    condition_pass = self._evaluate_condition(step.if_condition)
                    self._emit(EventType.SYSTEM_INFO, {
                        "message": (
                            f"🔀 步骤 [{step.id}] 条件: `{step.if_condition}` "
                            f"→ {'✅ 通过' if condition_pass else '⏭️ 跳过'}"
                        )
                    })
                    if not condition_pass:
                        outputs[step.id] = "[SKIPPED] 条件未满足"
                        skipped.append(step.id)
                        continue

                self._emit(EventType.SYSTEM_INFO, {
                    "message": f"▶️ 执行步骤 [{step.id}]: Agent '{step.agent}'"
                })

                result = await self._execute_step(step)

                # 保存输出
                outputs[step.id] = result
                if step.output_var:
                    self._context[step.output_var] = result

                # 保存到文件
                output_dir = self._resolve_path(
                    self.definition.output.get("dir", "./flow_output")
                )
                if self.definition.output.get("save_logs", True):
                    os.makedirs(output_dir, exist_ok=True)
                    log_file = os.path.join(output_dir, f"{step.id}.md")
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(f"# Step [{step.id}] @ {datetime.now().isoformat()}\n\n")
                        f.write(f"**Task**: {task_prompt}\n\n")
                        f.write(f"**Result**:\n\n{result}\n\n---\n\n")

                if result.startswith("[ERROR]") or result.startswith("[CANCELLED]"):
                    errors.append(f"步骤 [{step.id}] 失败: {result}")

        except Exception as e:
            errors.append(f"工作流执行异常: {e}")
            traceback.print_exc()
        finally:
            # 3. 清理
            await self._stop_agents()

        # 保存完整结果
        flow_result = {
            "flow_name": self.definition.name,
            "success": len(errors) == 0,
            "outputs": outputs,
            "context": self._context,
            "errors": errors,
            "skipped": skipped,
        }

        output_dir = self._resolve_path(
            self.definition.output.get("dir", "./flow_output")
        )
        os.makedirs(output_dir, exist_ok=True)
        summary_path = os.path.join(output_dir, "_flow_result.json")
        # 过滤非序列化内容
        serializable_context = {}
        for k, v in self._context.items():
            if isinstance(v, (str, int, float, bool, list, dict)):
                serializable_context[k] = v
            else:
                serializable_context[k] = str(v)[:500]
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "flow_name": self.definition.name,
                "success": len(errors) == 0,
                "outputs": {k: str(v)[:1000] for k, v in outputs.items()},
                "context": serializable_context,
                "errors": errors,
                "skipped": skipped,
            }, f, ensure_ascii=False, indent=2)

        self._emit(EventType.SYSTEM_INFO, {
            "message": f"✅ 工作流完成: {'成功' if flow_result['success'] else '失败'} | "
                       f"{len(outputs)} 步骤, {len(errors)} 错误"
        })

        return flow_result


# ====================== Convenience ======================
async def run_flow(flow_path: str, initial_context: dict = None,
                   console_stream: bool = False) -> Dict[str, Any]:
    """快速运行工作流（单函数入口）"""
    runner = AgentFlowRunner(flow_path, console_stream=console_stream)
    runner.load()
    return await runner.run(initial_context=initial_context)


def load_flow(flow_path: str) -> AgentFlowDefinition:
    """仅加载工作流定义（不执行）"""
    runner = AgentFlowRunner(flow_path)
    runner.load()
    return runner.definition


def validate_flow(flow_path: str) -> List[str]:
    """验证工作流 YAML 配置，返回错误列表"""
    errors = []
    try:
        runner = AgentFlowRunner(flow_path)
        runner.load()
        definition = runner.definition

        if not definition.agents:
            errors.append("未定义任何 Agent")

        if not definition.steps:
            errors.append("未定义任何步骤")

        # 确定 LLM/MCP 数据源（YAML 定义优先，否则 configs.json）
        mcp_source = (definition.mcp_servers
                       if definition.mcp_servers
                       else runner._configs_json.get("mcp_servers", {}))

        agent_names = {a.name for a in definition.agents}
        for step in definition.steps:
            if step.agent not in agent_names:
                errors.append(f"步骤 '{step.id}' 引用了未定义的 Agent: '{step.agent}'")

        for a in definition.agents:
            if a.llm_model:
                if a.llm_model not in definition.llm_models:
                    # YAML 无定义，检查 configs.json
                    found = runner._find_llm_from_configs(a.llm_model)
                    if not found:
                        errors.append(
                            f"Agent '{a.name}' 引用的 LLM model '{a.llm_model}' "
                            f"在 YAML 和 configs.json 中均未找到（或全部 disabled）")
            for mcp_key in a.mcp_servers:
                if mcp_key not in mcp_source:
                    errors.append(f"Agent '{a.name}' 引用了未定义的 MCP server: '{mcp_key}'")

    except Exception as e:
        errors.append(f"解析失败: {e}")

    return errors


# Re-export for convenience
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "调用搜索引擎",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询的信息"}
            },
            "required": ["query"]
        }
    }
}
