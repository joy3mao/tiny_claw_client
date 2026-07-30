#!/usr/bin/env python3
# coding:utf-8
"""Tiny Claw TUI - Textual Terminal UI Application (pure Textual, no Rich)"""
import asyncio, json, os, sys, re, collections
from datetime import datetime
from typing import Optional, List
import pyperclip,platform
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Header, Footer, Static, ListView, ListItem, Markdown, TextArea, RichLog
from textual.screen import Screen, ModalScreen
from textual import events


class FastScrollContainer(ScrollableContainer):
    """ScrollableContainer with 5-line mouse wheel scroll speed."""
    SCROLL_LINES = 5

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.scroll_relative(y=self.SCROLL_LINES, animate=False)
        event.stop()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.scroll_relative(y=-self.SCROLL_LINES, animate=False)
        event.stop()


from tiny_claw_core.core import (
    ChatSession, SessionEvent, EventType,
    WORKSPACE_DIR, SKILLS_DIR, FLOWS_DIR, config, base_tools, web_search_tool,
    load_skills_metadata, load_skill_full, load_flows_metadata, Server, Tool,
    image_to_base64, _PROJECT_DIR
)
from tiny_claw_core.lark_bot import LarkBot
from tiny_claw_core.agent_flow import AgentFlowRunner
# `tiny_claw_core.agent` 定义了它*自己*的一份 EventType/SessionEvent（与 core.py 里
# ChatSession 用的那份是两个完全独立的 Enum/dataclass，同名不同身份）。
# AgentFlowRunner/Agent（多 Agent 工作流）emit 的事件用的是这一份，而不是 core.py
# 那份——FlowRunnerScreen 必须用同一份来比较，否则所有 `==` 判断都会静默失败。
from tiny_claw_core.agent import EventType as FlowEventType, SessionEvent as FlowSessionEvent


SESSION_TIMEOUT = 21600  # 对话超时时间（秒），超过6小时后自动重置对话

def _markup_escape(text: str) -> str:
    """Escape Rich markup special characters [ and ] for use in Textual Static/Rich markup strings."""
    return text.replace("[", "\\[").replace("]", "\\]")

# ====================== Command List for Autocomplete ======================
COMMANDS = [
    ("/agent",        "开启Agent模式（调用工具、技能、MCP）"),
    ("/agent-task",   "开启Agent-Task模式（分步规划执行任务）"),
    ("/batch",        "批量任务（需先开启Agent-Task模式，提供任务描述文件路径）"),
    ("/agent-off",    "关闭Agent模式，回到普通Chat"),
    ("/lst",          "列出所有可用工具（Base + MCP + Skills）"),
    ("/snc",          "开始新对话"),
    ("/cls",          "清屏"),
    ("/clh",          "清理输入历史"),
    ("/swm",          "切换AI大模型"),
    ("/img",          "上传图片"),
    ("/compact",      "压缩当前会话并保存到 his_sessions/"),
    ("/load",         "加载已保存的压缩会话"),
    ("/reload",       "重新加载配置文件"),
    ("/stu",          "显示本轮Token使用量"),
    ("/help",         "显示帮助信息"),
    ("/log",          "查看日志文件路径"),
    ("/lark",         "启动/停止飞书Bot连接"),
    ("/exit",         "退出应用程序"),
]



# ====================== Help Screen ======================
class HelpScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        cmd_section = """***帮助信息***(按\\[Esc]关闭此窗口)
---常用命令表---
[bold #5CC9A0]/agent        [/bold #5CC9A0]开启Agent模式（调用工具、技能、MCP）
[bold #5CC9A0]/agent-task   [/bold #5CC9A0]开启Agent-Task模式（分步规划执行任务）
[bold #5CC9A0]/batch  路径   [/bold #5CC9A0]批量任务（需先开启Agent-Task模式，提供任务描述文件路径）
[bold #5CC9A0]/agent-off    [/bold #5CC9A0]关闭Agent模式，回到普通Chat
[bold #5CC9A0]/lst          [/bold #5CC9A0]列出所有可用工具（Base + MCP + Skills）
[bold #5CC9A0]/snc          [/bold #5CC9A0]开始新对话
[bold #5CC9A0]/cls          [/bold #5CC9A0]清理屏幕
[bold #5CC9A0]/clh          [/bold #5CC9A0]清理输入历史
[bold #5CC9A0]/swm          [/bold #5CC9A0]切换AI大模型
[bold #5CC9A0]/img          [/bold #5CC9A0]上传图片
[bold #5CC9A0]/compact      [/bold #5CC9A0]压缩当前会话并保存到 his_sessions/
[bold #5CC9A0]/load         [/bold #5CC9A0]加载已保存的压缩会话
[bold #5CC9A0]/reload       [/bold #5CC9A0]重新加载配置文件
[bold #5CC9A0]/stu          [/bold #5CC9A0]显示本轮Token使用量
[bold #5CC9A0]/log          [/bold #5CC9A0]查看日志文件路径
[bold #5CC9A0]/lark         [/bold #5CC9A0]启动/停止飞书Bot
[bold #5CC9A0]/flow:名称 需求  [/bold #5CC9A0]运行工作流（flows/ 目录下的 AgentFlow）
[bold #5CC9A0]/exit         [/bold #5CC9A0]退出应用程序
---特殊快捷键---
[bold #5CC9A0]ctrl+c[/bold #5CC9A0] 复制文字（终端原生）
"""
        yield ScrollableContainer(Static(cmd_section, id="help-content"))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss()


