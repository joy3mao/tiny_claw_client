# coding:utf-8
"""Tiny Claw UI - Core logic module (adapted from tiny_claw_client.py)"""
import warnings; warnings.filterwarnings("ignore", category=UserWarning)
import asyncio, json, typing, os, re, shutil, math, uuid, inspect, traceback, httpx
from datetime import datetime
from contextlib import AsyncExitStack
from typing import Any, Dict, Optional, List, Union
from dataclasses import dataclass, field
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent, PromptArgument, GetPromptResult, PromptMessage
from fastmcp.client.auth.oauth import OAuth
import subprocess, platform, sys
import base64
from PIL import Image
from io import BytesIO
from enum import Enum
import locale

# ====================== Constants ======================
# Resolve paths correctly when frozen (PyInstaller) or running from source
_RES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Tiny Claw/ project root
_DATA_DIR = _RES_DIR
# Editable user data lives in _DATA_DIR (writable even when frozen)
SKILLS_DIR = os.path.join(_DATA_DIR, "skills")
FLOWS_DIR = os.path.join(_DATA_DIR, "flows")
WORKSPACE_DIR = os.path.join(_DATA_DIR, "tiny_claw_workspace")
_PROJECT_DIR = _RES_DIR
os.makedirs(WORKSPACE_DIR, exist_ok=True)  # ensure workspace exists

# ====================== Image Helper ======================
def image_to_base64(image_path,max_size_kb):
    """将图片文件转换为base64编码的字符串，无异常处理！ß"""

    # 1. 获取图片原始字节数据
    if image_path.startswith("http://") or image_path.startswith("https://"):
        with httpx.Client() as client:
            response = client.get(image_path)
            response.raise_for_status()
            raw_data = response.content
    else:
        with open(image_path, 'rb') as image_file:
            raw_data = image_file.read()

    img_size_kb = len(raw_data) / 1024
    if img_size_kb<=max_size_kb:
        encoded_string = base64.b64encode(raw_data).decode('utf-8')
        return f"data:image/png;base64,{encoded_string}"
    # 2. 不需要压缩，直接编码返回
    img = Image.open(BytesIO(raw_data))
    fmt = img.format
    if fmt == 'JPEG' or fmt == 'JPG':
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
    elif fmt == 'PNG':
        if img.mode == 'P':
            img = img.convert('RGBA')
    # 3. 需要压缩
    buf = BytesIO()
    save_fmt = 'JPEG' if fmt in ('JPEG', 'JPG') else 'PNG'
    img.save(buf, format=save_fmt, quality=int(img_size_kb/max_size_kb*100), optimize=True)
    size = buf.tell()
    while size > max_size_kb * 1024:
        w, h = img.size
        img = img.resize((int(w*0.8), int(h*0.8)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format=save_fmt, quality=80, optimize=True)
        size = buf.tell()
    encoded_string = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded_string}"

# ====================== Base Tools ======================
base_tools = [
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
                "required": ["command","command_type"]
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
        "function":{
            "name": "insert_images",
            "description": "给当前对话加入图片路径列表，用于大模型理解",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_paths": {
                        "type": "array",
                        "description": "图片路径（可以是url、本地文件路径）",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": ["image_paths"]
            }
        }
    },{
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
                        "description": "直接列出的各项选择，每项应清晰说明该选项的含义，如 [\"继续执行当前任务\", \"跳过此步骤，进入下一步\", \"中止整个流程\"]"
                    }
                },
                "required": ["choices"]
            }
        }
    },
]

web_search_tool = {
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

update_task_step = {
    "type": "function",
    "function": {
        "name": "update_task_step",
        "description": "更新任务步骤的执行状态。每完成或失败一个步骤时调用，用于标记进度。",
        "parameters": {
            "type": "object",
            "properties": {
                "step_no": {"type": "number", "description": "任务步骤序号"},
                "new_status": {"type": "string", "description": "更新步骤状态，\"1\":进行中、\"2\":失败、\"3\":完成"},
                "step_result": {"type": "string", "description": "更新步骤执行成功或失败结果"}
            },
            "required": ["step_no", "new_status", "step_result"]
        }
    }
}

gen_task_step = {
    "type": "function",
    "function": {
        "name": "gen_task_step",
        "description": "分析用户的任务需求，生成完整的、可执行的任务步骤列表。必须在任务开始前调用此工具来规划步骤。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_steps": {
                    "type": "array",
                    "description": "任务步骤列表，按执行顺序排列",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_no": {"type": "number", "description": "步骤序号，新任务从1开始递增"},
                            "step_name": {"type": "string", "description": "步骤名称（简洁概括）"},
                            "step_desc": {"type": "string", "description": "步骤详细描述，说明这一步要做什么、需要用什么工具/方法"}
                        },
                        "required": ["step_no", "step_name", "step_desc"]
                    }
                }
            },
            "required": ["task_steps"]
        }
    }
}

get_task_process = {
    "type": "function",
    "function": {
        "name": "get_task_process",
        "description": "获取任务当前步骤的执行进度。当不知或不确认当前进度时调用。"
    }
}

pre_work_done = {
    "type": "function",
    "function": {
        "name": "pre_work_done",
        "description": "用于批量任务中标记前置工作已完成"
    }
}

current_task_done = {
    "type": "function",
    "function": {
        "name": "current_task_done",
        "description": "用于批量任务中标记当前任务已完成"
    }
}


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
    TASK_STEPS_UPDATED = "task_steps_updated"
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


def load_skills_metadata() -> List[Dict[str, str]]:
    skills_meta = []
    if not os.path.isdir(SKILLS_DIR):
        return skills_meta
    for skill_name in os.listdir(SKILLS_DIR):
        if skill_name.startswith("."): # 跳过隐藏的SKILL文件夹
            continue
        skill_path = os.path.join(SKILLS_DIR, skill_name)
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


def load_skill_full(skill_name: str) -> Optional[Skill]:
    skill_path = os.path.join(SKILLS_DIR, skill_name)
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


