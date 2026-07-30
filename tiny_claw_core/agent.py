# coding:utf-8
"""Agent — 独立可运行的智能体，每个Agent拥有独立的工作目录、技能、MCP配置和工具。"""
import warnings; warnings.filterwarnings("ignore", category=UserWarning)
import asyncio, json, typing, os, re, shutil, uuid, inspect, traceback, httpx
from datetime import datetime
from contextlib import AsyncExitStack
from typing import Any, Dict, Optional, List, Union
from dataclasses import dataclass, field
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from fastmcp.client.auth.oauth import OAuth
import subprocess, platform, sys
import base64
from PIL import Image
from io import BytesIO
from enum import Enum
import locale
import yaml

# ====================== Constants ======================
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_API_TIMEOUT = 360       # AI API 请求超时（秒）
MCP_HTTP_TIMEOUT = 300     # MCP streamable-http 客户端超时（秒）
MCP_INIT_TIMEOUT = 30      # MCP 服务初始化超时（秒）
BASH_TIMEOUT = 600         # bash 命令执行超时（秒）
TOOL_LOOP_MAX = 50         # 工具调用循环最大迭代次数


# ====================== Session Event Types ======================
class EventType(Enum):
    THINKING = "thinking"
    STREAMING = "streaming"
    STREAM_DONE = "stream_done"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM_INFO = "system_info"
    ERROR = "error"
    USER_INTERRUPT = "user_interrupt"
    AGENT_LOOP_DONE = "agent_loop_done"
    DANGEROUS_COMMAND = "dangerous_command"
    USER_CHOICE = "user_choice"


@dataclass
class SessionEvent:
    type: EventType
    data: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ====================== Skills ======================
@dataclass
class Skill:
    name: str
    description: str
    instruction: str
    path: str


def parse_skill_md(skill_path: str) -> tuple:
    with open(os.path.join(skill_path, "SKILL.md"), "r", encoding="utf-8") as f:
        content = f.read()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid SKILL.md format in {skill_path}")
    frontmatter_str, instruction = match.groups()
    frontmatter = {}
    for line in frontmatter_str.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            frontmatter[key.strip()] = val.strip()
    return frontmatter, instruction.strip()


def load_skills_metadata(skills_dir: str) -> List[Dict[str, str]]:
    """从指定 skills 目录加载技能元数据"""
    skills_meta = []
    if not os.path.isdir(skills_dir):
        return skills_meta
    for skill_name in os.listdir(skills_dir):
        if skill_name.startswith("."):
            continue
        skill_path = os.path.join(skills_dir, skill_name)
        if not os.path.isdir(skill_path):
            continue
        md_file = os.path.join(skill_path, "SKILL.md")
        if not os.path.exists(md_file):
            continue
        try:
            frontmatter, _ = parse_skill_md(skill_path)
            skills_meta.append({
                "name": frontmatter.get("name", skill_name),
                "description": frontmatter.get("description", "")
            })
        except Exception:
            pass
    return skills_meta


def load_skill_full(skill_name: str, skills_dir: str) -> Optional[Skill]:
    """从指定 skills 目录加载完整技能"""
    skill_path = os.path.join(skills_dir, skill_name)
    if not os.path.isdir(skill_path):
        return None
    try:
        frontmatter, instruction = parse_skill_md(skill_path)
        return Skill(
            name=frontmatter.get("name", skill_name),
            description=frontmatter.get("description", ""),
            instruction=instruction,
            path=skill_path
        )
    except Exception:
        return None


def extract_tool_calls(text: str) -> List[Dict]:
    pattern = r"<tool_call>(\w+)\s+({.*?})\s*</tool_call>"
    matches = re.findall(pattern, text, re.DOTALL | re.I)
    tool_calls = []
    for func_name, args_str in matches:
        try:
            args = json.loads(args_str, strict=False)
            tool_calls.append({"name": func_name, "arguments": args})
        except json.JSONDecodeError:
            pass
    return tool_calls


# ====================== Image Helper ======================
def image_to_base64(image_path, max_size_kb):
    """将图片文件转换为base64编码的字符串"""
    if image_path.startswith("http://") or image_path.startswith("https://"):
        with httpx.Client() as client:
            response = client.get(image_path)
            response.raise_for_status()
            raw_data = response.content
    else:
        with open(image_path, 'rb') as image_file:
            raw_data = image_file.read()

    img_size_kb = len(raw_data) / 1024
    if img_size_kb <= max_size_kb:
        encoded_string = base64.b64encode(raw_data).decode('utf-8')
        return f"data:image/png;base64,{encoded_string}"

    img = Image.open(BytesIO(raw_data))
    fmt = img.format
    if fmt in ('JPEG', 'JPG'):
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
    elif fmt == 'PNG':
        if img.mode == 'P':
            img = img.convert('RGBA')

    buf = BytesIO()
    save_fmt = 'JPEG' if fmt in ('JPEG', 'JPG') else 'PNG'
    img.save(buf, format=save_fmt, quality=int(img_size_kb / max_size_kb * 100), optimize=True)
    size = buf.tell()
    while size > max_size_kb * 1024:
        w, h = img.size
        img = img.resize((int(w * 0.8), int(h * 0.8)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format=save_fmt, quality=80, optimize=True)
        size = buf.tell()
    encoded_string = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded_string}"


# ====================== Script Helpers ======================
def build_script_command(script_path: str, args: List[str]) -> Optional[str]:
    script_name = os.path.basename(script_path)
    ext = os.path.splitext(script_name)[1].lower()
    is_windows = platform.system().lower() == "windows"
    args_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in args)

    if ext == ".py":
        python_cmd = "python" if is_windows else "python3"
        return f'{python_cmd} "{script_path}" {args_str}'
    elif ext in (".bat", ".cmd"):
        if not is_windows:
            return None
        return f'"{script_path}" {args_str}'
    elif ext == ".ps1":
        if not is_windows:
            return f'pwsh -File "{script_path}" {args_str}'
        return f'powershell -ExecutionPolicy Bypass -File "{script_path}" {args_str}'
    elif ext == ".sh":
        return f'bash "{script_path}" {args_str}'
    elif ext == ".js":
        return f'node "{script_path}" {args_str}'
    elif ext == ".exe":
        if not is_windows:
            return None
        return f'"{script_path}" {args_str}'
    else:
        return None