# ====================== Flow Runner Screen ======================
class FlowRunnerScreen(ModalScreen):
    """Modal screen — RichLog（历史记录）+ 底部 Static（定时器驱动的流式缓冲区）。

    RichLog.write() 每次调用都会产生新的一行，无法在同一行上追加内容；
    如果每个流式 chunk 都直接 write()，大模型输出会被拆成一堆碎片行，
    看起来就像完全没有流式效果。这里改为仿照主聊天窗口的做法：
    把 STREAMING/THINKING 内容累积到缓冲区，由 set_interval 定时器刷新到
    一个独立的 Static 上；chunk 全部到齐（STREAM_DONE）后再整体写入 RichLog。
    """

    BINDINGS = [
        ("escape", "cancel_flow", "中断"),
    ]

    STREAM_TICK_INTERVAL = 0.08

    def __init__(self, flow_path: str, flow_name: str, user_request: str):
        super().__init__()
        self._flow_path = flow_path
        self._flow_name = flow_name
        self._user_request = user_request
        self._runner: Optional[AgentFlowRunner] = None
        self._done = False
        self._log: Optional[RichLog] = None
        self._stream_widget: Optional[Static] = None
        self._streaming_buffer: str = ""
        self._thinking_buffer: str = ""
        self._stream_timer = None
        self._flow_result: Optional[dict] = None
        self._flow_error: Optional[str] = None
        self._error_shown: bool = False

    def compose(self) -> ComposeResult:
        yield RichLog(id="flow-log", markup=True, highlight=False, wrap=True, max_lines=21)
        yield Static("", id="flow-stream")

    def on_mount(self) -> None:
        self._log = self.query_one("#flow-log", RichLog)
        self._stream_widget = self.query_one("#flow-stream", Static)
        self._log.border_title = f"📋 Flow: {self._flow_name}"
        self._log.write("")
        self._log.write(f"[bold #7C8CF8]🚀 工作流: {self._flow_name}[/bold #7C8CF8]")
        self._log.write(f"[#707090]需求: {self._user_request[:100]}[/#707090]")
        self._log.write("")
        self._log.write("  ⏳ 正在启动工作流...")
        self._stream_timer = self.set_interval(self.STREAM_TICK_INTERVAL, self._flow_stream_tick)
        asyncio.create_task(self._run_flow())

    # ======================== 流式缓冲区渲染 ========================

    def _flow_stream_tick(self) -> None:
        """定时器回调：把累积的流式内容渲染到独立 Static 上，避免逐 chunk 换行。"""
        if not self._stream_widget:
            return
        if not self._streaming_buffer and not self._thinking_buffer:
            self._stream_widget.update("")
            return
        if self._thinking_buffer.strip() and not self._streaming_buffer.strip():
            lines = self._thinking_buffer.splitlines()
            show = ("•••  \n" if len(lines) > 8 else "") + "\n".join(lines[-8:])
            self._stream_widget.update(f"[#B0B0D0]💭 {_markup_escape(show)}[/#B0B0D0]")
        else:
            lines = self._streaming_buffer.splitlines()
            show = ("•••  \n" if len(lines) > 8 else "") + "\n".join(lines[-8:])
            self._stream_widget.update(f"[#D0D0E0]🏭 {_markup_escape(show)}[/#D0D0E0]")

    def _flush_stream_buffer(self) -> None:
        """把已累积的流式内容整体写入 RichLog（一次写入，保留内部换行），并清空缓冲区。"""
        if self._streaming_buffer.strip() and self._log:
            self._log.write(f"  {_markup_escape(self._streaming_buffer.strip())}", scroll_end=True)
            self._log.refresh()
        self._streaming_buffer = ""
        self._thinking_buffer = ""
        if self._stream_widget:
            self._stream_widget.update("")

    # ======================== 事件回调 ========================

    def _on_flow_event(self, event: FlowSessionEvent):
        """AgentFlowRunner 回调 — 从 DOM 取 RichLog 写入。

        注意：这里必须用 FlowEventType（tiny_claw_core.agent.EventType）比较，
        不能用 core.py 的 EventType——两者是同名但不同身份的独立 Enum 类，
        AgentFlowRunner/Agent 发出的事件用的是 agent.py 那一份。之前误用了
        core.py 的 EventType，导致下面每一个 `==` 判断永远为 False，事件
        虽然被正确回调，却没有任何一个分支被执行，界面自然什么都不刷新。

        另外，RichLog.write() 本身不会主动调用 self.refresh()——它只在
        scroll_end() 真正改变了 scroll_y 时才附带触发重绘（watch_scroll_y）。
        只要内容还没撑满可视区域，scroll_y 就不会变化，写入就不会反映到
        屏幕上，所以这里显式调用 rl.refresh()，对齐 Static.update() 的行为
        （它内部就是 update() 后接 self.refresh()）。
        """
        try:
            et = event.type
            d = event.data or {}
            rl = self.query_one("#flow-log", RichLog)
            if et == FlowEventType.SYSTEM_INFO:
                msg = d.get("message", "")
                if msg:
                    rl.write(f"  {msg}", scroll_end=True)
                    rl.refresh()
            elif et == FlowEventType.TOOL_CALL:
                name = d.get("tool_name", "?")
                args = d.get("arguments", {})
                s = json.dumps(args, ensure_ascii=False)
                rl.write(f"[#C08030]  🔧 {name}({s[:80]})[/#C08030]", scroll_end=True)
                rl.refresh()
            elif et == FlowEventType.TOOL_RESULT:
                icon = "✅" if d.get("status") == "success" else "❌"
                result = str(d.get("result", ""))[:200]
                rl.write(f"  {icon} 工具结果: {result}", scroll_end=True)
                rl.refresh()
            elif et == FlowEventType.STREAMING:
                # 累积到缓冲区，由定时器刷新，避免每个 chunk 都单独占一行
                self._streaming_buffer += d.get("content", "")
            elif et == FlowEventType.THINKING:
                # THINKING 事件携带的已经是完整快照，直接替换而非追加
                self._thinking_buffer = d.get("content", "")
            elif et == FlowEventType.ERROR:
                msg = d.get("message", "")
                self._flush_stream_buffer()
                rl.write(f"[bold #F06060]  ❌ {msg}[/bold #F06060]", scroll_end=True)
                rl.refresh()
            elif et == FlowEventType.STREAM_DONE:
                self._flush_stream_buffer()
                rl.write("  ── ✅ ──", scroll_end=True)
                rl.refresh()
        except Exception:
            pass

    # ======================== 工作流执行 ========================

    async def _run_flow(self):
        """后台执行 AgentFlow"""
        try:
            self._log.write("  [bold #5CC9A0]▶ 启动引擎[/bold #5CC9A0]")
            self._log.refresh()

            self._runner = AgentFlowRunner(
                self._flow_path,
                event_callback=self._on_flow_event,
            )
            self._runner.load()

            self._log.write("  [bold #5CC9A0]▶ 开始执行[/bold #5CC9A0]")
            self._log.refresh()

            result = await self._runner.run(
                initial_context={"user_request": self._user_request}
            )
            self._flow_result = result
        except Exception as e:
            self._flow_error = str(e)
            import traceback
            self._log.write(f"[bold #F06060]❌ 异常: {e}[/bold #F06060]")
            self._log.write(f"[dim]{traceback.format_exc()[:500]}[/dim]")
            self._log.refresh()
        finally:
            self._flush_stream_buffer()
            if self._stream_timer:
                self._stream_timer.stop()
                self._stream_timer = None
            self._log.write("")
            self._done = True
            if self._flow_error and not self._error_shown:
                self._error_shown = True
                self._log.write(f"[bold #F06060]❌ 工作流异常: {self._flow_error}[/bold #F06060]")
            elif self._flow_result:
                r = self._flow_result
                if r["success"]:
                    self._log.write("[bold #5CC9A0]✅ 工作流执行成功！[/bold #5CC9A0]")
                    self._log.write(f"  完成 {len(r['outputs'])} 个步骤")
                else:
                    self._log.write("[bold #F06060]❌ 工作流执行失败[/bold #F06060]")
                    for e in r.get("errors", []):
                        self._log.write(f"  ⚠️  {e}")
                outputs = r.get("outputs", {})
                if outputs:
                    self._log.write("")
                    self._log.write("[bold #7C8CF8]📝 各步骤输出预览:[/bold #7C8CF8]")
                    for step_id, out in outputs.items():
                        preview = str(out)[:200].replace("\n", " ")
                        self._log.write(f"  [{step_id}] {preview}...")
            self._log.write("[#707090]按 Esc 关闭[/#707090]")
            self._log.refresh()

    # ======================== 关闭 / 取消 ========================

    def action_cancel_flow(self):
        if self._done:
            self.dismiss()
        elif self._runner:
            self._runner.cancel()
            self._log.write("[bold #E0A040]⏹ 正在中断工作流...[/bold #E0A040]")
            self._log.refresh()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            if self._done:
                self.dismiss()
            elif self._runner:
                self._runner.cancel()
                self._log.write("[bold #E0A040]⏹ 正在中断工作流... (再按 Esc 关闭)[/bold #E0A040]")
                self._log.refresh()
                self._done = True



# ====================== Model Select Screen ======================
class ModelSelectScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        models = list(self.app.chat_session.client_models.items())
        items = []
        for no, model in models:
            status = ""
            if self.app.chat_session.llm_client:
                if (model["ai_channel"] == self.app.chat_session.llm_client.ai_channel
                        and model["ai_model"] == self.app.chat_session.llm_client.ai_model):
                    status = " ◀ 当前"
            label = f"[bold #5CC9A0]{no}[/bold #5CC9A0]. {model['ai_channel']} | {model['ai_model']} | {model['ai_provider']}{status}"
            items.append(ListItem(Static(label)))
        yield Static("[bold #7C8CF8]选择AI大模型:[/bold #7C8CF8] (数字键选择)")
        yield ListView(*items, id="model-list")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        models = list(self.app.chat_session.client_models.items())
        if 0 <= idx < len(models):
            self._select_model(models[idx][0])

    def _select_model(self, model_no: str):
        if self.app.chat_session.switch_model(model_no):
            info = self.app.chat_session.client_models[model_no]
            self.app.notify(f"已切换到 {info['ai_model']}", title="模型切换", severity="information")
            # Notify ChatScreen to update header immediately
            # screen_stack: [ChatScreen, ModelSelectScreen], top is last
            if len(self.app.screen_stack) >= 2:
                chat_screen = self.app.screen_stack[-2]
                if hasattr(chat_screen, '_update_header'):
                    chat_screen._update_header()
        self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()
        elif event.key.isdigit():
            model_no = event.key
            if model_no in self.app.chat_session.client_models:
                self._select_model(model_no)


# ====================== List Tools Screen ======================
class ListToolsScreen(ModalScreen):
    """Modal screen showing all available tools."""

    def __init__(self, content: str):
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(Static(self._content, id="lst-content"))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss()