def load_flows_metadata() -> List[Dict[str, str]]:
    """加载 flows/ 目录下所有 agent_flow YAML 的元数据"""
    flows_meta = []
    if not os.path.isdir(FLOWS_DIR):
        return flows_meta
    for fname in os.listdir(FLOWS_DIR):
        if fname.startswith(".") or not (fname.endswith(".yaml") or fname.endswith(".yml")):
            continue
        flow_path = os.path.join(FLOWS_DIR, fname)
        if not os.path.isfile(flow_path):
            continue
        try:
            import yaml as _yaml
            with open(flow_path, "r", encoding="utf-8") as f:
                raw = _yaml.safe_load(f)
            flow = raw.get("flow", raw) if raw else {}
            flow_name = fname.rsplit(".", 1)[0]  # 文件名去掉扩展名作为 key
            flows_meta.append({
                "name": flow_name,
                "title": flow.get("name", flow_name),
                "description": flow.get("description", ""),
                "path": flow_path,
            })
        except Exception:
            pass
    return flows_meta


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
        if is_windows:
            return f'bash "{script_path}" {args_str}'
        else:
            return f'bash "{script_path}" {args_str}'
    elif ext == ".js":
        return f'node "{script_path}" {args_str}'
    elif ext == ".exe":
        if not is_windows:
            return None
        return f'"{script_path}" {args_str}'
    else:
        return None


# ====================== Configuration ======================
class Configuration:
    @staticmethod
    def load_env() -> None:
        # Prefer writable _DATA_DIR copy; fall back to bundled _RES_DIR
        dotenv_path = os.path.join(_DATA_DIR, ".env")
        if not os.path.isfile(dotenv_path):
            dotenv_path = os.path.join(_RES_DIR, ".env")
        if os.path.isfile(dotenv_path):
            load_dotenv(dotenv_path=dotenv_path)
        else:
            load_dotenv()

    @staticmethod
    def load_config(file_path: str) -> Dict[str, Any]:
        if not os.path.isabs(file_path):
            # Prefer writable _DATA_DIR copy; fall back to bundled _RES_DIR
            data_copy = os.path.join(_DATA_DIR, file_path)
            if os.path.isfile(data_copy):
                file_path = data_copy
            else:
                file_path = os.path.join(_RES_DIR, file_path)
        with open(file_path, "r", encoding='utf-8') as f:
            return json.load(f)


config = Configuration()


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

    async def get_response(self, messages: list, use_tool_call=False, tools=base_tools) -> tuple:
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
        if self.support_tool_call and use_tool_call:
            payload["tools"] = tools
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            else:
                payload["thinking"] = {"type": "disabled"}

        try:
            async with httpx.AsyncClient(proxy=self.http_proxy) as client:
                response = await client.post(self.ai_api_url, headers=headers, json=payload, timeout=360)
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

    async def yield_response(self, messages: list, use_tool_call=False, tools=base_tools,
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
        if self.support_tool_call and use_tool_call:
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
                                         headers=headers, json=payload, timeout=360) as response:
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


class MCPPrompt:
    def __init__(self, name: str, description: str, arguments: list,
                 server_name: str = None) -> None:
        self.name = name
        self.server_name = server_name
        self.description = description
        self.arguments = arguments


class Server:
    def __init__(self, name: str, config: dict) -> None:
        self.name = name
        self.config = config
        self.stdio_context: Any | None = None
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack = AsyncExitStack()
        self.capabilities = set()
        self._transport_gen: Any | None = None  # anyio async generator, managed manually

    @staticmethod
    async def _close_transport(gen: Any) -> None:
        """Close the anyio transport generator safely, suppressing task-context errors."""
        try:
            await gen.__aexit__(None, None, None)
        except (RuntimeError, asyncio.CancelledError, GeneratorExit):
            pass  # anyio cancel scope / task context errors during shutdown
        except Exception:
            pass  # other runtime errors
        except BaseException:
            pass  # emergency catch-all

    async def initialize(self) -> None:
        """Initialize the MCP server: open transport + create session.
        
        The entire process is wrapped in a single try/except so that ANY failure
        (transport or session) triggers full cleanup — preventing resource leaks
        that could cascade and break subsequent server initializations.
        """
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
                    raise ValueError("streamable-http need url and the url is should end with /mcp")
                if self.config.get("headers") or self.config.get("oauth"):
                    headers = self.config.get("headers")
                    oauth_provider = None
                    if self.config.get("oauth"):
                        oauth_provider = OAuth(
                            mcp_url=streamableHttpUrl,
                            scopes=["openid", "profile"]
                        )
                    async_httpx_client = httpx.AsyncClient(headers=headers, timeout=300, auth=oauth_provider)
                    self._transport_gen = streamable_http_client(streamableHttpUrl, http_client=async_httpx_client)
                else:
                    self._transport_gen = streamable_http_client(streamableHttpUrl)
                read, write, getSessionIdCallback = await self._transport_gen.__aenter__()
            else:
                raise ValueError("The transport must be stdio/sse/streamable-http.")
            # --- ClientSession ---
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            init_resp = await session.initialize()
            if init_resp.capabilities.prompts:
                self.capabilities.add("prompts")
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
        except:
            await self.cleanup()
            return None

    async def cleanup(self) -> None:
        """Safely tear down transport generator first, then exit stack."""
        # 1. Close the anyio-based transport generator first (most fragile)
        if self._transport_gen is not None:
            try:
                await self._close_transport(self._transport_gen)
            except BaseException:
                pass
            self._transport_gen = None
        # 2. Close ClientSession via exit stack
        if self.exit_stack is not None:
            try:
                await self.exit_stack.aclose()
            except (RuntimeError, asyncio.CancelledError, GeneratorExit):
                pass  # anyio cancel scope / task context errors during shutdown
            except Exception:
                pass  # other runtime errors
            except BaseException:
                pass  # catch anything else during emergency cleanup
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

    async def list_prompts(self) -> list:
        if not self.session:
            raise RuntimeError(f"Server {self.name} not initialized")
        if "prompts" not in self.capabilities:
            return []
        try:
            prompts_response = await self.session.list_prompts()
            if not prompts_response:
                return []
            prompts = []
            for item in prompts_response:
                if isinstance(item, tuple) and item[0] == "prompts":
                    prompts.extend(
                        MCPPrompt(prompt.name, prompt.description, prompt.arguments,
                                  server_name=self.name)
                        for prompt in item[1]
                    )
            return prompts
        except Exception:
            return []

    async def get_prompt(self, prompt_name: str, arguments: dict) -> GetPromptResult | None:
        try:
            if not self.session:
                raise RuntimeError(f"Server {self.name} not initialized")
            result = await self.session.get_prompt(prompt_name, arguments)
            return result
        except Exception:
            return None