# ====================== LLM Clients ======================
class LLMClient:
    def __init__(self, api_key: str, ai_channel: str = "OpenAI",
                 ai_model: str = "gpt-4.1",
                 ai_api_url: str = "https://api.openai.com/v1/chat/completions",
                 ai_provider: str = "OpenAI",
                 support_stream: bool = True,
                 support_tool_call: bool = False,
                 support_thinking: tuple = (False, "off"),
                 support_multimodal: bool = False,
                 http_proxy: str = None) -> None:
        self.api_key = api_key
        self.ai_channel = ai_channel
        self.ai_model = ai_model
        self.ai_api_url = ai_api_url
        self.ai_provider = ai_provider
        self.support_stream = support_stream
        self.support_tool_call = support_tool_call
        self.support_thinking = support_thinking
        self.support_multimodal = support_multimodal
        self.http_proxy = http_proxy

    async def get_response(self, messages: list, use_tool_call=False, tools=None) -> tuple:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "messages": messages,
            "model": self.ai_model,
            "stream": False,
            "stop": None,
        }
        if self.support_tool_call and use_tool_call and tools:
            payload["tools"] = tools
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            else:
                payload["thinking"] = {"type": "disabled"}

        try:
            async with httpx.AsyncClient(proxy=self.http_proxy) as client:
                response = await client.post(self.ai_api_url, headers=headers, json=payload, timeout=AI_API_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                usage = None
                if data.get("usage"):
                    usage = {
                        "prompt_tokens": data["usage"].get("prompt_tokens"),
                        "completion_tokens": data["usage"].get("completion_tokens"),
                        "total_tokens": data["usage"].get("total_tokens")
                    }
                resp_message = data["choices"][0]["message"]
                tool_calls = []
                if resp_message.get("tool_calls"):
                    tool_calls = [func for func in data["choices"][0]["message"]["tool_calls"]
                                  if func["type"] == "function"]
                reasoning_content = resp_message.get("reasoning_content")
                return resp_message.get("content"), usage, tool_calls, reasoning_content
        except httpx.HTTPError as e:
            return (f"I encountered an error: Error getting LLM response. {str(e)}. "
                    "Please try again or rephrase your request."), None, None, None

    async def yield_response(self, messages: list, use_tool_call=False, tools=None,
                             cancel_event: asyncio.Event = None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "messages": messages,
            "model": self.ai_model,
            "stream": True,
            "stop": None,
        }
        if self.support_tool_call and use_tool_call and tools:
            payload["tools"] = tools
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            else:
                payload["thinking"] = {"type": "disabled"}

        async with httpx.AsyncClient(proxy=self.http_proxy) as client:
            try:
                async with client.stream("POST", url=self.ai_api_url,
                                         headers=headers, json=payload, timeout=AI_API_TIMEOUT) as response:
                    try:
                        async for line in response.aiter_lines():
                            if cancel_event and cancel_event.is_set():
                                break
                            if not line.strip():
                                continue
                            if not line.startswith("data:"):
                                yield 0, line
                                break
                            line_data = line[5:].strip()
                            if line_data == "[START]":
                                continue
                            if line_data == "[DONE]":
                                break
                            try:
                                data = json.loads(line_data)
                                if not data.get('choices'):
                                    continue
                                if data['choices'][0].get("delta", {}).get("content"):
                                    yield 1, data['choices'][0]["delta"]["content"]
                                if data['choices'][0].get("usage"):
                                    yield 2, data['choices'][0]["usage"]
                                if data.get("usage"):
                                    yield 2, data.get("usage")
                                if data['choices'][0].get("delta", {}).get("tool_calls"):
                                    tool = data['choices'][0]["delta"]["tool_calls"][0]
                                    if tool.get("type") == "function" and tool.get("id", "").strip():
                                        yield 31, json.dumps(tool)
                                    elif tool["function"].get("arguments"):
                                        yield 32, tool["function"]["arguments"]
                                    else:
                                        continue
                                if data['choices'][0].get("delta", {}).get("reasoning_content"):
                                    yield 4, data['choices'][0]["delta"]["reasoning_content"]
                            except Exception:
                                continue
                    except Exception as e:
                        yield 0, f" {str(e)}"
            except (httpx.HTTPError, StopAsyncIteration) as e:
                error_message = f"{str(e)}"
                if isinstance(e, httpx.HTTPStatusError):
                    error_message = f"Response code:{e.response.status_code}, content: {repr(e.response.text)}"
                yield 0, error_message


# ====================== MCP Classes ======================
class Tool:
    def __init__(self, name: str, description: str, input_schema: dict,
                 server_name: str = None) -> None:
        self.name = name
        self.server_name = server_name
        self.description = description
        self.input_schema = input_schema

    def format_for_llm(self) -> str:
        args_desc = []
        if "properties" in self.input_schema:
            for param_name, param_info in self.input_schema["properties"].items():
                arg_desc = f"- {param_name}: {param_info.get('description', 'No description')}"
                if param_name in self.input_schema.get("required", []):
                    arg_desc += " (required)"
                args_desc.append(arg_desc)
        return f"""`{self.name}` 
Description: {self.description.strip()}
Arguments:
{chr(10).join(args_desc)}
"""



class Server:
    def __init__(self, name: str, config: dict) -> None:
        self.name = name
        self.config = config
        self.stdio_context: Any | None = None
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack = AsyncExitStack()
        self.capabilities = set()
        self._transport_gen: Any | None = None

    @staticmethod
    async def _close_transport(gen: Any) -> None:
        try:
            await gen.__aexit__(None, None, None)
        except (RuntimeError, asyncio.CancelledError, GeneratorExit):
            pass
        except Exception:
            pass
        except BaseException:
            pass

    async def initialize(self) -> None:
        transport = self.config.get("transport")
        try:
            if transport == "stdio":
                command = (
                    shutil.which("npx")
                    if self.config.get("command") == "npx"
                    else self.config.get("command")
                )
                server_params = StdioServerParameters(
                    command=command,
                    args=self.config.get("args"),
                    env=self.config.get("env")
                )
                self._transport_gen = stdio_client(server_params)
                read, write = await self._transport_gen.__aenter__()
            elif transport == "sse":
                sseUrl = self.config.get("url", "")
                if not sseUrl or sseUrl.endswith("/mcp"):
                    raise ValueError("sse need url and the url should not end with /mcp")
                self._transport_gen = sse_client(sseUrl)
                read, write = await self._transport_gen.__aenter__()
            elif transport == "streamable-http":
                streamableHttpUrl = self.config.get("url", "")
                if not streamableHttpUrl or not streamableHttpUrl.endswith("/mcp"):
                    raise ValueError("streamable-http need url and the url should end with /mcp")
                if self.config.get("headers") or self.config.get("oauth"):
                    headers = self.config.get("headers")
                    oauth_provider = None
                    if self.config.get("oauth"):
                        oauth_provider = OAuth(
                            mcp_url=streamableHttpUrl,
                            scopes=["openid", "profile"]
                        )
                    async_httpx_client = httpx.AsyncClient(headers=headers, timeout=MCP_HTTP_TIMEOUT, auth=oauth_provider)
                    self._transport_gen = streamable_http_client(streamableHttpUrl, http_client=async_httpx_client)
                else:
                    self._transport_gen = streamable_http_client(streamableHttpUrl)
                read, write, getSessionIdCallback = await self._transport_gen.__aenter__()
            else:
                raise ValueError("The transport must be stdio/sse/streamable-http.")
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            init_resp = await session.initialize()
            if init_resp.capabilities.tools:
                self.capabilities.add("tools")
            self.session = session
        except BaseException:
            traceback.print_exc()
            await self.cleanup()
            raise Exception(f"Initialize MCP Server [{self.name}] Failed")

    async def list_tools(self) -> list | None:
        try:
            if not self.session:
                raise RuntimeError(f"Server {self.name} not initialized")
            if "tools" not in self.capabilities:
                return []
            tools_response = await self.session.list_tools()
            tools = []
            for item in tools_response:
                if isinstance(item, tuple) and item[0] == "tools":
                    tools.extend(
                        Tool(tool.name, tool.description, tool.inputSchema, server_name=self.name)
                        for tool in item[1]
                    )
            return tools
        except Exception:
            await self.cleanup()
            return None

    async def cleanup(self) -> None:
        if self._transport_gen is not None:
            try:
                await self._close_transport(self._transport_gen)
            except BaseException:
                pass
            self._transport_gen = None
        if self.exit_stack is not None:
            try:
                await self.exit_stack.aclose()
            except (RuntimeError, asyncio.CancelledError, GeneratorExit):
                pass
            except Exception:
                pass
            except BaseException:
                pass
            finally:
                self.session = None
                self.stdio_context = None
                self.exit_stack = AsyncExitStack()

    async def execute_tool(self, tool_name: str, arguments: dict) -> Any:
        try:
            if not self.session:
                raise RuntimeError(f"Server {self.name} not initialized")
            result = await self.session.call_tool(tool_name, arguments)
            return result
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text",
                                     text=f"Error executing tool {self.name} - {tool_name}. {str(e)}.")],
                isError=True)