# ====================== Dangerous Command Confirm Screen ======================
class DangerousCommandScreen(ModalScreen):
    """Confirmation screen for potentially dangerous commands."""

    BINDINGS = [
        ("y", "confirm", "确认执行"),
        ("enter", "confirm", "确认执行"),
        ("n", "cancel_cmd", "取消"),
        ("escape", "cancel_cmd", "取消"),
    ]

    def __init__(self, command: str, patterns: list):
        super().__init__()
        self._command = command
        self._patterns = patterns

    def compose(self) -> ComposeResult:
        patterns_list = "、".join(
            f"[bold #E0A040]`{_markup_escape(p)}`[/]"
            for p in self._patterns
        )
        # Truncate command display to avoid huge walls of text
        cmd_display = self._command
        if len(cmd_display) > 200:
            cmd_display = cmd_display[:200] + "…"

        content = f"""[bold #F06060]⚠ 检测到潜在危险命令[/]

[bold #D0D0E0]命令:[/] [#F06060]{_markup_escape(cmd_display)}[/]

[bold #D0D0E0]危险模式:[/] {patterns_list}

[bold #F0A060]确定要继续执行？[/]

[bold #5CC9A0][Y/Enter][/] 确认    [bold #F06060][N/Esc][/] 取消"""
        yield ScrollableContainer(Static(content, id="danger-content"))

    def action_confirm(self):
        self.app.chat_session.confirm_dangerous_command(True)
        self.dismiss()

    def action_cancel_cmd(self):
        self.app.chat_session.confirm_dangerous_command(False)
        self.dismiss()

# ====================== User Choice Screen ======================
class UserChoiceScreen(ModalScreen):
    """Modal screen for LLM to ask user to pick from a list of choices.
    Arrow keys navigate, Enter confirms, Esc cancels."""

    BINDINGS = [
        ("up", "cursor_up", "上移"),
        ("down", "cursor_down", "下移"),
        ("enter", "select", "确认选择"),
        ("escape", "cancel_choice", "取消"),
    ]

    def __init__(self, choices: list):
        super().__init__()
        self._choices = list(choices) + ["# 都不选择（跳过）"]
        self._selected_idx = 0

    def compose(self) -> ComposeResult:
        lines = ["[bold #7C8CF8]🤔 请选择一个选项：[/bold #7C8CF8]\n"]
        for i, opt in enumerate(self._choices):
            if i == self._selected_idx:
                lines.append(f"[bold #5CC9A0]▶ {_markup_escape(opt)}[/bold #5CC9A0]")
            else:
                lines.append(f"  [#A0A0C0]{_markup_escape(opt)}[/#A0A0C0]")
        lines.append("\n[#707090]↑↓ 导航  Enter 确认  Esc 取消[/#707090]")
        content = "\n".join(lines)
        yield FastScrollContainer(Static(content, id="choice-content"))

    def _render_options(self):
        content_widget = self.query_one("#choice-content", Static)
        lines = ["[bold #7C8CF8]🤔 请选择一个选项：[/bold #7C8CF8]\n"]
        for i, opt in enumerate(self._choices):
            if i == self._selected_idx:
                lines.append(f"[bold #5CC9A0]▶ {_markup_escape(opt)}[/bold #5CC9A0]")
            else:
                lines.append(f"  [#A0A0C0]{_markup_escape(opt)}[/#A0A0C0]")
        lines.append("\n[#707090]↑↓ 导航  Enter 确认  Esc 取消[/#707090]")
        content_widget.update("\n".join(lines))

    def action_cursor_up(self):
        if self._selected_idx > 0:
            self._selected_idx -= 1
            self._render_options()

    def action_cursor_down(self):
        if self._selected_idx < len(self._choices) - 1:
            self._selected_idx += 1
            self._render_options()

    def action_select(self):
        self.app.chat_session.answer_user_choice(self._choices[self._selected_idx])
        self.dismiss()

    def action_cancel_choice(self):
        self.app.chat_session.answer_user_choice(None)
        self.dismiss()

# ====================== Session Load Screen ======================
class SessionLoadScreen(ModalScreen):
    """Modal screen showing available compressed session files to load."""

    BINDINGS = [
        ("up", "cursor_up", "上移"),
        ("down", "cursor_down", "下移"),
        ("enter", "select", "确认选择"),
        ("escape", "cancel_load", "取消"),
    ]

    def __init__(self):
        super().__init__()
        self._files: list[str] = []
        self._selected_idx = 0

    def compose(self) -> ComposeResult:
        yield FastScrollContainer(Static("", id="session-load-content"))

    def on_mount(self) -> None:
        self._files = self.app.chat_session._list_session_files()
        if not self._files:
            self.query_one("#session-load-content", Static).update(
                "[bold #F06060]❌ his_sessions/ 中没有已保存的会话文件[/]\n\n[#707090]按 Esc 关闭[/#707090]"
            )
            return
        self._render_options()

    def _render_options(self):
        content_widget = self.query_one("#session-load-content", Static)
        lines = ["[bold #7C8CF8]📂 已保存的会话文件 — 选择要加载的会话:[/bold #7C8CF8]\n"]
        for i, fname in enumerate(self._files):
            if i == self._selected_idx:
                lines.append(f"[bold #5CC9A0]▶ {_markup_escape(fname)}[/bold #5CC9A0]")
            else:
                lines.append(f"  [#A0A0C0]{_markup_escape(fname)}[/#A0A0C0]")
        lines.append("\n[#707090]↑↓ 导航  Enter 确认  Esc 取消[/#707090]")
        content_widget.update("\n".join(lines))

    def action_cursor_up(self):
        if self._selected_idx > 0:
            self._selected_idx -= 1
            self._render_options()

    def action_cursor_down(self):
        if self._selected_idx < len(self._files) - 1:
            self._selected_idx += 1
            self._render_options()

    def action_select(self):
        if not self._files:
            return
        fname = self._files[self._selected_idx]
        his_dir = os.path.join(WORKSPACE_DIR, "his_sessions")
        filepath = os.path.join(his_dir, fname)
        self.dismiss()
        # 异步加载
        asyncio.create_task(self._do_load(filepath, fname))

    async def _do_load(self, filepath: str, filename: str):
        chat_screen = self.app.screen_stack[-1]  # ChatScreen
        if hasattr(chat_screen, '_msg_info'):
            chat_screen._msg_info(f"📂 正在加载 {filename} ...")
        result = await self.app.chat_session._load_session_file(filepath, filename)
        if result:
            msg = f"✅ 已加载会话: [bold]{_markup_escape(result)}[/bold]"
            if hasattr(chat_screen, '_msg_info'):
                chat_screen._msg_info(msg)
            self.app.notify(f"已加载: {result}", title="加载会话", severity="information")
        else:
            msg = "⚠️ 当前会话已经加载过了/加载会话报错"
            if hasattr(chat_screen, '_msg_info'):
                chat_screen._msg_info(msg)
            self.app.notify("加载会话失败", severity="error")

    def action_cancel_load(self):
        self.dismiss()


# ====================== Main Chat Screen ======================

class ChatTextArea(TextArea):
    """TextArea: Enter submits, Ctrl+J inserts newline, Up/Down navigates suggestions.
    
    Ctrl+C: if TextArea has selected text → copies to clipboard.
    Otherwise → no-op (terminal-native copy is blocked by Textual framework).
    """

    def _on_mouse_scroll(self, event: events.MouseScrollDown | events.MouseScrollUp) -> None:
        """Do NOT consume mouse scroll events — let them bubble to parent ScrollableContainers.
        The TextArea has max-height: 6 + overflow: hidden, so it never needs internal scrolling.
        This prevents the focused TextArea from intercepting mouse wheel events that should
        go to #chat-log or modal ScrollableContainers."""
        pass

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            # Let TextArea's default handler process ESC first
            # (clears text selection, resets mouse tracking state, etc.)
            # Screen binding ("escape", "cancel") will also fire action_cancel
            await super()._on_key(event)
            return
        # Ctrl+C: only intercept when TextArea has selected text.
        # When no selection → just return (don't call super, don't stop event),
        # so the event bubbles through to the terminal for native mouse-selection copy.
        if event.key == "ctrl+c":
            if self.selected_text:
                event.stop()
                event.prevent_default()
                pyperclip.copy(self.selected_text)
                return
            # No TextArea selection → let terminal handle it
            return
        # When processing, block all other keys
        if self.screen._processing:
            event.stop()
            event.prevent_default()
            return
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "up" or event.key == "down":
            if self.screen._cmd_matches:
                event.stop()
                event.prevent_default()
                self.screen._navigate_suggestion(1 if event.key == "down" else -1)
                return
            # Navigate input history
            event.stop()
            event.prevent_default()
            self.screen._navigate_history(1 if event.key == "down" else -1)
            return
        # Paste from system clipboard (Cmd+V)
        if event.key == "super+v":
            event.stop()
            event.prevent_default()
            try:
                text = pyperclip.paste()
                if text:
                    self.insert(text)
            except Exception:
                pass
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.screen.action_submit_message()
            return
        await super()._on_key(event)