# ====================== Chat Session (Core Logic) ======================
class ChatSession:
    def __init__(self, event_callback=None):
        self.servers: list[Server] = []
        self.invalid_servers: set[Server] = set()
        self.llm_client: LLMClient | None = None
        self.usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.log_file = None
        self.messages = []
        self.agent_switch = 0
        self.agent_task_batch_switch = 0  # 批量任务开关 0：关闭，1：开启
        self.agent_batch_pre_work_msg_len = 1  # 批量任务消息体长度（前置工作完成时的索引）
        self.task_steps = []  # agent_task模式步骤列表: [{"step_no":1,"step_name":"...","step_desc":"...","status":"pending|done|failed","result":""}]
        self.skills_meta = []
        self.active_skill: Optional[Skill] = None
        self.active_mcp: Optional[Server] = None
        self.base_configs = {}
        self.client_models = {}
        self.mcp_when_to_use = ""
        self.markdown_theme = "dracula"
        self._cancel_event = asyncio.Event()
        self._event_callback = event_callback
        self._dangerous_command_event = asyncio.Event()
        self._dangerous_command_confirmed = False
        self._user_choice_event = asyncio.Event()
        self._user_choice_result = None
        self._batch_cleanup_pending = False
        self.img_path_list = []

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
            "gen_task_step": self.handle_gen_task_step,
            "update_task_step": self.handle_update_task_step,
            "get_task_process": lambda: self._gen_task_progress_md(),
            "pre_work_done": self.handle_pre_work_done,
            "current_task_done": self.handle_current_task_done,
            "ask_user": self.handle_ask_user,
        }

        try:
            self.base_configs = config.load_config("configs.json")
        except Exception:
            self.base_configs = {"llm_models": [], "mcp_servers": [], "search_switch": {}}

        if not self.base_configs.get("web_search", {}).get("disabled", True):
            self.tool_handlers["web_search"] = lambda **kw: self.web_search(kw["query"])
            if web_search_tool not in base_tools:
                base_tools.append(web_search_tool)

        self.client_models = {}
        model_no = 1
        for model in self.base_configs.get("llm_models", []):
            if model.get("disabled"):
                continue
            self.client_models[str(model_no)] = model
            model_no += 1

    def _emit(self, event_type: EventType, data=None, **extra):
        if self._event_callback:
            self._event_callback(SessionEvent(type=event_type, data=data, extra=extra))

    def cancel_current(self):
        self._cancel_event.set()

    def confirm_dangerous_command(self, confirmed: bool):
        """Called by UI to respond to a dangerous command confirmation prompt."""
        self._dangerous_command_confirmed = confirmed
        self._dangerous_command_event.set()

    def answer_user_choice(self, result: str):
        """Called by UI to respond to an ask_user choice prompt."""
        self._user_choice_result = result
        self._user_choice_event.set()

    # ====================== Base Tools ======================
    async def execute_bash(self, command: str, command_type: str = "bash") -> str:
        """
        执行 shell 命令（经过安全过滤）。
        Windows 固定使用 PowerShell，Linux/macOS 使用 bash。
        """
        is_windows = platform.system().lower() == "windows"

        if not command.strip():
            return "[ERROR]命令不能为空"

        if is_windows:
            if command_type == "bash":
                return "[ERROR]Windows只支持powershell类型"
            # ===== Windows：只用 PowerShell =====
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
                r"new-itemproperty.*hk",r"set-executionpolicy",
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
                self._dangerous_command_event.clear()
                self._dangerous_command_confirmed = False
                self._emit(EventType.DANGEROUS_COMMAND, {
                    "command": command,
                    "patterns": matched_patterns,
                })
                await self._dangerous_command_event.wait()
                if not self._dangerous_command_confirmed:
                    return "[ERROR]PowerShell 命令包含潜在危险操作，已被拒绝。"

            # 自动补全 powershell 前缀
            if not command.lower().startswith("powershell "):
                # 转义命令中的双引号，防止PowerShell解析错误
                escaped_command = command.replace('"', '\\"')
                command = f'powershell -Command "{escaped_command}"'
        else:
            # ===== Linux/macOS：使用 bash =====
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
                self._dangerous_command_event.clear()
                self._dangerous_command_confirmed = False
                self._emit(EventType.DANGEROUS_COMMAND, {
                    "command": command,
                    "patterns": matched_patterns,
                })
                await self._dangerous_command_event.wait()
                if not self._dangerous_command_confirmed:
                    return "[ERROR]禁止执行该命令"

        try:
            cp_env = os.environ.copy()
            # 设置正确的编码以兼容不同操作系统
            cp_env["PYTHONIOENCODING"] = "utf-8"

            # 获取系统首选编码，Mac和Linux通常是utf-8，Windows可能是其他编码
            system_encoding = locale.getpreferredencoding(False)

            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=600, encoding=system_encoding, errors="replace",
                cwd=os.path.dirname(WORKSPACE_DIR), env=cp_env
            )
            if result.returncode == 0:
                return result.stdout if result.stdout else "(命令执行成功，无输出)"
            else:
                return f"[ERROR]命令执行失败 (退出码 {result.returncode}):\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return "[ERROR]命令执行超时（600秒）"
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
            search_key = self.base_configs.get("web_search", {}).get("api_key", "**********")
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
            if not path.startswith(WORKSPACE_DIR) and not path.startswith(WORKSPACE_DIR.removeprefix("./")):
                path = os.path.join(WORKSPACE_DIR, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, mode="w", encoding="utf-8") as f:
                f.write(content)
            return f"文件保存成功，文件路径:{file_path}"
        except Exception as e:
            return f"[ERROR]文件保存失败：{e}"

    def edit_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        if not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            source_file_path = os.path.abspath(path)
        else:
            source_file_path = path.removeprefix("file:///")
        if not os.path.exists(source_file_path):
            return f"[ERROR]源文件不存在: {source_file_path}"
        try:
            file_name = os.path.basename(source_file_path)
            file_size = os.path.getsize(source_file_path)
            if file_size > 1024 * 1024 * 5:
                return f"[ERROR]文件过大，无法进行文本替换，文件大小: {file_size / 1024 / 1024} MB"
            with open(source_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if old_text not in content:
                return f"[WARNING]未找到需要替换的文本内容"
            backup_file_path = os.path.join(os.path.dirname(source_file_path), f"{file_name}.backup")
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
            return f"文件({source_file_path})备份，并成功替换了 {count} 处文本内容"
        except Exception as e:
            return f"[ERROR]文件替换失败：{e}"

    def append_file(self, path: str, content: str) -> str:
        if not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path
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
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            file_size = os.path.getsize(file_path)
            if file_size > 1024 * 1024 * 2:
                return f"[ERROR]文件过大，无法在指定行号后插入信息，文件大小: {file_size / 1024 / 1024} MB"
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if isinstance(line_number, str):
                try:
                    line_number = int(line_number)
                except ValueError:
                    return f"[ERROR]无效行号: {line_number}"
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
            return f"[ERROR]此路径为网页：{file_path}，无法直接读取"
        path = os.path.expanduser(path)
        if path.startswith("@skill/") and self.active_skill:
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
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
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
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
        if script_path.startswith("@skill/") or script_path.startswith("scripts/") or script_path.startswith("./scripts/") or script_path.startswith(".\\scripts\\"):
            script_path = script_path.removeprefix("@skill/")
            script_path = os.path.join(self.active_skill.path, script_path)
        else:
            script_path = os.path.join(self.active_skill.path, "scripts", script_path)
        if not os.path.exists(script_path): # 容错查找
            script_path = os.path.join(WORKSPACE_DIR, script_path)
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
            stdout = result.stdout
            stderr = result.stderr
            if result.returncode == 0:
                return stdout if stdout else "(脚本执行成功，无输出)"
            else:
                return f"[ERROR]脚本执行失败 (退出码 {result.returncode}):\n{stderr}"
        except subprocess.TimeoutExpired:
            return f"[ERROR]脚本执行超时（{timeout}秒）"
        except Exception as e:
            return f"[ERROR]执行脚本出错: {str(e)}"

    def insert_images(self,image_paths:typing.List[str]):
        """插入图片路径到当前会话"""
        self.img_path_list.clear()
        if not self.llm_client.support_multimodal:
            return "[ERROR]当前大模型不支持多模态，无法理解图片"
        for img_path in image_paths:
            if not img_path.startswith("http"):
                img_path = img_path.strip().strip("&").strip().strip('"').strip("'")
                if not os.path.isfile(img_path) \
                    or not img_path.lower().endswith(('.png', '.jpg', '.jpeg')):
                    error_console.print(" ❌ 文件不存在或格式不支持(仅png/jpg/jpeg)...")
                    continue
                if os.path.getsize(img_path) / 1024 > 1024*1:
                    error_console.print(" ❌ 文件太大了(max 1MB)...")
                    continue
            self.img_path_list.append(img_path)
        return "图片已经插入, 请继续处理"

    # ====================== Agent-Task Methods ======================
    def _gen_task_progress_md(self) -> str:
        """生成当前任务进度的Markdown格式化文本"""
        if not self.task_steps:
            return "## 当前任务进度\n\n> 尚未规划任务步骤，请先调用 `gen_task_step` 生成步骤。\n"

        total = len(self.task_steps)
        done = sum(1 for s in self.task_steps if s.get("status") == "done")
        failed = sum(1 for s in self.task_steps if s.get("status") == "failed")

        lines = [f"## 当前任务进度 ({done}/{total} 已完成" + (f", {failed} 失败)" if failed > 0 else ")") + "\n"]
        lines.append("| # | 任务描述 | 状态 | 执行结果 |")
        lines.append("|---|----------|------|----------|")

        def _cell(v):
            """转义表格单元格内容：去掉 | 和换行，截断到 200 字符"""
            s = str(v)[:200]
            s = s.replace("|", "／").replace("\n", " ").replace("\r", "")
            return s

        status_text = {"pending": "📋 待办","ongoing": "🔄 进行", "done": "✅ 完成", "failed": "❌ 失败"}
        c = 0
        for step in self.task_steps:
            c += 1
            no = step.get("step_no", c)
            status = status_text.get(step.get("status", "pending"), "待执行")
            desc = f"{step.get('step_name', '')}: {step.get('step_desc', '')}"
            result = step.get("result", "") or "-"
            lines.append(f"| {no} | {_cell(desc)} | {status} | {_cell(result)} |")

        return "\n".join(lines) + "\n"

    async def handle_gen_task_step(self, task_steps: list) -> str:
        """处理 gen_task_step 工具调用：接收并存储任务步骤列表"""
        if not task_steps or not isinstance(task_steps, list):
            return "[错误] task_steps 必须是非空数组"

        try:
            task_steps_1st_no = int(task_steps[0].get("step_no", 1))
        except (TypeError, ValueError):
            task_steps_1st_no = 1

        if task_steps_1st_no == 1:
            # 如果所有旧步骤都已完成/失败，说明是新任务，全部清空
            if self.task_steps and all(s.get("status") in ("done", "failed") for s in self.task_steps):
                self.task_steps = []
            elif self.task_steps:
                # 执行过程中 LLM 重新生成全部步骤，也清空重新开始
                self.task_steps = []
        else:
            if len(self.task_steps) >= task_steps_1st_no:
                del self.task_steps[task_steps_1st_no - 1:]

        for step in task_steps:
            self.task_steps.append({
                "step_no": step.get("step_no", len(self.task_steps) + 1),
                "step_name": step.get("step_name", f"步骤{step.get('step_no', '?')}"),
                "step_desc": step.get("step_desc", ""),
                "status": "pending",
                "result": ""
            })

        steps_summary = "\n".join([
            f"  {s['step_no']}. [{s['step_name']}] {s['step_desc']}"
            for s in self.task_steps
        ])
        self._emit(EventType.TASK_STEPS_UPDATED)
        return f"✅ 已生成 {len(self.task_steps)} 个任务步骤：\n{steps_summary}\n\n请按照步骤顺序逐一执行，每完成一步调用 `update_task_step` 更新状态。"

    async def handle_update_task_step(self, step_no: int, new_status: str, step_result: str = "") -> str:
        """处理 update_task_step 工具调用：更新步骤状态并刷新系统提示词"""
        target_step = None
        for step in self.task_steps:
            if step["step_no"] == step_no:
                target_step = step
                break

        if not target_step:
            return f"[错误] 未找到步骤序号 {step_no}，当前步骤列表共 {len(self.task_steps)} 步。"

        if new_status == "1":
            target_step["status"] = "ongoing"
        elif new_status == "2":
            target_step["status"] = "failed"
        elif new_status == "3":
            target_step["status"] = "done"
        else:
            target_step["status"] = "pending"

        target_step["result"] = step_result or ""

        total = len(self.task_steps)
        done = sum(1 for s in self.task_steps if s["status"] == "done")
        failed = sum(1 for s in self.task_steps if s["status"] == "failed")

        status_text = "完成" if new_status == "3" else "失败" if new_status == "2" else "进行" if new_status == "1" else "待执行"
        result = f"步骤 {step_no}「{target_step['step_name']}」已标记为：{status_text}。"
        if step_result:
            result += f" 执行结果：{step_result}"
        # 简略进度：只给分数 + 下一步，需要完整表时调用 get_task_process
        next_step = next((s for s in self.task_steps if s["status"] == "pending"), None)
        result += f"\n📊 进度：{done}/{total} 已完成"
        if failed > 0:
            result += f"，{failed} 失败"
        if next_step:
            result += f"  \n→ 下一步：步骤{next_step['step_no']}「{next_step['step_name']}」"
        if done + failed >= total:
            if self.agent_task_batch_switch == 0:
                result += "\n🎉 所有步骤已执行完毕！请向用户汇报最终结果。"
            self.task_steps = []

        self._emit(EventType.TASK_STEPS_UPDATED)
        return result

    def handle_pre_work_done(self) -> str:
        """处理批量任务中，标记前置工作已完成"""
        self.agent_batch_pre_work_msg_len = len(self.messages)
        return "...前置工作已完成，开始批量任务..."

    def handle_current_task_done(self) -> str:
        """处理批量任务中，标记当前任务已经完成"""
        self._batch_cleanup_pending = True
        return "...已完成一个任务，开始下一个..."

    async def handle_ask_user(self, choices: list) -> str:
        """向用户展示各项选择，等待用户选择后返回结果。"""
        if not choices:
            return "[ERROR] 选项列表不能为空"
        self._user_choice_event.clear()
        self._user_choice_result = None
        self._emit(EventType.USER_CHOICE, {
            "choices": choices,
        })
        await self._user_choice_event.wait()
        if self._user_choice_result is None:
            return "[用户取消了选择]"
        return self._user_choice_result

    # ====================== System Prompt Generation ======================
    async def gen_agent_system_content(self):
        """ 生成Agent的系统信息 """
        user_memory = "[暂无]"
        memory_path = os.path.join(WORKSPACE_DIR, "AGENT.md")
        if os.path.exists(memory_path):
            with open(memory_path, "r", encoding="utf-8") as f:
                user_memory = f.read()

        # 技能列表   
        skills_list = "\n".join(
            [f"- {s['name']}: {s['description']}" for s in self.skills_meta])


        # Agent-Task 模式：任务规划指引（静态，不含进度）
        task_mode = ""
        if self.agent_switch == 2:
            task_mode = """
## Agent任务模式
你正在以「任务规划+分步执行」模式工作。请遵循以下流程：

### 阶段一：任务规划
1. 分析用户的任务需求+读取需要的信息，制定执行计划，拆解为可执行的步骤
2. 调用 `gen_task_step` 工具生成完整的任务步骤列表
3. 步骤应具体、可执行，每步说明需要用到的工具或方法
4. 如果某步骤复杂，将其拆分为多个步骤

### 阶段二：逐步执行
1. 按照步骤顺序，从第1步开始逐一执行
2. 每开始执行一个步骤，先调用 `update_task_step` 标记该步骤状态为1（进行中)
3. 每完成一步，立即调用 `update_task_step` 标记该步骤状态（2=失败，3=成功）
 - 执行过程中如发现需要调整步骤或拆分细化步骤，调用 `gen_task_step` 更新步骤列表：任务编号从调整位置编号开始！
 - 执行失败，根据失败原因重新尝试，失败次数不得超过限制(默认3次)，否认需用户介入
4. 所有步骤完成后，向用户汇报执行结果

### 确认当前步骤
当不知或不确认当前进度时，调用`get_task_process`获取当前任务进度
"""
        system_prompts = f"""## 角色定义      
你是一个智能助手（风格：不装、说干就干、基于现有数据、绝不捏造信息）, 运行的系统为{platform.system()}，可以根据用户需求进行回答或调用工具/技能/MCP服务。

## 核心工作
根据用户的问题、用户的技能工具使用习惯(如果有)，从提供的工具列表中选择合适的工具、技能或MCP服务进行调用。  
 - 如果无需调用，或无匹配工具、技能或MCP服务情况，直接根据用户问题进行回答即可。
 - 如果你没有需查询的信息或非最新，请调用web_search工具(如果提供)。

## 当前用户技能工具使用习惯
{user_memory}

## 可用技能
{skills_list}

### 技能激活
当你调用 skill_call 后，你会获得该技能的详细指令。之后处理该任务时，请遵循技能指令。  
如果激活的技能说明中有参考文件，请根据必要使用`read_file`读取文件内容作为参考:  
    - 参考文件路径若以`@skill/`开头,如`@skill/reference.md`等，**以此直接**作为技能的参考文件路径(“读取文件”工具会自动将@skill替换为当前skill目录路径)。
    - 否则根据**技能路径**及**参考文件路径**获取其参考文件的完整路径

## 可用MCP服务
{self.mcp_when_to_use}

### MCP服务激活
当用户需求匹配某个MCP服务时，需要判断：提供的tools列表中是否包含此MCP服务提供的工具（MCP工具描述开头大致为“[由MCP服务`xxx`提供]”）？  
    - 不包含：那么调用`mcp_call`激活该MCP服务，这样提供的tools列表中将增加此mcp服务提供的工具。  
    - 包含：不要重复激活，直接调用对应工具  
> MCP服务没有匹配功能时，不要使用。

{task_mode}  
--- 
现在请开始处理用户的问题。[今天:{datetime.now().strftime('%Y-%m-%d')}][默认工作目录:{WORKSPACE_DIR}]
"""
        return system_prompts

    def gen_chat_system_content(self):
        return f"你是一个智能助手，根据用户的提问，直接、正确、绝不捏造信息地回答用户问题(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    # ====================== Server Management ======================
    async def _init_one_server(self, server: Server) -> None:
        """Initialize one MCP server inside an isolated asyncio Task.

        Each server init runs in its own Task with a 30s timeout so that a
        corrupted anyio cancel scope from a prior failure cannot propagate.
        """
        task = asyncio.ensure_future(server.initialize())
        try:
            await asyncio.wait_for(task, timeout=30)
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            raise

    async def initialize_servers(self) -> None:
        self.servers = [
            Server(name, srv_config)
            for name, srv_config in self.base_configs.get("mcp_servers", {}).items()
            if not srv_config.get("disabled")
        ]
        for server in self.servers:
            try:
                await self._init_one_server(server)
            except BaseException as e:
                self.invalid_servers.add(server)
                self._emit(EventType.ERROR, {"message": f"MCP服务 [{server.name}] 初始化失败: {e}"})
                # Ensure cleanup completes + OS reaps the subprocess before next server
                try:
                    await server.cleanup()
                except BaseException:
                    pass
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0)

    async def reinitialize_servers(self, new_servers: list[Server]) -> None:
        await self.cleanup_servers()
        self.servers = new_servers
        for server in self.servers:
            try:
                server.exit_stack = AsyncExitStack()
                await self._init_one_server(server)
            except BaseException as e:
                self.invalid_servers.add(server)
                self._emit(EventType.ERROR, {"message": f"MCP服务 [{server.name}] 重新初始化失败: {e}"})
                try:
                    await server.cleanup()
                except BaseException:
                    pass
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0)

    async def cleanup_servers(self) -> None:
        all_servers = list(self.servers) + list(self.invalid_servers)
        for server in reversed(all_servers):
            try:
                await server.cleanup()
            except Exception:
                pass
        self.servers.clear()
        self.invalid_servers.clear()

    # ====================== Model Management ======================
    def switch_model(self, model_no: str):
        model_info = self.client_models.get(model_no)
        if not model_info:
            return False
        if model_info["api_style"].lower() == "openai":
            self.llm_client = LLMClient(
                api_key=model_info["api_key"],
                ai_channel=model_info["ai_channel"],
                ai_model=model_info["ai_model"],
                ai_api_url=model_info["ai_api_url"],
                ai_provider=model_info["ai_provider"],
                support_tool_call=model_info["support_tool_call"],
                support_stream=model_info["support_stream"],
                support_thinking=model_info["support_thinking"],
                support_multimodal=model_info.get("support_multimodal", False),
                http_proxy=model_info["api_proxy"]
            )
        else:
            return False
        return True

    def get_model_info_text(self) -> str:
        if not self.llm_client:
            return "No model selected"
        if self.agent_switch == 2:
            agent_status = "AGENT TASK"
        elif self.agent_switch == 1:
            agent_status = "AGENT ON"
        else:
            agent_status = "AGENT OFF"
        return (f"{self.llm_client.ai_model} | "
                f"{agent_status}")

    # ====================== Skill/MCP Handlers ======================
    def handle_skill_call(self, skill_name: str, user_request: str) -> str:
        skill = load_skill_full(skill_name)
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

    async def process_mcp_response(self, server: Server, tool_call: dict) -> str:
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
                "tool_name": tool_call["name"],
                "result": out_put,
                "status": status,
                "elapsed": f"{elapsed:.2f}"
            })
            return called_rst
        except Exception as e:
            self._emit(EventType.ERROR, {"message": str(e)})
            return f"Tool Execute Failed:\n{str(e)}"

    # ====================== Logging ======================
    def creat_new_log(self):
        if self.log_file and not self.log_file.closed:
            self.log_file.close()
        formatted_time = datetime.now().strftime('%Y%m%d')
        new_log_file_path = os.path.join(_DATA_DIR, "logs", f"{formatted_time}.md")
        if not os.path.exists(new_log_file_path):
            os.makedirs(os.path.dirname(new_log_file_path), exist_ok=True)
        self.log_file = open(new_log_file_path, mode="a+", encoding='utf-8')

    def append_info_to_log(self, role: str, info: str):
        expected_name = os.path.join(_DATA_DIR, "logs", f"{datetime.now().strftime('%Y%m%d')}.md")
        if self.log_file.name != expected_name:
            self.creat_new_log()
        log = f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[{role}]:  \n{info}  \n\n"
        self.log_file.write(log)
        self.log_file.flush()

    # ====================== Message Sanitization ======================
    def _sanitize_messages(self, messages: list) -> list:
        """Remove image_url blocks from history if model doesn't support multimodal."""
        if self.llm_client.support_multimodal:
            return messages
        cleaned = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                # Keep only text blocks, drop image_url blocks
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                cleaned.append({"role": msg["role"], "content": "\n".join(filter(None, text_parts))})
            else:
                cleaned.append(msg)
        return cleaned

    # ====================== Streaming Response ======================
    async def stream_response(self, use_tool_call=False, tools=base_tools):
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
                show_content = ("•••  \n" if len(resp_lines) > 10 else "") + "\n".join(
                    resp_lines[-10:])
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
            "content": src_response.strip(),
            "tool_calls": tool_calls,
            "reasoning_content": reasoning_content,
            "elapsed": f"{elapsed:.2f}"
        })

        return src_response, tool_calls, reasoning_content

    async def get_response_blocking(self, use_tool_call=False, tools=base_tools):
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
            "content": show_result,
            "tool_calls": tool_calls,
            "reasoning_content": reasoning_content,
            "elapsed": f"{elapsed:.2f}"
        })

        return result, tool_calls, reasoning_content

    # ====================== MCP Tools Loading ======================
    async def load_mcp_servers_info(self):
        all_tools = []
        all_prompts = []
        self.mcp_when_to_use = ""
        self.active_mcp = None

        for server in self.servers:
            if server in self.invalid_servers:
                continue
            tools = await server.list_tools()
            if tools is None:
                self.invalid_servers.add(server)
                self._emit(EventType.ERROR, {"message": f"MCP服务 [{server.name}] 工具列表获取失败，已跳过"})
                await asyncio.sleep(0)
                continue
            if tools:
                all_tools.extend(tools)
            mcp_prompts = await server.list_prompts()
            all_prompts.extend(mcp_prompts)
            self.mcp_when_to_use += f"\n**Server**: {server.name}\n**When-to-use**: {server.config.get('when_to_use', '')}\n"
            await asyncio.sleep(0)

        return all_tools, all_prompts

    # ====================== Main Chat Processing ======================
    async def process_user_message(self, user_input: str) -> str:
        """Process a user message and return the final assistant response."""
        self._cancel_event.clear()
        if self.llm_client.support_multimodal and self.img_path_list:
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

        self.append_info_to_log("user", user_input)

        if self.agent_switch == 0:
            if self.llm_client.support_stream:
                orig_response, _, _ = await self.stream_response()
            else:
                orig_response, _, _ = await self.get_response_blocking()
            self.messages.append({"role": "assistant", "content": orig_response})
            self.append_info_to_log("assistant", orig_response)
            
            # 处理多模态api，image_url有误情况
            if orig_response.lower().find('invalid_request_error') >=0 and orig_response.lower().find('unsupported image url')>=0:
               for message in reversed(self.messages):
                   if message['role'] == 'user' and isinstance(message.get("content"),list):
                       if any([x.get("image_url") for x in message.get("content")]):
                           message['content'] = "...请继续..."
                           break

            return orig_response

        # Agent mode with tool call loop
        support_tools = base_tools.copy()
        base_tools_count = len(base_tools)
        max_loop = 500 if self.agent_task_batch_switch == 1 else 50
        current_loop = 0

        while current_loop < max_loop:
            if self._cancel_event.is_set():
                self._emit(EventType.ERROR, {"message": "用户中断了Agent执行"})
                break
            current_loop += 1
            if current_loop > max_loop:
                self._emit(EventType.ERROR, {"message": f"Tool调用迭代次数超过上限：{max_loop}!"})
                break

            del support_tools[base_tools_count:]

            # agent_task模式：每轮添加任务规划/更新工具
            if self.agent_switch == 2:
                support_tools.append(gen_task_step)
                support_tools.append(update_task_step)
                support_tools.append(get_task_process)
                if self.agent_task_batch_switch == 1:
                    support_tools.append(pre_work_done)
                    support_tools.append(current_task_done)

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

            if self.llm_client.support_stream:
                orig_response, tool_calls, reasoning = await self.stream_response(
                    use_tool_call=True, tools=support_tools)
            else:
                orig_response, tool_calls, reasoning = await self.get_response_blocking(
                    use_tool_call=True, tools=support_tools)

            # 处理多模态api，image_url有误情况
            if orig_response.lower().find('invalid_request_error') >=0 and orig_response.lower().find('unsupported image url')>=0:
               for message in reversed(self.messages):
                   if message['role'] == 'user' and isinstance(message.get("content"),list):
                       if any([x.get("image_url") for x in message.get("content")]):
                           message['content'] = "...请继续..."
                           break

            # Check for cancellation after LLM call
            if self._cancel_event.is_set():
                self.messages.append({"role": "assistant", "content": "[用户中断了Agent执行]"})
                self._emit(EventType.ERROR, {"message": "用户中断了Agent执行"})
                break

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
                        functions.append(
                            {"id": tool_id, "name": func_name, "arguments": func_args})
                    except Exception:
                        continue

            if functions:
                tool_execute_list = functions
                self.messages.append({
                    "role": "assistant", "content": orig_response,
                    "tool_calls": tool_calls, "reasoning_content": reasoning or "正在调用工具..."
                })
                self.append_info_to_log("assistant",
                                        f"**content**:{orig_response}  \n**tool_calls**:{tool_calls}")
            else:
                tool_execute_list = []
                self.messages.append({"role": "assistant", "content": orig_response})
                self.append_info_to_log("assistant", orig_response)

            if not tool_execute_list:
                break

            tool_results = []
            for tc in tool_execute_list:
                func_id = tc.get("id")
                func_name = tc["name"]
                func_args = tc["arguments"]
                self._emit(EventType.TOOL_CALL, {
                    "tool_name": func_name,
                    "arguments": func_args,
                    "status": "running"
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
                        "tool_name": func_name,
                        "result": str(result)[:500],
                        "status": "success"
                    })
                elif self.active_mcp and func_name in [
                    tool.name for tool in await self.active_mcp.list_tools()]:
                    result = await self.process_mcp_response(
                        self.active_mcp, {"name": func_name, "arguments": func_args})
                else:
                    result = f"未知工具: {func_name}"
                    self._emit(EventType.ERROR, {"message": result})

                tool_results.append(
                    {"tool_call_id": func_id, "tool_name": func_name, "content": result})

            is_insert_images = False
            for tr in tool_results:
                if not tr["tool_call_id"]:
                    continue
                self.messages.append(
                    {"role": "tool", "tool_call_id": tr["tool_call_id"],
                     "content": str(tr['content'])})
                self.append_info_to_log("tool",
                                        f"**tool_call_id**:{tr['tool_call_id']}  \n**content**:{tr['content']}")
                if tr['tool_name']=='insert_images' and self.img_path_list:
                    is_insert_images = True
            
            if is_insert_images and self.llm_client.support_multimodal and len(self.img_path_list) > 0:
                user_content = [{ "type": "text", "text": "请继续处理提供的图片"}]
                for img_path in self.img_path_list:
                    try:
                        img_b64=image_to_base64(img_path,max_size_kb=1000)   
                        user_content.append({"type": "image_url", "image_url": {"url":img_b64}})
                    except Exception as e:
                        continue
                self.messages.append({"role": "user", "content": user_content})                         
                self.img_path_list.clear()

            # 批量任务：current_task_done 后清理中间消息，注入下一任务指引
            if self.agent_task_batch_switch == 1  and self._batch_cleanup_pending:
                self._batch_cleanup_pending = False
                del self.messages[self.agent_batch_pre_work_msg_len + 1:]
                self.messages.append({
                    "role": "user",
                    "content": """之前的任务已完成(...为节省上下文，省略之前任务的过程...)
请根据"任务文件夹"中"批量任务清单"进度及"共性任务执行总结"，请进行下个任务；若无，则根据各个"单个任务执行结果"总结整个批量任务的结果。"""
                })

        self._emit(EventType.AGENT_LOOP_DONE)
        return orig_response if 'orig_response' in dir() else ""

    async def start_new_chat(self):
        del self.messages[:]
        self.active_skill = None
        self.active_mcp = None
        self.task_steps = []
        self.agent_task_batch_switch = 0
        self.agent_batch_pre_work_msg_len = 1
        self._batch_cleanup_pending = False
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if self.agent_switch in (1, 2):
            self.messages.append(
                {"role": "system", "content": await self.gen_agent_system_content()})
        else:
            self.messages.append(
                {"role": "system", "content": self.gen_chat_system_content()})
        self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")

    async def enable_agent(self, switch_mode: int = 1):
        self.skills_meta = load_skills_metadata()
        try:
            await self.initialize_servers()
        except BaseException:
            pass  # MCP servers failing shouldn't block agent mode
        await self.load_mcp_servers_info()
        self.messages[0] = {"role": "system",
                            "content": await self.gen_agent_system_content()}
        self.task_steps = []
        self.agent_switch = switch_mode

    async def disable_agent(self):
        self.active_skill = None
        self.skills_meta = []
        self.active_mcp = None
        self.task_steps = []
        self.agent_task_batch_switch = 0
        self.agent_batch_pre_work_msg_len = 1
        await self.cleanup_servers()
        self.messages[0] = {"role": "system", "content": self.gen_chat_system_content()}
        self.agent_switch = 0

    async def reload_config(self):
        try:
            self.base_configs = config.load_config("configs.json")
        except Exception:
            self.base_configs = {"llm_models": [], "mcp_servers": [], "web_search": {}}
            return 0, 0, 0
        # Reload models
        self.client_models.clear()
        model_no = 1
        for model in self.base_configs.get("llm_models", []):
            if model.get("disabled"):
                continue
            self.client_models[str(model_no)] = model
            model_no += 1
        model_count = len(self.client_models)
        # Reload MCP servers from updated config
        mcp_configs = self.base_configs.get("mcp_servers", {})
        new_servers = [
            Server(name, srv_config)
            for name, srv_config in mcp_configs.items()
            if not srv_config.get("disabled")
        ]
        mcp_errors = 0
        if self.agent_switch in (1, 2):
            # Agent mode active: reconnect servers and refresh tools
            await self.reinitialize_servers(new_servers)
            await self.load_mcp_servers_info()
            mcp_errors = len(self.invalid_servers)
            self.skills_meta = load_skills_metadata()
            self.messages[0] = {"role": "system",
                                "content": await self.gen_agent_system_content()}
        else:
            # Agent mode off: replace server list without connecting
            await self.cleanup_servers()
            self.servers = new_servers
        return model_count, len(new_servers), mcp_errors

    async def process_use_prompt(self, prompt_name: str) -> list | None:
        selected_prompt = None
        selected_server = None
        for server in self.servers:
            if server in self.invalid_servers:
                continue
            try:
                mcp_prompts = await server.list_prompts()
            except RuntimeError:
                continue
            if not mcp_prompts:
                continue
            for mcp_prompt in mcp_prompts:
                if mcp_prompt.name == prompt_name:
                    selected_prompt = mcp_prompt
                    selected_server = server
                    break
            if selected_prompt:
                break
        if not selected_prompt:
            return None
        args = {}
        if selected_prompt.arguments:
            for arg in selected_prompt.arguments:
                args[arg.name] = ""  # UI will handle input
        tool_resp_prompts = await selected_server.get_prompt(selected_prompt.name, args)
        if not tool_resp_prompts:
            return None
        return tool_resp_prompts.messages

    # ====================== 会话压缩与加载 ======================

    async def _compact_session(self):
        """压缩当前会话并保存到 his_sessions/ 目录

        返回:
            str | None: 保存的文件路径，失败返回 None
        """
        his_dir = os.path.join(WORKSPACE_DIR, "his_sessions")
        os.makedirs(his_dir, exist_ok=True)

        # 从 messages 中提取对话内容（跳过 system 消息）
        conv_parts = []
        for msg in self.messages:
            role = msg.get("role", "")
            if role == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [x["text"] for x in content if x.get("type") == "text"]
                content = " ".join(texts)
            if not content or content.strip() == "":
                content = "(无文本内容)"
            if isinstance(content, str) and len(content) > 600:
                content = content[:600] + "\n...[截断]..."
            role_label = {"user": "用户", "assistant": "助手"}.get(role, role)
            conv_parts.append(f"## {role_label}\n{content}")

            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                if len(str(reasoning)) > 300:
                    reasoning = str(reasoning)[:300] + "..."
                conv_parts.append(f"[思考过程]: {reasoning}")

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tc_summary = []
                for tc in tool_calls:
                    fname = tc.get("function", {}).get("name", "?")
                    tc_summary.append(f"调用 `{fname}`")
                conv_parts.append(f"[工具调用]: {' → '.join(tc_summary)}")

        conversation_text = "\n\n".join(conv_parts)
        msg_count = sum(1 for m in self.messages if m.get("role") != "system")

        self._emit(EventType.SYSTEM_INFO, {"message": "🧠 正在压缩会话（调用 AI 生成摘要）..."})

        compress_msgs = [
            {"role": "system", "content": "你是一个会话压缩专家。请分析以下对话，生成结构化摘要。"},
            {"role": "user", "content": f"""请分析以下会话（共 {msg_count} 条消息），生成结构化的 Markdown 摘要，包含：

### Goal
会话的高层目标 / 用户想要什么

### Constraints
用户设定的硬性约束、边界条件、不允许做的事

### Progress

#### Done
已完成并验证的事项

#### In Progress
正在进行中但尚未完成的工作

#### Blocked
被卡住的事项、原因、解锁条件

### Key Decisions
架构选择、设计决策、技术权衡

### Next step
恢复工作时下一步要做什么（单行、具体）

---

对话内容：

{conversation_text}

---

请只输出上述结构的 Markdown 内容，不要额外说明。如果某个字段没有对应内容，写「暂无」。"""
            }
        ]
        try:
            result, _, _, _ = await self.llm_client.get_response(compress_msgs, use_tool_call=False)
        except Exception as e:
            self._emit(EventType.ERROR, {"message": f"压缩失败（AI 调用异常）: {e}"})
            return None

        # 从第一条用户消息中提取主题，生成文件名
        first_user = ""
        for msg in self.messages:
            if msg.get("role") == "user":
                c = msg.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x["text"] for x in c if x.get("type") == "text")
                first_user = c[:40]
                break
        topic = re.sub(r'[\\/:*?"<>|\n\r]', '_', first_user).strip()
        topic = re.sub(r'_+', '_', topic)[:30] or "session"

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"session-{topic}-{timestamp}.md"
        filepath = os.path.join(his_dir, filename)

        # 写入文件
        model_name = getattr(self.llm_client, 'ai_model', 'N/A') if self.llm_client else "N/A"
        agent_mode = {0: "关闭", 1: "Agent", 2: "Agent-Task"}.get(self.agent_switch, "N/A")
        full_md = f"""# Compaction Relay

> 自动压缩于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 消息数: {msg_count} | 模型: {model_name} | Agent模式: {agent_mode}

---

{result}

---

## 元信息

- **文件名**: `{filename}`
- **原始消息数**: {msg_count}（系统消息不计）
- **模型**: {model_name}
- **Agent模式**: {agent_mode}
- **工作目录**: `{WORKSPACE_DIR}`
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_md)

        return filepath

    def _list_session_files(self) -> list[str]:
        """列出 his_sessions/ 目录下的会话文件。

        返回:
            list[str]: 按文件名降序排列的会话文件列表
        """
        his_dir = os.path.join(WORKSPACE_DIR, "his_sessions")
        if not os.path.isdir(his_dir):
            return []
        files = sorted(
            [f for f in os.listdir(his_dir) if f.startswith("session-") and f.endswith(".md")],
            reverse=True
        )
        return files

    async def _load_session_file(self, filepath: str, filename: str) -> str | None:
        """读取已保存的会话文件并作为 assistant 消息插入到消息列表。

        Args:
            filepath: 会话文件的绝对路径
            filename: 会话文件名

        返回:
            str | None: 成功返回 filename，失败返回 None
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        load_message = f"""---
## 已加载的压缩会话

以下是从 `{filename}` 加载的之前压缩的会话摘要，可以基于此继续之前的任务：

{content}

---"""

        if len(self.messages) >= 2 and self.messages[0].get("role") == "system" and self.messages[1].get("role") != "assistant":
            self.messages.insert(1, {"role": "assistant", "content": load_message})
            return filename
        return None