# ====================== Base Tool Definitions ======================
BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "skill_call",
            "description": "调用技能",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "技能名称"},
                    "user_request": {"type": "string", "description": "用户请求"}
                },
                "required": ["skill_name", "user_request"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "调用MCP服务",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {"type": "string", "description": "根据需求选择的MCP服务名称"},
                    "user_request": {"type": "string", "description": "用户的工具调用请求"}
                },
                "required": ["server_name", "user_request"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "执行shell命令（Windows使用PowerShell，Linux/macOS使用bash）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "完整的PowerShell（Windows）或bash（Linux/macOS）命令"},
                    "command_type": {"type": "string", "description": "在Linux/macOS下默认为bash，Windows下为powershell"}
                },
                "required": ["command", "command_type"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本类型文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "start_line_num": {"type": "number", "description": "读取的开始行号，默认1"},
                    "lines": {"type": "number", "description": "读取的行数，默认-1：表示全部"}
                },
                "required": ["path"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "read_file_and_linenum",
            "description": "读取文本类型文件, 且读取的内容带有行号",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "start_line_num": {"type": "number", "description": "读取的开始行号，默认1"},
                    "lines": {"type": "number", "description": "读取的行数，默认-1：表示全部"}
                },
                "required": ["path"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "insert_file_at_line",
            "description": "在指定行号前插入内容到文本文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "line_number": {"type": "number", "description": "行号"},
                    "content": {"type": "string", "description": "需要插入的内容"}
                },
                "required": ["path", "line_number", "content"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "追加信息到文本文件(会主动创建文件)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "需要追加的内容"}
                },
                "required": ["path"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "一次性将信息到文本文件(会主动创建文件)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "需要写入的内容"}
                },
                "required": ["path"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "修改文件的指定文本内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_text": {"type": "string", "description": "需要被替换的文本内容"},
                    "new_text": {"type": "string", "description": "新的文本内容"},
                    "replace_all": {"type": "boolean", "description": "是否替换所有匹配项"}
                },
                "required": ["path", "old_text", "new_text", "replace_all"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "execute_script",
            "description": "执行脚本",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string", "description": "脚本路径"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "脚本需要的各个参数"},
                    "timeout": {"type": "number", "description": "脚本执行超时时间，单位秒"}
                },
                "required": ["script_path"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "insert_images",
            "description": "给当前对话加入图片路径列表，用于大模型理解",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_paths": {
                        "type": "array",
                        "description": "图片路径（可以是url、本地文件路径）",
                        "items": {"type": "string"}
                    }
                },
                "required": ["image_paths"]
            }
        }
    }, {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "当你需要用户做出选择或确认时调用此工具, 直接列出各项选择，并等待用户选择后返回结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "直接列出的各项选择，每项应清晰说明该选项的含义"
                    }
                },
                "required": ["choices"]
            }
        }
    },
]

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