class ChatScreen(Screen):
    """Main chat screen with message log and input."""

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("ctrl+l", "clear_screen", "清屏"),
        ("f1", "show_help", "帮助"),
        ("tab", "autocomplete(False)", "补全"),
        ("enter", "submit_message", "发送"),
        ("ctrl+j", "newline", "换行"),
        ("escape", "cancel", "中断"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield FastScrollContainer(id="chat-log")
        yield ListView(id="cmd-suggestions")
        yield Static(id="task-panel")
        yield ChatTextArea("", id="chat-input", classes="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        self._streaming_buffer = ""
        self._thinking_buffer = ""
        self._stream_widget: Optional[Static] = None
        self._stream_timer = self.set_interval(0.08, self._stream_tick)
        self._cmd_matches: list = []
        self._cmd_select_idx: int = 0
        self._suppress_autocomplete: bool = False
        self._processing: bool = False
        self._input_history: list[str] = []
        self._history_idx: Optional[int] = None
        self._history_draft: str = ""
        self._suppress_history_reset: bool = False
        self._collecting_images: bool = False

        self.chat_session.creat_new_log()
        self.chat_session._event_callback = self._on_session_event

        if self.chat_session.client_models:
            self.chat_session.switch_model("1")

        self._render_title()

        self.chat_session.messages = [
            {"role": "system", "content": self.chat_session.gen_chat_system_content()}
        ]

        self.query_one("#cmd-suggestions").display = False
        self.query_one("#cmd-suggestions").can_focus = False
        self.query_one("#task-panel").display = False
        self.query_one("#task-panel").can_focus = False
        self.query_one("#chat-input").focus()

    @property
    def chat_session(self) -> ChatSession:
        return self.app.chat_session

    def _add_msg(self, text: str, classes: str = "", label: str = ""):
        log = self.query_one("#chat-log")
        w = Static(text, classes=classes)
        if label:
            w.border_title = label
        log.mount(w)
        w.scroll_visible()

    def _render_title(self):
        title = """[bold #7C8CF8]
  ┏┳┓•      ┏┓┓       ┏┓┓•     
   ┃ ┓┏┓┓┏  ┃ ┃┏┓┓┏┏  ┃ ┃┓┏┓┏┓╋
   ┻ ┗┛┗┗┫  ┗┛┗┗┻┗┻┛  ┗┛┗┗┗ ┛┗┗
         ┛                     
[/bold #7C8CF8]"""
        self._add_msg(title, "msg-title")

        welcome = (
            "[bold #5CC9A0]Tiny Claw TUI[/bold #5CC9A0] — 终端AI助手\n\n"
            "[#A0A0C0]/agent 开启Agent | /lark 飞书Bot | /help 查看帮助 | Ctrl+Q 退出 | Ctrl+C 复制[/#A0A0C0]"
        )
        self._add_msg(welcome, "msg-welcome")

    def _msg_user(self, text: str):
        self._add_msg(f"[#D0D0E0]{_markup_escape(text)}[/#D0D0E0]", "msg-user", "🧑 用户")

    def _msg_assistant(self, text: str):
        log = self.query_one("#chat-log")
        body = Markdown(text, classes="msg-assistant")
        body.border_title = "🤖 助手"
        log.mount(body)
        body.scroll_visible()

    def _msg_tool_call(self, tool_name: str, args: dict):
        args_str = json.dumps(args, ensure_ascii=False, indent=2)
        self._add_msg(f"[#C0C0D0]{_markup_escape(args_str)}[/#C0C0D0]", "msg-tool", f"🔧 {tool_name}")

    def _msg_tool_result(self, tool_name: str, result: str, status: str, elapsed: str):
        emoji = "✅" if status == "success" else "❌"
        log = self.query_one("#chat-log")
        body = Markdown(result, classes="msg-tool-result")
        body.border_title = f"{emoji} {tool_name} ({elapsed}s)"
        log.mount(body)
        body.scroll_visible()

    def _msg_info(self, text: str):
        self._add_msg(f"[#D0D0E0]{_markup_escape(text)}[/#D0D0E0]", "msg-info", "ℹ️ 信息")

    def _msg_error(self, text: str):
        self._add_msg(f"[bold #F06060]{_markup_escape(text)}[/bold #F06060]", "msg-error", "❌ 错误")

    def _msg_list(self, text: str):
        self._add_msg(f"[#D0D0E0]{_markup_escape(text)}[/#D0D0E0]", "msg-list", "📋 列表")

    def _clear_chat(self):
        self.query_one("#chat-log").remove_children()
        self._stream_widget = None
        self._streaming_buffer = ""
        self._thinking_buffer = ""

    # ==================== Streaming (timer-driven flush) ====================
    def _stream_tick(self):
        """Called by set_interval(0.08s) to render buffered stream content."""
        if not self._streaming_buffer and not self._thinking_buffer:
            return

        log = self.query_one("#chat-log")

        if self._stream_widget is None:
            self._stream_widget = Static("", classes="msg-stream")
            log.mount(self._stream_widget)

        if self._thinking_buffer.strip() and not self._streaming_buffer.strip():
            buf = self._thinking_buffer
            lines = buf.splitlines()
            show = ("•••  \n" if len(lines) > 10 else "") + "\n".join(lines[-10:])
            self._stream_widget.update(f"[#B0B0D0]{_markup_escape(show)}[/#B0B0D0]")
            self._stream_widget.border_title = "🧠 思考中..."
        else:
            buf = self._streaming_buffer
            lines = buf.splitlines()
            show = ("•••  \n" if len(lines) > 10 else "") + "\n".join(lines[-10:])
            self._stream_widget.update(f"[#D0D0E0]{_markup_escape(show)}[/#D0D0E0]")
            self._stream_widget.border_title = "🏭 生成中..."

        # Only auto-scroll if user is already at/near bottom — don't steal scroll position
        if log.scroll_y >= log.max_scroll_y - 1:
            self._stream_widget.scroll_visible()

    def _finalize_stream(self, reasoning_content: str = ""):
        """Convert live stream widget into final Markdown-rendered assistant message."""
        if self._stream_widget is None:
            return

        content = self._streaming_buffer

        self._stream_widget.remove()
        self._stream_widget = None
        self._streaming_buffer = ""
        self._thinking_buffer = ""

        if content.strip():
            if reasoning_content:
                final = f"{content}\n\n---\n*思考过程*: {reasoning_content}"
            else:
                final = content
            self._msg_assistant(final)

    # ==================== Event Callback (from ChatSession) ====================
    def _on_session_event(self, event: SessionEvent):
        """Buffer events only — UI updates happen via _stream_tick timer."""
        if event.type == EventType.STREAMING:
            chunk = event.data.get("content", "")
            if chunk:
                self._streaming_buffer += chunk
        elif event.type == EventType.THINKING:
            chunk = event.data.get("content", "")
            if chunk:
                self._thinking_buffer = chunk  # replace, not append — core already sends full snapshot
        elif event.type == EventType.STREAM_DONE:
            had_stream = bool(self._streaming_buffer or self._thinking_buffer)
            reasoning = event.data.get("reasoning_content", "")
            self._finalize_stream(reasoning_content=reasoning)
            content = event.data.get("content", "")
            tool_calls = event.data.get("tool_calls", [])
            # If _finalize_stream didn't render (non-streaming / agent-mode blocking call),
            # render the response from event data now
            if content.strip() and not had_stream:
                if reasoning:
                    content = f"{content}\n\n---\n*思考过程*: {reasoning}"
                self._msg_assistant(content)
            elif tool_calls and not content.strip():
                func_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                self._msg_info(f"🔔 调用工具: {', '.join(func_names)} ...")
            self._update_header()
        elif event.type == EventType.TOOL_CALL:
            self._msg_tool_call(
                event.data.get("tool_name", "?"),
                event.data.get("arguments", {})
            )
        elif event.type == EventType.TOOL_RESULT:
            self._msg_tool_result(
                event.data.get("tool_name", "?"),
                event.data.get("result", ""),
                event.data.get("status", ""),
                event.data.get("elapsed", "")
            )
        elif event.type == EventType.ERROR:
            self._msg_error(event.data.get("message", ""))
        elif event.type == EventType.AGENT_LOOP_DONE:
            self._update_header()
        elif event.type == EventType.TASK_STEPS_UPDATED:
            self._update_task_panel()
        elif event.type == EventType.DANGEROUS_COMMAND:
            command = event.data.get("command", "")
            patterns = event.data.get("patterns", [])
            self.app.push_screen(DangerousCommandScreen(command, patterns))
        elif event.type == EventType.USER_CHOICE:
            choices = event.data.get("choices", [])
            self.app.push_screen(UserChoiceScreen(choices))

    def _update_header(self):
        info = self.chat_session.get_model_info_text()
        usage = self.chat_session.usage
        mcp_count = len([s for s in self.chat_session.servers if s not in self.chat_session.invalid_servers])
        skill_count = len(self.chat_session.skills_meta)
        lark_status = "🟢 Lark" if self.app.lark_bot.is_running else "⚫ Lark"
        self.app.title = f"{info} | MCP:{mcp_count} | SKILL:{skill_count} | Tokens: {usage['total_tokens']} | {lark_status}"

    def _update_task_panel(self):
        """Update the task progress panel (agent-task mode)."""
        panel = self.query_one("#task-panel", Static)

        if self.chat_session.agent_switch != 2:
            panel.display = False
            return

        steps = self.chat_session.task_steps
        if not steps:
            panel.border_title = "📋 任务进度"
            panel.update("  ⏳ 等待模型规划任务步骤...")
            panel.display = True
            return

        total = len(steps)
        done = sum(1 for s in steps if s.get("status") == "done")
        failed = sum(1 for s in steps if s.get("status") == "failed")
        ongoing = sum(1 for s in steps if s.get("status") == "ongoing")

        panel.border_title = f"📋 任务进度 ({done}/{total})"

        status_icons = {"pending": "📋", "ongoing": "🔄", "done": "✅", "failed": "❌"}
        lines = []
        for step in steps:
            icon = status_icons.get(step.get("status", "pending"), "📋")
            no = step.get("step_no", "?")
            name = step.get("step_name", f"步骤{no}")
            lines.append(f"  {icon} {no}. {name}")
        if ongoing:
            ongoing_nos = [s['step_no'] for s in steps if s.get('status') == 'ongoing']
            lines.append(f"\n  🔄 正在执行步骤 {ongoing_nos[0]}...")
        if done + failed >= total:
            lines.append(f"\n  🎉 全部 {total} 步已完成！")

        panel.update("\n".join(lines))
        panel.display = True

    # ==================== Input Handling ====================

    def action_submit_message(self) -> None:
        """Enter: submit TextArea content, or autocomplete suggestion if active."""
        ta = self.query_one("#chat-input", TextArea)

        # If suggestions visible with a selection, autocomplete AND submit
        if self._cmd_matches:
            idx = self._cmd_select_idx % len(self._cmd_matches)
            cmd = self._cmd_matches[idx][0]
            self._cmd_matches = []
            self.query_one("#cmd-suggestions").display = False
            self._suppress_autocomplete = True
            ta.text = cmd
            ta.cursor_location = (0, len(ta.text))
            # Fall through to submit

        user_input = ta.text.strip()
        ta.text = ""

        if not user_input:
            return

        # Save to input history (skip consecutive duplicates, skip when collecting images)
        if not self._collecting_images:
            if not self._input_history or self._input_history[-1] != user_input:
                self._input_history.append(user_input)
        self._history_idx = None
        self._history_draft = ""

        if user_input.startswith("/"):
            asyncio.create_task(self._run_command(user_input))
            self.query_one("#cmd-suggestions").display = False
            return

        self.query_one("#cmd-suggestions").display = False
        asyncio.create_task(self._process_message(user_input))

    async def _run_command(self, user_input: str):
        handled = await self._handle_command(user_input)
        if not handled:
            await self._process_message(user_input)
        self.query_one("#chat-input", TextArea).focus()

    async def _handle_command(self, user_input: str) -> bool:
        """Handle a command. Returns True if command was handled (no AI processing needed)."""
        parts = user_input.split(" ", 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # When collecting images, don't treat /-prefixed paths as commands
        if self._collecting_images:
            return False

        if cmd == "/exit":
            await self._do_exit()
            return True

        if cmd == "/help":
            self.app.push_screen(HelpScreen())
            return True

        if cmd == "/cls":
            self._clear_chat()
            self._render_title()
            return True

        if cmd == "/clh":
            self._input_history.clear()
            self._history_idx = None
            self._history_draft = ""
            self.app.notify("已清理输入历史", title="历史", severity="information")
            return True

        if cmd == "/snc":
            await self.chat_session.start_new_chat()
            self._chat_start_time = datetime.now()
            self._update_header()
            self.app.notify("已开始新对话", title="对话", severity="information")
            return True

        if cmd in ("/agent", "/agent-task"):
            switch_mode = 2 if cmd == "/agent-task" else 1
            if self.chat_session.agent_switch == switch_mode:
                self.app.notify(f"已经是{'AgentTask' if switch_mode == 2 else 'Agent'}模式了", severity="warning")
                return True
            self.chat_session.task_steps = []
            try:
                await self.chat_session.enable_agent(switch_mode)
                self.app.notify(f"✅ {'AgentTask' if switch_mode == 2 else 'Agent'}模式已开启", title="Agent模式", severity="information")
            except BaseException as e:
                self.app.notify(f"开启模式失败: {str(e)}", title="Agent模式", severity="error")
            # Show task panel only in agent-task mode
            if switch_mode == 2:
                panel = self.query_one("#task-panel", Static)
                panel.border_title = "📋 任务进度"
                panel.update("  ⏳ 等待模型规划任务步骤...")
                panel.display = True
            else:
                self.query_one("#task-panel").display = False
            self._update_header()
            return True

        if cmd == "/agent-off":
            if self.chat_session.agent_switch == 0:
                self.app.notify("已经是普通对话模式", severity="warning")
                return True
            await self.chat_session.disable_agent()
            self.app.notify("Agent模式已关闭，回到普通对话模式", title="Agent模式", severity="information")
            self.query_one("#task-panel").display = False
            self._update_header()
            return True

        if cmd == "/batch":
            if self.chat_session.agent_switch != 2:
                self.app.notify("仅AgentTask模式可以使用此命令", severity="error")
                return True
            cmd_slice = [x.strip() for x in user_input.split(" ", maxsplit=1)]
            if len(cmd_slice) < 2:
                self.app.notify("命令不完整，请提供任务描述文件路径", severity="error")
                return True
            task_desc_path = cmd_slice[1]
            task_desc = self.chat_session.read_file(task_desc_path)
            if task_desc.startswith("[ERROR]"):
                self.app.notify(f"读取任务描述文件失败: {task_desc}", severity="error")
                return True
            # 重置对话，开启批量模式（process_user_message 会自动添加 system message）
            await self.chat_session.start_new_chat()
            self.chat_session.agent_task_batch_switch = 1
            # 构造批量指令消息
            full_prompt = f"""我有批量任务需你严格按要求完成（任务描述在之后提供），请严格按如下进行：  
# 前置任务（此任务**不要使用**"Agent任务模式"）  
概述：在工作目录下需要有"以整体任务名称命名"的任务文件夹，里面需要存放"批量任务清单.txt"(一个任务一行)(必要文件)、各个"单个任务执行结果"(任务x结果.md、任务y结果.md...)、"共性任务执行总结.md"。
1. 先判断工作目录下相关任务文件夹及必要文件已经存在？  
2-1. 不存在或缺失：  
    1. 生成"批量任务清单"，将其写入任务文件夹中。   
    2. 仔细分析任务描述并拆分出各项任务，判断各项任务之间是"**输入参数**不同->**执行过程**一致->**输出格式**一致"的共性任务，还是其他类型。  
        - 共性任务：提炼出"输出部分"、"执行步骤部分"、"输出部分"，"执行步骤部分"中拆分出具体的操作流程；将这些生成「任务规划」模版供对应共性任务使用。  
        - 其他类型：按各自任务要求生成「任务规划」。  
2-2. 已经存在：    
    读取"批量任务清单"、"共性任务执行总结"（如果存在），开始后续任务    
3. 以上结束后，调用工具`pre_work_done`（标记前置工作完成），继续下面的工作。    
  
# 批量任务依次执行（每个任务独立使用"Agent任务模式"，各自构建构建自己的「任务规则」）  
按"批量任务清单"依次执行（注：已完成任务在对话历史中只保留结果），每个任务执行完成后，需要做如下处理：    
    - 将各个任务执行结果归档到整体任务文件夹中  
    - 如果是共性任务，第1个任务执行完成后，总结过程中正确步骤及需要避免的项目到"共性任务执行总结.md"，后续任务执行请参考该文件  
    - 必做项：更新任务文件夹中"批量任务清单"中对应任务的完成状态，如"任务3. 判断用户张三的信息。 -- 已完成"  
        - 不知任务完成进度，请读取"批量任务清单"进行了解  
    - 必做项：总结任务结果后，调用工具`current_task_done`(标记当前任务已完成)  
  
--- 【任务描述-开始】 ---    
{task_desc}
--- 【任务描述-结束】 ---    
"""
            self._msg_info("🚀 批量任务已启动，开始处理...")
            self._msg_user(full_prompt[:240] + ("..." if len(full_prompt) > 240 else ""))
            # process_user_message 会自动将 full_prompt 追加到 messages
            asyncio.create_task(self._run_user_message(full_prompt, self.query_one("#chat-input")))
            self._chat_start_time = datetime.now()
            return True

        if cmd == "/swm":
            self.app.push_screen(ModelSelectScreen())
            return True

        if cmd == "/lst":
            if self.chat_session.agent_switch == 0:
                self.app.notify("需要先开启Agent模式", severity="warning")
                return True
            base_names = [t["function"]["name"] for t in base_tools]
            lines = ["[bold #5CC9A0]▸ 基本工具:[/bold #5CC9A0]"]
            for name in base_names:
                lines.append(f"    + [bold #E0A040]{_markup_escape(name)}[/bold #E0A040]")
            lines.append("")
            lines.append("[bold #5CC9A0]▸ MCP工具:[/bold #5CC9A0]")
            # Group MCP tools by server name
            server_groups = []
            for server in self.chat_session.servers:
                if server in self.chat_session.invalid_servers:
                    continue
                try:
                    tools = await server.list_tools()
                    if tools:
                        server_groups.append((server.name, tools, None))
                    else:
                        server_groups.append((server.name, [], "无工具"))
                except:
                    server_groups.append((server.name, [], "加载失败"))
            for srv_name, tools, err in server_groups:
                if err:
                    lines.append(f"  [bold #7C8CF8]▹ {_markup_escape(srv_name)}[/bold #7C8CF8] — {_markup_escape(err)}")
                else:
                    lines.append(f"  [bold #7C8CF8]▹ {_markup_escape(srv_name)}[/bold #7C8CF8]")
                    for tool in tools:
                        lines.append(f"      + [bold #E0A040]{_markup_escape(tool.name)}[/bold #E0A040]: {_markup_escape(tool.description[:60])}")
            lines.append("")
            lines.append("[bold #5CC9A0]▸ 技能:[/bold #5CC9A0]")
            for skill in self.chat_session.skills_meta:
                lines.append(f"    + [bold #E0A040]{_markup_escape(skill['name'])}[/bold #E0A040]: {_markup_escape(skill['description'])}")
            self.app.push_screen(ListToolsScreen("\n".join(lines)))
            return True

        if cmd == "/stu":
            usage = self.chat_session.usage
            info = (
                f"  Prompt Tokens:     {usage['prompt_tokens']}\n"
                f"  Completion Tokens: {usage['completion_tokens']}\n"
                f"  Total Tokens:      {usage['total_tokens']}"
            )
            self._msg_info(info)
            return True

        if cmd == "/log":
            if self.chat_session.log_file:
                log_path = os.path.abspath(self.chat_session.log_file.name)
                try:
                    pyperclip.copy(log_path)
                    copied = " (已复制到剪贴板)"
                except:
                    copied = ""
                self._msg_info(f"📄 {log_path}{copied}")
            return True

        if cmd == "/reload":
            await self.chat_session.reload_config()
            self.app.notify("配置/MCP/SKILLS已重新加载", title="配置", severity="information")
            self._update_header()
            return True

        if cmd == "/lark":
            lark_bot = self.app.lark_bot
            if lark_bot.is_running:
                # 停止
                try:
                    await lark_bot.stop_async()
                    self._update_header()
                    self.app.notify("飞书Bot已停止", title="飞书", severity="information")
                except Exception as e:
                    self.app.notify(f"停止失败: {e}", title="飞书", severity="error")
            else:
                # 启动
                try:
                    if lark_bot.load_config():
                        asyncio.create_task(lark_bot.start_async())
                        self._update_header()
                        self.app.notify("飞书Bot启动中...", title="飞书", severity="information")
                    else:
                        self.app.notify("飞书配置未启用或无效，请检查configs.json", title="飞书", severity="error")
                except Exception as e:
                    self.app.notify(f"启动失败: {e}", title="飞书", severity="error")
            return True

        if cmd == "/img":
            # if self.chat_session.agent_switch == 0:
            #     self.app.notify("需要先开启Agent模式", severity="warning")
            #     return True
            if not self.chat_session.llm_client or not self.chat_session.llm_client.support_multimodal:
                self.app.notify("当前模型不支持多模态", severity="warning")
                return True
            self.app.notify("按 ESC 结束图片选择", title="图片上传", severity="information")
            self.chat_session.img_path_list.clear()
            self._collecting_images = True
            return True

        if cmd == "/compact":
            if self.chat_session.agent_switch == 0:
                self.app.notify("压缩功能建议在 Agent 模式下使用", severity="warning")
                return True
            if len(self.chat_session.messages) <= 3:
                self.app.notify("对话上下文信息过少（需超过 3 条）", severity="warning")
                return True
            self._msg_info("🧠 正在压缩会话（调用 AI 生成摘要）...")
            filepath = await self.chat_session._compact_session()
            if filepath:
                fname = os.path.basename(filepath)
                self._msg_info(f"✅ 会话已压缩保存: [bold]{_markup_escape(fname)}[/bold]")
                self.app.notify(f"已保存: {fname}", title="压缩会话", severity="information")
            else:
                self.app.notify("压缩失败", severity="error")
            return True

        if cmd == "/load":
            if self.chat_session.agent_switch == 0:
                self.app.notify("加载 session 建议在 Agent 模式下使用", severity="warning")
                return True
            # 弹出文件选择界面
            self.app.push_screen(SessionLoadScreen())
            return True

        # ===== /skill:<name> <user_request> — 激活技能并处理 =====
        if user_input.startswith("/skill:"):
            if self.chat_session.agent_switch == 0:
                self.app.notify("需要先开启Agent模式", severity="error")
                return True
            skill_match = re.match(r"^/skill:(\S+)\s*(.*)", user_input, re.DOTALL)
            if not skill_match:
                self.app.notify("命令格式错误，正确格式：/skill:<技能名> <用户需求>", severity="error")
                return True
            skill_name = skill_match.group(1).strip()
            user_request = skill_match.group(2).strip()
            if not user_request:
                self.app.notify("请提供用户需求，格式：/skill:<技能名> <用户需求>", severity="error")
                return True
            skill = load_skill_full(skill_name)
            if not skill:
                available = [s['name'] for s in self.chat_session.skills_meta]
                self.app.notify(f"技能 '{skill_name}' 不存在。可用技能：{available}", severity="error")
                return True
            self.chat_session.active_skill = skill
            self.app.notify(f"✅ 技能「{skill.name}」已激活，正在按指令处理...", title="技能", severity="information")
            # 刷新系统提示词
            self.chat_session.messages[0] = {"role": "system", "content": await self.chat_session.gen_agent_system_content()}
            # 追加用户消息：技能指令 + 用户需求
            full_prompt = f"""请按照以下技能指令处理我的需求：

## 当前激活的技能指令
{skill.instruction}

---
## 我的需求
{user_request}"""
            self.chat_session.messages.append({"role": "user", "content": full_prompt})
            self._msg_info(f"🔧 技能「{skill.name}」已激活")
            self._msg_user(f"/skill:{skill_name} {user_request}")
            # 交给 LLM 处理
            input_widget = self.query_one("#chat-input")
            asyncio.create_task(self._run_user_message(full_prompt, input_widget))
            self._chat_start_time = datetime.now()
            return True

        # ===== /flow:<name> <user_request> — 运行 AgentFlow 工作流 =====
        if user_input.startswith("/flow:"):
            flow_match = re.match(r"^/flow:(\S+)\s*(.*)", user_input, re.DOTALL)
            if not flow_match:
                self.app.notify("命令格式错误，正确格式：/flow:<工作流名> <用户需求>", severity="error")
                return True
            flow_name = flow_match.group(1).strip()
            user_request = flow_match.group(2).strip()
            if not user_request:
                self.app.notify("请提供用户需求，格式：/flow:<工作流名> <用户需求>", severity="error")
                return True
            # 查找 flow 文件
            flows_meta = load_flows_metadata()
            flow_info = None
            for fm in flows_meta:
                if fm["name"] == flow_name:
                    flow_info = fm
                    break
            if not flow_info:
                available = [fm['name'] for fm in flows_meta]
                self.app.notify(f"工作流 '{flow_name}' 不存在。可用：{available}", severity="error")
                return True
            self._msg_info(f"📋 启动工作流「{flow_info['title']}」...")
            self._msg_user(f"/flow:{flow_name} {user_request}")
            self.app.push_screen(FlowRunnerScreen(
                flow_path=flow_info["path"],
                flow_name=flow_info["title"],
                user_request=user_request,
            ))
            return True

        self.app.notify(f"未知命令: {cmd}", severity="error")
        return True

    # ==================== Command Autocomplete ====================
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Show command suggestions when user types '/'."""
        if event.text_area.id != "chat-input":
            return

        # When text was set by _navigate_history, don't reset history state
        if self._suppress_history_reset:
            self._suppress_history_reset = False
            return
        # User manually typed — exit history navigation
        self._history_idx = None
        self._history_draft = ""

        # Suppress after Enter-autocomplete to allow submission on next Enter
        if self._suppress_autocomplete:
            self._suppress_autocomplete = False
            return

        val = event.text_area.text.strip()
        sg = self.query_one("#cmd-suggestions")

        if not val.startswith("/"):
            sg.display = False
            self._cmd_matches = []
            return

        # Build combined command list including dynamic /skill:<name> and /flow-<name> commands
        all_cmds = list(COMMANDS)
        for s in self.chat_session.skills_meta:
            all_cmds.append((f"/skill:{s['name']}", "输入需求"))
        flows_meta = load_flows_metadata()
        for fm in flows_meta:
            all_cmds.append((f"/flow:{fm['name']}", fm.get("description", "运行工作流")))

        self._cmd_matches = [(c, d) for c, d in all_cmds if c.startswith(val)]
        self._cmd_select_idx = 0
        if not self._cmd_matches:
            sg.display = False
            return

        self._render_suggestions()
        sg.display = True

    def _render_suggestions(self):
        """Rebuild ListView items from current _cmd_matches."""
        lv = self.query_one("#cmd-suggestions", ListView)
        items = []
        for cmd, desc in self._cmd_matches:
            label = f" [#7C8CF8]{cmd}[/#7C8CF8]  [#707090]{desc}[/#707090]"
            items.append(ListItem(Static(label)))
        lv.clear()
        lv.extend(items)
        lv.index = self._cmd_select_idx

    def _navigate_suggestion(self, delta: int):
        """Move selection up (-1) or down (+1)."""
        if not self._cmd_matches:
            return
        self._cmd_select_idx = (self._cmd_select_idx + delta) % len(self._cmd_matches)
        self.query_one("#cmd-suggestions", ListView).index = self._cmd_select_idx

    def _navigate_history(self, delta: int):
        """Move through input history: delta=-1 for older, +1 for newer."""
        if not self._input_history:
            return
        ta = self.query_one("#chat-input", TextArea)

        if self._history_idx is None:
            # Starting history navigation: save current draft
            self._history_draft = ta.text
            if delta == -1:
                self._history_idx = len(self._input_history) - 1
            else:
                return  # Nothing newer when at draft

        else:
            new_idx = self._history_idx + delta
            if new_idx < 0 or new_idx >= len(self._input_history):
                if delta == 1:
                    # Past newest: restore draft and exit history mode
                    self._history_idx = None
                    self._suppress_history_reset = True
                    ta.text = self._history_draft
                    ta.cursor_location = (0, len(ta.text))
                    self._history_draft = ""
                return
            self._history_idx = new_idx

        ta.text = self._input_history[self._history_idx]
        ta.cursor_location = (0, len(ta.text))
        self._suppress_history_reset = True

    def action_autocomplete(self, reverse: bool = False) -> None:
        """Tab: autocomplete the current /command prefix."""
        sg = self.query_one("#cmd-suggestions")
        if not sg.display:
            return
        inp = self.query_one("#chat-input", TextArea)
        val = inp.text.strip()
        if not val.startswith("/"):
            return

        # Build combined command list including dynamic /skill:<name> and /flow:<name> commands
        all_cmds = list(COMMANDS)
        for s in self.chat_session.skills_meta:
            all_cmds.append((f"/skill:{s['name']}", "输入需求"))
        flows_meta = load_flows_metadata()
        for fm in flows_meta:
            all_cmds.append((f"/flow:{fm['name']}", fm.get("description", "运行工作流")))

        if not self._cmd_matches:
            return

        if not reverse:
            idx = self._cmd_select_idx % len(self._cmd_matches)
            inp.text = self._cmd_matches[idx][0] + " "
        else:
            inp.text = self._cmd_matches[-1][0] + " "
        inp.cursor_location = (0, len(inp.text))
        sg.display = False
        self._cmd_matches = []

    async def _process_message(self, user_input: str):
        if self._collecting_images:
            # Strip surrounding quotes from pasted paths
            path = user_input.strip().strip("'\"")
            if os.path.isfile(path) and path.lower().endswith(('.png', '.jpg', '.jpeg')):
                if os.path.getsize(path) / 1024 <= 1024:
                    self.chat_session.img_path_list.append(path)
                    self.app.notify(f"已添加: {os.path.basename(path)}", severity="information")
                else:
                    self.app.notify("图片超过1MB", severity="error")
            else:
                self.app.notify("文件不存在或格式不支持", severity="error")
            return

        if hasattr(self, '_chat_start_time'):
            if (datetime.now() - self._chat_start_time).seconds > SESSION_TIMEOUT:
                await self.chat_session.start_new_chat()
                self._clear_chat()
                self._render_title()
                self._chat_start_time = datetime.now()
                self.app.notify(f"会话时长超{SESSION_TIMEOUT//3600}小时，已重置", severity="warning")
        else:
            self._chat_start_time = datetime.now()

        if not self.chat_session.llm_client:
            if self.chat_session.client_models:
                self.chat_session.switch_model("1")
            else:
                self._msg_error("没有可用的AI模型")
                return

        self._msg_user(user_input)

        input_widget = self.query_one("#chat-input")
        self._cmd_matches = []
        self.query_one("#cmd-suggestions").display = False
        # Run in background so Textual can process timer ticks during streaming
        asyncio.create_task(self._run_user_message(user_input, input_widget))


    async def _run_user_message(self, user_input: str, input_widget):
        """Background task: process user message, then re-enable input."""
        self._processing = True
        self.chat_session._cancel_event.clear()
        # Show waiting indicator immediately for long LLM response times
        self._streaming_buffer = ""
        self._thinking_buffer = ""
        log = self.query_one("#chat-log")
        if self._stream_widget is None:
            self._stream_widget = Static("[#707090]正在等待大模型响应...[/#707090]", classes="msg-stream")
            self._stream_widget.border_title = "⏳ 等待中..."
            log.mount(self._stream_widget)
            self._stream_widget.scroll_visible()
        try:
            await self.chat_session.process_user_message(user_input)
        except Exception as e:
            self._msg_error(f"处理消息出错: {str(e)}")
        finally:
            self._processing = False
            # If still showing waiting indicator (no stream events arrived), remove it
            if self._stream_widget is not None and self._streaming_buffer == "" and self._thinking_buffer == "":
                self._stream_widget.remove()
                self._stream_widget = None
            input_widget.focus()
            self._update_header()
            
    # ==================== Actions ====================
    async def _do_exit(self):
        """All exit paths converge here."""
        if self.chat_session.agent_switch != 0:
            await self.chat_session.disable_agent()
            # 停止飞书Bot
            try:
                if self.app.lark_bot.is_running:
                    await self.app.lark_bot.stop_async()
            except Exception:
                pass
            self.app.notify("Agent模式/Lark Bot已关闭", title="Agent模式/Lark Bot", severity="information")
            self._update_header()
            await asyncio.sleep(1)  # Allow UI to update before exit
        self.app.exit()

    async def action_quit(self):
        """Footer Quit / Ctrl+Q → _do_exit."""
        await self._do_exit()

    def action_show_help(self):
        self.app.push_screen(HelpScreen())

    def action_clear_screen(self):
        self._clear_chat()
        self._render_title()

    def action_newline(self):
        """Ctrl+J: handled by ChatTextArea._on_key, this is only for Footer display."""
        pass


    def action_cancel(self):
        """Esc: end image collection, dismiss suggestions, or cancel LLM."""
        # End image collection mode
        if self._collecting_images:
            self._collecting_images = False
            if self.chat_session.img_path_list:
                self.app.notify(f"已选择 {len(self.chat_session.img_path_list)} 张图片，请输入消息", severity="information")
            else:
                self.app.notify("已取消图片选择", severity="information")
            return
        # Dismiss command suggestions
        if self._cmd_matches:
            self._cmd_matches = []
            self.query_one("#cmd-suggestions").display = False
            return
        # Cancel LLM processing
        if self._processing:
            self.chat_session.cancel_current()
            self.app.notify("⏹ 已发送中断信号，正在停止...", severity="warning")


# ====================== Main Application ======================
class TinyClawApp(App):
    """Tiny Claw Textual TUI Application."""

    theme = "dracula"

    CSS = """

    /* ---- Chat log ---- */
    #chat-log {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        overflow-x: hidden;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: #5CC9A0;
        scrollbar-color-hover: #7C8CF8;
        scrollbar-background: #1A1A2E;
        scrollbar-background-hover: #2D2D4A;
    }

    #chat-log > Static {
        width: 100%;
        height: auto;
        padding: 1 1 0 1;
        margin: 0;
    }

    /* ── Message types (Static widgets) ── */
    .msg-user {
        border-top: round #3560A0;
        border-bottom: round #3560A0;
        border-left: round #3560A0;
        border-title-color: #4A8CF8;
        border-title-style: bold;
    }

    .msg-tool {
        border-top: round #e07b29;
        border-bottom: round #e07b29;
        border-left: round #e07b29;
        border-title-color: #E0A040;
        border-title-style: bold;
    }

    .msg-stream {
        border-top: round #5CC9A0;
        border-bottom: round #5CC9A0;
        border-left: round #5CC9A0;
        border-title-color: #5CC9A0;
        border-title-style: bold;
    }

    .msg-info {
        border-top: round #6A5ACD;
        border-bottom: round #6A5ACD;
        border-left: round #6A5ACD;
        border-title-color: #7C8CF8;
        border-title-style: bold;
    }

    .msg-error {
        border-top: round #D04040;
        border-bottom: round #D04040;
        border-left: round #D04040;
        border-title-color: #F06060;
        border-title-style: bold;
    }

    .msg-list {
        border-top: round #6A5ACD;
        border-bottom: round #6A5ACD;
        border-left: round #6A5ACD;
        border-title-color: #7C8CF8;
        border-title-style: bold;
    }

    .msg-title {
        border: none;
        text-align: center;
        color: #7C8CF8;
    }

    .msg-welcome {
        border: round #3A3A5A;
    }

    /* ── Markdown-based message bodies ── */
    .msg-assistant {
        width: 100%;
        height: auto;
        overflow-y: hidden;
        overflow-x: hidden;
        border-top: round #3A9060;
        border-bottom: round #3A9060;
        border-left: round #3A9060;
        padding: 1 1 0 1;
        margin: 0;
        border-title-color: #5CC9A0;
        border-title-style: bold;
    }

    .msg-tool-result {
        width: 100%;
        height: auto;
        overflow-y: hidden;
        overflow-x: hidden;
        border-top: round #5CC9A0;
        border-bottom: round #5CC9A0;
        border-left: round #5CC9A0;
        padding: 1 1 0 1;
        margin: 0;
        border-title-color: #5CC9A0;
        border-title-style: bold;
    }

    /* ---- Input ---- */
    /* ---- TextArea input ---- */
    .chat-input {
        width: 100%;
        max-height: 6;
        overflow-y: hidden;
        overflow-x: hidden;
        border: none;
        border-top: solid #363650;
        padding: 1 2;
    }

    .chat-input:focus {
        overflow-y: hidden;
        overflow-x: hidden;
        border: none;
        border-top: solid #7C8CF8;
    }

    /* ---- Header & Footer ---- */
    Header {
        color: #7C8CF8;
    }

    Footer {
        color: #707088;
    }

    /* ---- Modal Screens ---- */
    ModalScreen {
    }

    DangerousCommandScreen {
        align: center middle;
    }

    DangerousCommandScreen ScrollableContainer {
        width: 60;
        max-height: 15;
        border-top: solid #F06060;
        border-bottom: solid #F06060;
        background: #1A1A2E;
        scrollbar-color: #F06060;
        scrollbar-size-vertical: 1;
        overflow-y: auto;
    }

    UserChoiceScreen {
        align: center middle;
    }

    UserChoiceScreen ScrollableContainer {
        width: 64;
        max-height: 28;
        border-top: solid #5CC9A0;
        border-bottom: solid #5CC9A0;
        background: #1A1A2E;
        scrollbar-color: #5CC9A0;
        scrollbar-size-vertical: 1;
        overflow-y: auto;
    }

    #choice-content {
        width: 100%;
        height: auto;
        padding: 1 1;
    }

    SessionLoadScreen {
        align: center middle;
    }

    SessionLoadScreen FastScrollContainer {
        width: 72;
        max-height: 30;
        border-top: solid #7C8CF8;
        border-bottom: solid #7C8CF8;
        background: #1A1A2E;
        scrollbar-color: #7C8CF8;
        scrollbar-size-vertical: 1;
        overflow-y: auto;
    }

    #session-load-content {
        width: 100%;
        height: auto;
        padding: 1 2;
    }

    #danger-content {
        width: 100%;
        height: auto;
        padding: 1 1;
    }

    ModelSelectScreen, HelpScreen {
        align: center middle;
    }

    FlowRunnerScreen {
        align: center middle;
    }

    FlowRunnerScreen RichLog {
        width: 80;
        height: 22;
        border-top: solid #7C8CF8;
        background: #1A1A2E;
        scrollbar-color: #7C8CF8;
        scrollbar-size-vertical: 1;
    }

    #flow-stream {
        width: 80;
        height: auto;
        max-height: 9;
        border-bottom: solid #7C8CF8;
        background: #1A1A2E;
        padding: 0 1;
    }

    ListToolsScreen {
        align: center middle;
    }

    ListToolsScreen ScrollableContainer {
        width: 80;
        max-height: 40;
        border-top: solid #5CC9A0;
        border-bottom: solid #5CC9A0;
        scrollbar-color: #5CC9A0;
        scrollbar-size-vertical: 1;
        overflow-y: auto;
    }

    #lst-content {
        width: 100%;
        height: auto;
        padding: 1 2;
        border: none;
    }

    #help-content {
        width: 80;
        padding: 1 2;
        border: solid #7C8CF8;
    }

    ModelSelectScreen > Static {
        width: 52;
        padding: 1 2;
        border-top: solid #7C8CF8;
        border-bottom: solid #7C8CF8;
    }

    ListView {
        width: 52;
        height: auto;
        max-height: 32;
        border-top: solid #363650;
        border-bottom: solid #363650;
    }

    ListItem {
        padding: 0 2;
    }

    ListItem:hover {
        background: #2D2D4A;
    }

    /* ---- Command autocomplete suggestions ---- */
    #cmd-suggestions {
        max-height: 8;
        width: 100%;
        border-top: solid #7C8CF8;
        border-bottom: solid #7C8CF8;
        margin: 0;
        padding: 0;
    }

    #cmd-suggestions > ListItem {
        padding: 0 1;
    }

    /* ---- Task progress panel (agent-task mode) ---- */
    #task-panel {
        width: 100%;
        max-height: 10;
        border-top: solid #6A5ACD;
        border-bottom: solid #6A5ACD;
        border-title-color: #7C8CF8;
        border-title-style: bold;
        padding: 1 1;
        margin: 0;
        display: none;
    }
    """

    def __init__(self):
        super().__init__()
        self.chat_session = ChatSession()
        self.lark_bot = LarkBot()

    def copy_to_clipboard(self, text: str) -> None:
        """Sync to system clipboard in addition to Textual's internal clipboard."""
        try:
            pyperclip.copy(text)
        except Exception:
            pass
        super().copy_to_clipboard(text)

    async def action_quit(self):
        """Ctrl+Q → 走 ChatScreen._do_exit 流程（自动关闭Agent后退出）。"""
        screen = self.screen
        if hasattr(screen, '_do_exit'):
            await screen._do_exit()
        else:
            self.exit()

    def on_mount(self) -> None:
        self.push_screen(ChatScreen())

async def main():
    app = TinyClawApp()

    try:
        await app.run_async()
    finally:
        print("Tiny Claw TUI 已经退出。")



if __name__ == "__main__":
    asyncio.run(main())