class Agent:
    """独立可运行的智能体。

    每个 Agent 拥有：
    - 独立的工作目录（workspace_dir），包含 AGENT.MD、skills/、logs/ 等
    - 独立的 LLM 客户端
    - 独立的 MCP 服务连接
    - 独立的技能系统
    - 独立的对话历史和工具调用循环
    """

    def __init__(self,
                 name: str = "default",
                 workspace_dir: str = None,
                 llm_config: dict = None,
                 mcp_configs: dict = None,
                 skills_dir: str = None,
                 base_tools_override: list = None,
                 web_search_config: dict = None,
                 event_callback=None,
                 auto_reject_dangerous_command: bool = False):
        self.name = name
        # 无人值守场景（如 AgentFlow 工作流）没有人能响应危险命令确认弹窗，
        # 开启后遇到危险命令直接拒绝执行，而不是emit事件后永久阻塞等待。
        self._auto_reject_dangerous_command = auto_reject_dangerous_command
        self.workspace_dir = workspace_dir or os.path.join(_PROJECT_DIR, f"agent_workspaces/{name}")
        self.skills_dir = skills_dir or os.path.join(self.workspace_dir, "skills")
        self.outputs_dir = os.path.join(self.workspace_dir, "outputs")
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.outputs_dir, exist_ok=True)

        # LLM
        self.llm_client: Optional[LLMClient] = None
        self.llm_config = llm_config or {}

        # MCP
        self.mcp_configs = mcp_configs or {}
        self.servers: list[Server] = []
        self.invalid_servers: set[Server] = set()

        # Skills
        self.skills_meta: List[Dict[str, str]] = []
        self.active_skill: Optional[Skill] = None

        # Active MCP
        self.active_mcp: Optional[Server] = None
        self.mcp_when_to_use: str = ""

        # Conversation
        self.messages: list = []
        self.usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.img_path_list: list = []

        # Tools
        self._base_tools = base_tools_override or BASE_TOOLS.copy()
        if web_search_config and not web_search_config.get("disabled", True):
            self._base_tools.append(WEB_SEARCH_TOOL)

        # Events & async primitives
        self._event_callback = event_callback
        self._cancel_event = asyncio.Event()
        self._dangerous_command_event = asyncio.Event()
        self._dangerous_command_confirmed = False
        self._user_choice_event = asyncio.Event()
        self._user_choice_result = None

        # Tool handlers
        self.tool_handlers = {
            "skill_call": self.handle_skill_call,
            "mcp_call": self.handle_mcp_call,
            "execute_bash": self.execute_bash,
            "execute_script": lambda **kw: self.execute_script(kw["script_path"],
                                                                kw.get("args", []),
                                                                kw.get("timeout", 30)),
            "read_file": lambda **kw: self.read_file(kw["path"],
                                                      kw.get("start_line_num", 1),
                                                      kw.get("lines", -1)),
            "read_file_and_linenum": lambda **kw: self.read_file_and_linenum(
                kw["path"], kw.get("start_line_num", 1), kw.get("lines", -1)),
            "write_file": lambda **kw: self.write_file(kw["path"], kw["content"]),
            "append_file": lambda **kw: self.append_file(kw["path"], kw["content"]),
            "insert_file_at_line": lambda **kw: self.insert_file_at_line(
                kw["path"], kw["line_number"], kw["content"]),
            "edit_file": lambda **kw: self.edit_file(kw["path"], kw["old_text"],
                                                      kw["new_text"],
                                                      kw.get("replace_all", False)),
            "insert_images": lambda **kwargs: self.insert_images(kwargs["image_paths"]),

            "ask_user": self.handle_ask_user,
        }
        if web_search_tool_enabled := (web_search_config and not web_search_config.get("disabled", True)):
            self.tool_handlers["web_search"] = lambda **kw: self.web_search(kw["query"])

        # Output file
        self.output_file = None

    def _emit(self, event_type: EventType, data=None, **extra):
        if self._event_callback:
            self._event_callback(SessionEvent(type=event_type, data=data, extra=extra))

    def cancel_current(self):
        self._cancel_event.set()

    def confirm_dangerous_command(self, confirmed: bool):
        self._dangerous_command_confirmed = confirmed
        self._dangerous_command_event.set()

    def answer_user_choice(self, result: str):
        self._user_choice_result = result
        self._user_choice_event.set()

    def get_agent_md_content(self) -> str:
        """读取当前工作目录下的 AGENT.MD"""
        agent_md = os.path.join(self.workspace_dir, "AGENT.MD")
        if os.path.exists(agent_md):
            with open(agent_md, "r", encoding="utf-8") as f:
                return f.read()
        return "[暂无]"

    # ====================== Init / Teardown ======================
    def configure_llm(self, llm_config: dict) -> bool:
        """配置 LLM 客户端"""
        self.llm_config = llm_config
        if llm_config.get("api_style", "").lower() == "openai":
            self.llm_client = LLMClient(
                api_key=llm_config["api_key"],
                ai_channel=llm_config.get("ai_channel", "OpenAI"),
                ai_model=llm_config.get("ai_model", "gpt-4.1"),
                ai_api_url=llm_config.get("ai_api_url", "https://api.openai.com/v1/chat/completions"),
                ai_provider=llm_config.get("ai_provider", "OpenAI"),
                support_tool_call=llm_config.get("support_tool_call", False),
                support_stream=llm_config.get("support_stream", True),
                support_thinking=llm_config.get("support_thinking", (False, "off")),
                support_multimodal=llm_config.get("support_multimodal", False),
                http_proxy=llm_config.get("api_proxy")
            )
            return True
        return False

    async def init_mcp_servers(self, mcp_configs: dict = None):
        """初始化 MCP 服务"""
        if mcp_configs:
            self.mcp_configs = mcp_configs
        await self.cleanup_servers()
        self.servers = [
            Server(name, srv_config)
            for name, srv_config in self.mcp_configs.items()
            if not srv_config.get("disabled")
        ]
        for server in self.servers:
            try:
                await self._init_one_server(server)
            except BaseException as e:
                self.invalid_servers.add(server)
                self._emit(EventType.ERROR, {"message": f"MCP [{self.name}] 服务 [{server.name}] 初始化失败: {e}"})
                try:
                    await server.cleanup()
                except BaseException:
                    pass
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0)

    async def _init_one_server(self, server: Server) -> None:
        task = asyncio.ensure_future(server.initialize())
        try:
            await asyncio.wait_for(task, timeout=MCP_INIT_TIMEOUT)
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            raise

    async def load_mcp_tools(self) -> List[Tool]:
        """加载所有可用 MCP 工具"""
        all_tools = []
        self.mcp_when_to_use = ""
        self.active_mcp = None

        for server in self.servers:
            if server in self.invalid_servers:
                continue
            tools = await server.list_tools()
            if tools is None:
                self.invalid_servers.add(server)
                self._emit(EventType.ERROR, {"message": f"MCP [{self.name}] 服务 [{server.name}] 工具列表获取失败"})
                await asyncio.sleep(0)
                continue
            if tools:
                all_tools.extend(tools)
            self.mcp_when_to_use += f"\n**Server**: {server.name}\n**When-to-use**: {server.config.get('when_to_use', '')}\n"
            await asyncio.sleep(0)

        return all_tools

    async def cleanup_servers(self) -> None:
        all_servers = list(self.servers) + list(self.invalid_servers)
        for server in reversed(all_servers):
            try:
                await server.cleanup()
            except Exception:
                pass
        self.servers.clear()
        self.invalid_servers.clear()

    def load_skills(self):
        """从工作目录加载技能"""
        self.skills_meta = load_skills_metadata(self.skills_dir)

    # ====================== Chat / System Prompt ======================
    async def gen_system_prompt(self) -> str:
        """生成系统提示词"""
        user_memory = self.get_agent_md_content()

        skills_list = "\n".join(
            [f"- {s['name']}: {s['description']}" for s in self.skills_meta])

        system_prompt = f"""## 角色定义      
你是智能助手「{self.name}」, 运行于 {platform.system()}，可以根据需求调用工具/技能/MCP服务。

## 核心工作
根据用户的问题，从工具列表中选择合适的工具、技能或MCP服务进行调用。  
 - 如果无需调用，或无匹配情况，直接回答即可。

## 当前用户技能工具使用习惯
{user_memory}

## 可用技能
{skills_list}

### 技能激活
当你调用 skill_call 后，你会获得该技能的详细指令。之后处理该任务时，请遵循技能指令。  
参考文件路径以`@skill/`开头的，直接作为技能内文件路径。

## 可用MCP服务
{self.mcp_when_to_use}

### MCP服务激活
当用户需求匹配某个MCP服务时，需要判断：提供的tools列表中是否包含此MCP服务提供的工具（MCP工具描述开头大致为“[由MCP服务`xxx`提供]”）？  
    - 不包含：那么调用`mcp_call`激活该MCP服务，这样提供的tools列表中将增加此mcp服务提供的工具。  
    - 包含：不要重复激活，直接调用对应工具  
> MCP服务没有匹配功能时，不要使用。

---
现在请开始处理。[今天:{datetime.now().strftime('%Y-%m-%d')}][工作目录:{self.workspace_dir}]
"""
        return system_prompt

    def gen_chat_system_content(self) -> str:
        return f"你是一个智能助手，根据用户的提问，直接、正确、绝不捏造信息地回答用户问题(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    # ====================== Start / Stop ======================
    async def start(self):
        """启动 Agent（Agent 模式，带工具调用循环）"""
        self.load_skills()
        try:
            await self.init_mcp_servers()
        except BaseException:
            pass
        await self.load_mcp_tools()
        self.messages = [
            {"role": "system", "content": await self.gen_system_prompt()}
        ]

    async def stop(self):
        """停止 Agent，清理资源"""
        self.active_skill = None
        self.skills_meta = []
        self.active_mcp = None
        await self.cleanup_servers()
        self.messages = []
        if self.output_file and not self.output_file.closed:
            self.output_file.close()

    async def new_chat(self):
        """开始新对话"""
        self.messages.clear()
        self.active_skill = None
        self.active_mcp = None
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.messages.append({"role": "system", "content": await self.gen_system_prompt()})

    async def handle_ask_user(self, choices: list) -> str:
        if not choices:
            return "[ERROR] 选项列表不能为空"
        self._user_choice_event.clear()
        self._user_choice_result = None
        self._emit(EventType.USER_CHOICE, {"choices": choices})
        await self._user_choice_event.wait()
        if self._user_choice_result is None:
            return "[用户取消了选择]"
        return self._user_choice_result

    # ====================== Tool Handlers ======================
    async def execute_bash(self, command: str, command_type: str = "bash") -> str:
        """执行 shell 命令（安全过滤）"""
        is_windows = platform.system().lower() == "windows"

        if not command.strip():
            return "[ERROR]命令不能为空"

        if is_windows:
            if command_type == "bash":
                return "[ERROR]Windows只支持powershell类型"
            ps_dangerous_patterns = [
                r"-enc(?:odedcommand)?\s", r"\biex\b", r"invoke-expression",
                r"invoke-webrequest.*-outfile", r"\biwr\b.*-outfile",
                r"new-object\s+net\.webclient", r"downloadfile", r"downloadstring",
                r"invoke-restmethod.*-outfile", r"\birm\b.*-outfile",
                r"\bremove-item\b", r"\bdel\b", r"\brmdir\b",
                r"format-volume", r"clear-disk", r"initialize-disk",
                r"set-disk", r"clear-recyclebin",
                r"stop-computer", r"restart-computer",
                r"shutdown\.exe", r"shutdown\s",
                r"stop-process.*-force", r"\bkill\b.*-force",
                r"taskkill", r"tskill",
                r"remove-itemproperty.*hk", r"set-itemproperty.*hk",
                r"new-itemproperty.*hk", r"set-executionpolicy",
                r"net\s+(localgroup|user|group)\s",
                r"sc\.exe\s+(stop|config|delete)", r"stop-service.*-force",
                r"set-service.*-status.*stopped",
                r"start-process.*-verb\s+runas",
                r"-windowstyle\s+hidden",
                r"invoke-command", r"invoke-wmimethod",
                r"new-cimsession", r"enter-pssession",
                r"disable-windowsoptionalfeature",
                r"dism\.exe\s+/online\s+/disable",
            ]
            matched_patterns = [p for p in ps_dangerous_patterns
                                if re.search(p, command, re.IGNORECASE)]
            if matched_patterns:
                if self._auto_reject_dangerous_command:
                    return "[ERROR]PowerShell 命令包含潜在危险操作，工作流无人值守，已自动拒绝。"
                self._dangerous_command_event.clear()
                self._dangerous_command_confirmed = False
                self._emit(EventType.DANGEROUS_COMMAND, {
                    "command": command, "patterns": matched_patterns,
                })
                await self._dangerous_command_event.wait()
                if not self._dangerous_command_confirmed:
                    return "[ERROR]PowerShell 命令包含潜在危险操作，已被拒绝。"

            if not command.lower().startswith("powershell "):
                escaped_command = command.replace('"', '\\"')
                command = f'powershell -Command "{escaped_command}"'
        else:
            linux_dangerous = [
                r"\brm\b", r"\brmdir\b", r"\bdd\b", r"\bmkfs\b", r"\bfdisk\b",
                r"\bsudo\b", r"\bsu\b", r"\bpasswd\b", r"\breboot\b", r"\bshutdown\b",
                r"\bpoweroff\b", r"\bhalt\b", r"\bkill\s+-9\b", r"\bpkill\b",
                r"\bwget\s+\S+\s+\|\s+sh\b", r"\bcurl\s+\S+\s+\|\s+sh\b",
                r"\bsystemctl\s+disable\b", r"\bchkconfig\s+off\b",
                r"\biptables\s+-F\b", r"\bufw\s+disable\b", r"\bsetenforce\s+0\b",
            ]
            matched_patterns = [p for p in linux_dangerous
                                if re.search(p, command, re.IGNORECASE)]
            if matched_patterns:
                if self._auto_reject_dangerous_command:
                    return "[ERROR]命令包含潜在危险操作，工作流无人值守，已自动拒绝。"
                self._dangerous_command_event.clear()
                self._dangerous_command_confirmed = False
                self._emit(EventType.DANGEROUS_COMMAND, {
                    "command": command, "patterns": matched_patterns,
                })
                await self._dangerous_command_event.wait()
                if not self._dangerous_command_confirmed:
                    return "[ERROR]禁止执行该命令"

        try:
            cp_env = os.environ.copy()
            cp_env["PYTHONIOENCODING"] = "utf-8"
            system_encoding = locale.getpreferredencoding(False)

            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=BASH_TIMEOUT, encoding=system_encoding, errors="replace",
                cwd=self.workspace_dir, env=cp_env
            )
            if result.returncode == 0:
                return result.stdout if result.stdout else "(命令执行成功，无输出)"
            else:
                return f"[ERROR]命令执行失败 (退出码 {result.returncode}):\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return f"[ERROR]命令执行超时（{BASH_TIMEOUT}秒）"
        except Exception as e:
            return f"[ERROR]执行出错: {str(e)}"

    def web_search(self, query: str) -> str:
        payload = {
            "messages": [{"content": f"{query}", "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": 20}],
            "search_recency_filter": "year"
        }
        with httpx.Client() as client:
            search_key = self.llm_config.get("web_search_api_key", "**********")
            try:
                response = client.post(
                    url="https://qianfan.baidubce.com/v2/ai_search/web_search",
                    headers={
                        "Authorization": f"Bearer {search_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
                response.raise_for_status()
                formatted_context = ""
                for i, result in enumerate(response.json()['references']):
                    formatted_context += f"[{i + 1}] 标题: {result['title']}\n"
                    formatted_context += f"    摘要: {result['content']}\n\n"
                    formatted_context += f"    原文链接: {result['url']}\n\n"
                return formatted_context
            except Exception as e:
                return f"[ERROR]Web Search Error: {e}"

    def write_file(self, path: str, content: str) -> str:
        if not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
        file_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, mode="w", encoding="utf-8") as f:
                f.write(content)
            return f"文件保存成功，文件路径:{file_path}"
        except Exception as e:
            return f"[ERROR]文件保存失败：{e}"

    def edit_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        if not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
        source_file_path = os.path.abspath(path).removeprefix("file:///")
        if not os.path.exists(source_file_path):
            return f"[ERROR]源文件不存在: {source_file_path}"
        try:
            file_size = os.path.getsize(source_file_path)
            if file_size > 1024 * 1024 * 5:
                return f"[ERROR]文件过大: {file_size / 1024 / 1024} MB"
            with open(source_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if old_text not in content:
                return f"[WARNING]未找到需要替换的文本内容"
            backup_file_path = source_file_path + ".backup"
            if not os.path.exists(backup_file_path):
                shutil.copy2(source_file_path, backup_file_path)
            if replace_all:
                new_content = content.replace(old_text, new_text)
                count = content.count(old_text)
            else:
                new_content = content.replace(old_text, new_text, 1)
                count = 1
            with open(source_file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"文件备份并成功替换了 {count} 处文本内容"
        except Exception as e:
            return f"[ERROR]文件替换失败：{e}"

    def append_file(self, path: str, content: str) -> str:
        if not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
        file_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, mode="a", encoding="utf-8") as f:
                f.write(content)
                f.flush()
            return f"文件追加成功，文件路径:{file_path}"
        except Exception as e:
            return f"[ERROR]文件追加失败：{e}"

    def insert_file_at_line(self, path, line_number: int, content: str):
        if not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
        file_path = os.path.abspath(path)
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            file_size = os.path.getsize(file_path)
            if file_size > 1024 * 1024 * 2:
                return f"[ERROR]文件过大"
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if isinstance(line_number, str):
                line_number = int(line_number)
            if 1 <= line_number <= len(lines) + 1:
                lines.insert(line_number - 1, content + '\n')
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                return f"在第 {line_number} 行前插入了文本"
            else:
                return f"[ERROR]行号 {line_number} 超出范围（文件共 {len(lines)} 行）"
        except Exception as e:
            return f"[ERROR]文件在指定行后插入信息失败：{e}"

    def read_file(self, path: str, start_line_num: int = 1, lines: int = -1) -> str:
        if path.startswith("http:") or path.startswith("https:"):
            return f"[ERROR]此路径为网页，无法直接读取"
        path = os.path.expanduser(path)
        if path.startswith("@skill/") and self.active_skill:
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path.removeprefix("file:///")
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                if lines == -1:
                    if start_line_num <= 1:
                        return f.read()
                    all_lines = f.readlines()
                    return "".join(all_lines[start_line_num - 1:])
                else:
                    all_lines = f.readlines()
                    end = min(start_line_num - 1 + lines, len(all_lines))
                    return "".join(all_lines[start_line_num - 1:end])
        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"

    def read_file_and_linenum(self, path: str, start_line_num: int = 1, lines: int = -1) -> str:
        path = os.path.expanduser(path)
        if path.startswith("@skill/") and self.active_skill:
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            path = os.path.join(self.workspace_dir, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path.removeprefix("file:///")
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                if lines == -1:
                    all_lines = f.readlines()
                    end = len(all_lines)
                else:
                    all_lines = f.readlines()
                    end = min(start_line_num - 1 + lines, len(all_lines))
                result = []
                max_linenum_width = len(str(end))
                for i in range(start_line_num - 1, end):
                    line_num = i + 1
                    result.append(f"{line_num:>{max_linenum_width}}| {all_lines[i].rstrip()}")
                return "\n".join(result)
        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"

    def execute_script(self, script_path: str, args: List[str] = None, timeout: int = 30) -> str:
        if not self.active_skill:
            return "[ERROR]当前没有激活的技能"
        if script_path.startswith("@skill/") or script_path.startswith("scripts/") or script_path.startswith("./scripts/"):
            script_path = script_path.removeprefix("@skill/")
            script_path = os.path.join(self.active_skill.path, script_path)
        else:
            script_path = os.path.join(self.active_skill.path, "scripts", script_path)
        if not os.path.exists(script_path):
            script_path = os.path.join(self.workspace_dir, script_path)
        if not os.path.exists(script_path):
            return f"[ERROR]脚本文件不存在: {script_path}"
        cmd = build_script_command(script_path, args or [])
        if not cmd:
            return f"[ERROR]不支持的脚本类型: {script_path}"
        try:
            cp_env = os.environ.copy()
            cp_env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                    timeout=timeout, encoding='utf-8', errors='replace',
                                    cwd=os.path.dirname(script_path), env=cp_env)
            if result.returncode == 0:
                return result.stdout if result.stdout else "(脚本执行成功，无输出)"
            else:
                return f"[ERROR]脚本执行失败 (退出码 {result.returncode}):\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return f"[ERROR]脚本执行超时（{timeout}秒）"
        except Exception as e:
            return f"[ERROR]执行脚本出错: {str(e)}"

    def insert_images(self, image_paths: typing.List[str]):
        self.img_path_list.clear()
        if not self.llm_client.support_multimodal:
            return "[ERROR]当前大模型不支持多模态，无法理解图片"
        for img_path in image_paths:
            if not img_path.startswith("http"):
                img_path = img_path.strip().strip("&").strip().strip('"').strip("'")
                if not os.path.isfile(img_path) \
                    or not img_path.lower().endswith(('.png', '.jpg', '.jpeg')):
                    continue
                if os.path.getsize(img_path) / 1024 > 1024 * 1:
                    continue
            self.img_path_list.append(img_path)
        return "图片已经插入, 请继续处理"

    def handle_skill_call(self, skill_name: str, user_request: str) -> str:
        skill = load_skill_full(skill_name, self.skills_dir)
        if not skill:
            return f"错误：技能 '{skill_name}' 不存在。"
        old_skill_desc = ""
        if self.active_skill:
            old_skill_desc = f"（之前激活的技能`{self.active_skill.name}`已替换）\n"
        self.active_skill = skill
        return f"""技能`{skill.name}`已激活。{old_skill_desc}以下是技能指令：
{skill.instruction}
---
现在请按照技能指令处理用户的请求：
{user_request}"""

    async def handle_mcp_call(self, server_name: str, user_request: str) -> str:
        query_servers = list(filter(
            lambda x: x.name.lower().strip() == server_name.lower().strip(), self.servers))
        if not query_servers:
            return f"⚠️ No such MCP server - {server_name} !"
        selected = query_servers[0]
        if selected in self.invalid_servers:
            return f"⚠️ MCP服务 [{server_name}] 不可用（初始化失败），无法激活！"
        self.active_mcp = selected
        return f"MCP服务`{server_name}`已激活，请立刻继续处理用户的请求：  \n{user_request}"

    async def process_mcp_tool(self, server: Server, tool_call: dict) -> str:
        if server in self.invalid_servers:
            return f"No server found with tool: {tool_call['name']}"
        try:
            start_time = asyncio.get_running_loop().time()
            result = await server.execute_tool(tool_call["name"], tool_call.get("arguments"))
            elapsed = asyncio.get_running_loop().time() - start_time
            if result.content and result.content[0].type == 'text':
                called_rst = result.content[0].text.strip()
                try:
                    data = json.loads(called_rst)
                    out_put = f"\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"
                except Exception:
                    out_put = f"\n{called_rst}"
            else:
                out_put = called_rst = f"{str(result.content).strip()}"
            status = "error" if result.isError else "success"
            self._emit(EventType.TOOL_RESULT, {
                "tool_name": tool_call["name"], "result": out_put,
                "status": status, "elapsed": f"{elapsed:.2f}"
            })
            return called_rst
        except Exception as e:
            self._emit(EventType.ERROR, {"message": str(e)})
            return f"Tool Execute Failed:\n{str(e)}"

    # ====================== Output ======================
    def _init_output_file(self):
        if self.output_file and not self.output_file.closed:
            self.output_file.close()
        out_path = os.path.join(self.outputs_dir, "log.md")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        self.output_file = open(out_path, mode="a+", encoding='utf-8')

    def append_to_output(self, role: str, info: str):
        if not self.output_file or self.output_file.closed:
            self._init_output_file()
        out = f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[{role}]:  \n{info}  \n\n"
        self.output_file.write(out)
        self.output_file.flush()

    # ====================== Message Sanitization ======================
    def _sanitize_messages(self, messages: list) -> list:
        if self.llm_client and self.llm_client.support_multimodal:
            return messages
        cleaned = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                cleaned.append({"role": msg["role"], "content": "\n".join(filter(None, text_parts))})
            else:
                cleaned.append(msg)
        return cleaned

    # ====================== Streaming Response ======================
    async def stream_response(self, use_tool_call=False, tools=None):
        """Stream LLM response, emitting events for UI updates."""
        src_response = ""
        tool_calls = []
        tool_args_str = ""
        reasoning_content = ""
        start_time = asyncio.get_running_loop().time()
        self._cancel_event.clear()

        async for code, chunk in self.llm_client.yield_response(
                self._sanitize_messages(self.messages), use_tool_call, tools, self._cancel_event):
            if self._cancel_event.is_set():
                break
            if code == 2:
                usage = chunk
                if usage and isinstance(usage, dict):
                    self.usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                    self.usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
                    self.usage["total_tokens"] += int(usage.get("total_tokens", 0))
            elif code == 31:
                if len(tool_calls) >= 1:
                    tool_calls[-1]["function"]["arguments"] = tool_args_str
                tool_calls.append(json.loads(chunk))
                tool_args_str = ""
            elif code == 32:
                tool_args_str += chunk
            elif code == 4:
                reasoning_content += chunk
                resp_lines = reasoning_content.splitlines()
                show_content = ("•••  \n" if len(resp_lines) > 10 else "") + "\n".join(resp_lines[-10:])
                self._emit(EventType.THINKING, {"content": show_content})
            else:
                src_response += chunk
                self._emit(EventType.STREAMING, {"content": chunk})

        elapsed = asyncio.get_running_loop().time() - start_time

        if self._cancel_event.is_set():
            src_response += "\n\n[*用户中断了大模型输出*]"
            tool_calls = []
            reasoning_content = ""
        elif len(tool_calls) >= 1:
            tool_calls[-1]["function"]["arguments"] = tool_args_str
        elif not src_response.strip():
            src_response = "[Error] 大模型没有进行任何回复，可能出现了异常，可重新开始新的对话"

        if reasoning_content:
            reasoning_content = "".join(reasoning_content[:500].splitlines())

        self._emit(EventType.STREAM_DONE, {
            "content": src_response.strip(), "tool_calls": tool_calls,
            "reasoning_content": reasoning_content, "elapsed": f"{elapsed:.2f}"
        })

        return src_response, tool_calls, reasoning_content

    async def get_response_blocking(self, use_tool_call=False, tools=None):
        """Non-streaming LLM response."""
        start_time = asyncio.get_running_loop().time()
        result, usage, tool_calls, reasoning_content = await self.llm_client.get_response(
            self._sanitize_messages(self.messages), use_tool_call, tools)
        elapsed = asyncio.get_running_loop().time() - start_time

        if usage:
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
            self.usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
            self.usage["total_tokens"] += int(usage.get("total_tokens", 0))

        show_result = result.strip() if result else "[Error] Received empty response from LLM"
        if reasoning_content:
            reasoning_content = "".join(reasoning_content[:500].splitlines())

        self._emit(EventType.STREAM_DONE, {
            "content": show_result, "tool_calls": tool_calls,
            "reasoning_content": reasoning_content, "elapsed": f"{elapsed:.2f}"
        })

        return result, tool_calls, reasoning_content

    # ====================== Main Processing ======================
    async def process_message(self, user_input: str, context: dict = None) -> str:
        """处理用户消息，返回最终响应。

        Args:
            user_input: 用户消息文本
            context: 可选的上下文信息，将作为 system 级补充注入
        """
        self._cancel_event.clear()

        # 注入上下文（来自工作流的上游结果等）
        if context:
            ctx_text = "## 工作流上下文\n"
            for k, v in context.items():
                ctx_text += f"- **{k}**: {v}\n"
            self.messages.append({"role": "system", "content": ctx_text})

        # 处理多模态
        if self.llm_client and self.llm_client.support_multimodal and self.img_path_list:
            user_content = [{"type": "text", "text": user_input}]
            for img_path in self.img_path_list:
                try:
                    img_b64 = image_to_base64(img_path, max_size_kb=1000)
                    user_content.append({"type": "image_url", "image_url": {"url": img_b64}})
                except Exception:
                    continue
            self.messages.append({"role": "user", "content": user_content})
            self.img_path_list.clear()
        else:
            self.messages.append({"role": "user", "content": user_input})

        self.append_to_output("user", user_input)

        # Agent mode with tool call loop
        support_tools = self._base_tools.copy()
        base_tools_count = len(self._base_tools)
        max_loop = TOOL_LOOP_MAX
        current_loop = 0

        while current_loop < max_loop:
            if self._cancel_event.is_set():
                self._emit(EventType.ERROR, {"message": "用户中断了Agent执行"})
                break
            current_loop += 1
            if current_loop > max_loop:
                self._emit(EventType.ERROR, {"message": f"Tool调用迭代次数超过上限：{TOOL_LOOP_MAX}!"})
                break

            # 重置扩展工具
            del support_tools[base_tools_count:]

            # 刷新 MCP 工具（system prompt 已静态化，无需重建）
            if self.active_skill or self.active_mcp:
                if self.active_mcp:
                    mcp_tools = [
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": f"[由MCP服务`{self.active_mcp.name}`提供]{tool.description}",
                                "parameters": {
                                    "type": tool.input_schema.get("type"),
                                    "properties": tool.input_schema.get("properties"),
                                    "required": tool.input_schema.get("required", [])
                                }
                            }
                        }
                        for tool in (await self.active_mcp.list_tools())
                    ]
                    support_tools.extend(mcp_tools)

            if self.llm_client and self.llm_client.support_stream:
                orig_response, tool_calls, reasoning = await self.stream_response(
                    use_tool_call=True, tools=support_tools)
            else:
                orig_response, tool_calls, reasoning = await self.get_response_blocking(
                    use_tool_call=True, tools=support_tools)

            # 多模态 image_url 错误处理
            if orig_response and 'invalid_request_error' in orig_response.lower() and 'unsupported image url' in orig_response.lower():
                for message in reversed(self.messages):
                    if message['role'] == 'user' and isinstance(message.get("content"), list):
                        if any(x.get("image_url") for x in message.get("content")):
                            message['content'] = "...请继续..."
                            break

            if self._cancel_event.is_set():
                self.messages.append({"role": "assistant", "content": "[用户中断了Agent执行]"})
                self._emit(EventType.ERROR, {"message": "用户中断了Agent执行"})
                break

            # 解析 tool_calls
            functions = []
            if tool_calls:
                fix_id_idx = 0
                for tc in tool_calls:
                    try:
                        if not tc.get("id") or tc["id"].strip() == "":
                            tc["id"] = f"{tc['function']['name']}-{fix_id_idx}"
                            fix_id_idx += 1
                        tool_id = tc["id"]
                        func_name = tc["function"]["name"]
                        func_args = json.loads(tc["function"]["arguments"])
                        functions.append({"id": tool_id, "name": func_name, "arguments": func_args})
                    except Exception:
                        continue

            if functions:
                tool_execute_list = functions
                self.messages.append({
                    "role": "assistant", "content": orig_response,
                    "tool_calls": tool_calls,
                    "reasoning_content": reasoning or "正在调用工具..."
                })
                self.append_to_output("assistant", f"**content**:{orig_response}  \n**tool_calls**:{tool_calls}")
            else:
                tool_execute_list = []
                self.messages.append({"role": "assistant", "content": orig_response})
                self.append_to_output("assistant", orig_response)

            if not tool_execute_list:
                break

            # 执行工具
            tool_results = []
            for tc in tool_execute_list:
                func_id = tc.get("id")
                func_name = tc["name"]
                func_args = tc["arguments"]
                self._emit(EventType.TOOL_CALL, {
                    "tool_name": func_name, "arguments": func_args, "status": "running"
                })

                handler = self.tool_handlers.get(func_name)
                if handler:
                    try:
                        if inspect.iscoroutinefunction(handler):
                            result = await handler(**func_args)
                        else:
                            result = handler(**func_args)
                    except Exception as e:
                        result = f"工具执行出错: {str(e)}"
                    self._emit(EventType.TOOL_RESULT, {
                        "tool_name": func_name, "result": str(result)[:500], "status": "success"
                    })
                elif self.active_mcp and func_name in [
                    tool.name for tool in (await self.active_mcp.list_tools())]:
                    result = await self.process_mcp_tool(
                        self.active_mcp, {"name": func_name, "arguments": func_args})
                else:
                    result = f"未知工具: {func_name}"
                    self._emit(EventType.ERROR, {"message": result})

                tool_results.append({"tool_call_id": func_id, "tool_name": func_name, "content": result})

            is_insert_images = False
            for tr in tool_results:
                if not tr["tool_call_id"]:
                    continue
                self.messages.append({
                    "role": "tool", "tool_call_id": tr["tool_call_id"],
                    "content": str(tr['content'])
                })
                self.append_to_output("tool", f"**tool_call_id**:{tr['tool_call_id']}  \n**content**:{tr['content']}")
                if tr['tool_name'] == 'insert_images' and self.img_path_list:
                    is_insert_images = True

            if is_insert_images and self.llm_client and self.llm_client.support_multimodal and self.img_path_list:
                user_content = [{"type": "text", "text": "请继续处理提供的图片"}]
                for img_path in self.img_path_list:
                    try:
                        img_b64 = image_to_base64(img_path, max_size_kb=1000)
                        user_content.append({"type": "image_url", "image_url": {"url": img_b64}})
                    except Exception:
                        continue
                self.messages.append({"role": "user", "content": user_content})
                self.img_path_list.clear()


        self._emit(EventType.AGENT_LOOP_DONE)
        return orig_response if 'orig_response' in locals() else ""

    def get_info_text(self) -> str:
        model = self.llm_client.ai_model if self.llm_client else "No model"
        return f"{model} | AGENT"
