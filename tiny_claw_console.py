# coding:utf-8

import warnings;warnings.filterwarnings("ignore", category=UserWarning)
import asyncio,json,typing,os,re,shutil,math,uuid,inspect,traceback,httpx
import locale
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.style import Style
from rich.markup import escape
from prompt_toolkit.completion import WordCompleter
from rich.box import Box,MARKDOWN
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from prompt_toolkit import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.filters import is_done
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text
from datetime import datetime
from contextlib import AsyncExitStack
from typing import Any
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult,TextContent,PromptArgument,GetPromptResult,PromptMessage,TextContent
from typing import Dict, Any, Optional, List, Union, get_type_hints
from dataclasses import dataclass
import subprocess,platform
import base64
from PIL import Image
from io import BytesIO
from fastmcp.client.auth.oauth import OAuth
import pyperclip


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


def view_image(image_path, ratio =0.1):
        """
        将图片转换为rich的art对象，在终端中显示
        """
        if not os.path.isabs(image_path):
            if not os.path.exists(image_path):
                image_path = os.path.join(WORKSPACE_DIR, image_path)
            file_path = os.path.abspath(image_path)
        else:
            file_path = image_path.removeprefix("file:///")
        # 检查源文件是否存在
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        # 打开图片
        img = None
        try:
            img = Image.open(file_path).convert("RGB")
            o_width, o_height = img.size
            default_ratio = server_console.width / 2 / o_width
            ratio = min(ratio, default_ratio)
            width = int(o_width * ratio)
            height = int(o_height * ratio)
            # 缩放至目标尺寸
            img_resized = img.resize((width, height), Image.LANCZOS) # pyright: ignore
            # 每两行作为一个 rich 行（使用半块字符 ▄）
            lines = []
            for y in range(0, height, 2):
                line_parts = []
                for x in range(width):
                    # 上像素
                    r1, g1, b1 = img_resized.getpixel((x, y)) # pyright: ignore
                    if y + 1 < height:
                        # 下像素
                        r2, g2, b2 = img_resized.getpixel((x, y + 1)) # pyright: ignore
                        # 使用 ▄ 下半块，背景色=上像素颜色，前景色=下像素颜色
                        line_parts.append(f"[on rgb({r1},{g1},{b1}) rgb({r2},{g2},{b2})]▄[/]")
                    else:
                        # 最后一行只有上半部分
                        line_parts.append(f"[rgb({r1},{g1},{b1})]▀[/]")
                line = "".join(line_parts) # 拼接成一行
                lines.append(line)
            pic_content = "\n".join(lines)
            console.print(Text.from_markup(pic_content, justify="left"))
            img.close()
            return f"已经给用户显示图片了"
        except Exception as e:
            if img:
                img.close()
            return f"[ERROR]读取图片失败: {e}"

# ====================== 基础设置 ======================
base_tools = [
    {
        "type": "function",
        "function":{
            "name": "skill_call",
            "description": "调用技能",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "技能名称",
                    },
                    "user_request":{
                        "type": "string",
                        "description": "用户请求"
                    }
                },
                "required": ["skill_name", "user_request"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "mcp_call",
            "description": "调用MCP服务",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "根据需求选择的MCP服务名称",
                    },
                    "user_request":{
                        "type": "string",
                        "description": "用户的工具调用请求"
                    }
                },
                "required": ["server_name", "user_request"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "execute_bash",
            "description": "执行shell命令（Windows使用PowerShell，Linux/macOS使用bash）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "完整的PowerShell（Windows）或bash（Linux/macOS）命令",
                    },
                    "command_type": {
                        "type": "string",
                        "description": "仅在Linux/macOS下可指定为bash（默认），Windows下固定为powershell",
                    }
                },
                "required": ["command","command_type"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "read_file",
            "description": "读取文本类型文件（限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `htm`, `xml`源文件，其他类型文件由其他工具处理.）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "start_line_num": {
                        "type": "number",
                        "description": "读取的开始行号，默认1",
                    },
                    "lines": {
                        "type": "number",
                        "description": "读取的行数，默认-1：表示全部",
                    }
                },
                "required": ["path"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "read_file_and_linenum",
            "description": "读取文本类型文件, 且读取的内容带有行号（限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `htm`, `xml`源文件，其他类型文件由其他工具处理.）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "start_line_num": {
                        "type": "number",
                        "description": "读取的开始行号，默认1",
                    },
                    "lines": {
                        "type": "number",
                        "description": "读取的行数，默认-1：表示全部",
                    }
                },
                "required": ["path"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "insert_file_at_line",
            "description": "在指定行号前插入内容到文本文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "line_number": {
                        "type": "number",
                        "description": "行号",
                    },
                    "content": {
                        "type": "string",
                        "description": "需要插入的内容",
                    }
                },
                "required": ["path","line_number","content"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "append_file",
            "description": "追加信息到文本文件(会主动创建文件)。适用于：写入的内容很多，为了防止数据中断，用此分段写入",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "需要追加的内容",
                    }
                },
                "required": ["path"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "write_file",
            "description": "一次性将信息到文本文件(会主动创建文件)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "需要写入的内容",
                    }
                },
                "required": ["path"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "edit_file",
            "description": "修改文件的指定文本内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "需要被替换的文本内容",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "新的文本内容",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换所有匹配项，true为替换所有，false为只替换第一个",
                    }
                },
                "required": ["path","old_text","new_text","replace_all"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "execute_script",
            "description": "执行脚本",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_name": {
                        "type": "string",
                        "description": "脚本名",
                    },
                    "args":{
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "description": "脚本需要的各个参数"
                    },
                    "timeout":{
                        "type": "number",
                        "description": "脚本执行超时时间，单位秒"
                    }

                },
                "required": ["script_name"]
            }
        }
    },{
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
    }
]


gen_task_step = {
    "type": "function",
    "function":{
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
                            "step_no": {
                                "type": "number",
                                "description": "步骤序号，新任务从1开始递增，调整任务从调整位置开始递增"
                            },
                            "step_name": {
                                "type": "string",
                                "description": "步骤名称（简洁概括）"
                            },
                            "step_desc": {
                                "type": "string",
                                "description": "步骤详细描述，说明这一步要做什么、需要用什么工具/方法"
                            }
                        },
                        "required": ["step_no", "step_name", "step_desc"]
                    }
                }
            },
            "required": ["task_steps"]
        }
    }
}

update_task_step = {
    "type": "function",
    "function":{
        "name": "update_task_step",
        "description": "更新任务步骤的执行状态。每完成或失败一个步骤时调用，用于标记进度。",
        "parameters": {
            "type": "object",
            "properties": {
                "step_no": {
                    "type": "number",
                    "description": "任务步骤序号"
                },
                "new_status":{
                    "type":"string",
                    "description": "更新步骤状态，\"1\":进行中、\"2\":失败、\"3\":完成"
                },
                "step_result":{
                    "type":"string",
                    "description": "更新步骤执行成功或失败结果"
                }
            },
            "required": ["step_no","new_status","step_result"]
        }
    }
}

get_task_process = {
    "type": "function",
    "function":{
        "name": "get_task_process",
        "description": "获取任务当前步骤的执行进度。"
    }
}

pre_work_done = {
    "type": "function",
    "function":{
        "name": "pre_work_done",
        "description": "用于批量任务中标记前置工作已完成"
    }
}
current_task_done = {
    "type": "function",
    "function":{
        "name": "current_task_done",
        "description": "用于批量任务中标记当前任务已完成"
    }
}


web_search_tool = {
    "type": "function",
    "function":{
        "name": "web_search",
        "description": "调用搜索引擎",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询的信息",
                }
            },
            "required": ["query"]
        }
    }
}


# ====================== UI Config======================
code_themes=[
    "monokai","default","pastie","rrt","igor","solarized-light","emacs","one-dark"
]

class NoSlideBox(Box):
    def __init__(self,):
        super().__init__(
                "╭─┬╮\n"
                "    \n"
                "├─┼┤\n"
                "    \n"
                "├─┼┤\n"
                "├─┼┤\n"
                "    \n"
                "╰─┴╯\n"
        )

# 配置Console
console = Console(
    color_system="auto",
)
error_console = Console(
    stderr=True,
    style="bold red",
)
server_console = Console(
    style="bold blue", 
)


# ====================== SKILLS ======================
SKILLS_DIR = "./skills"
FLOWS_DIR = "./flows"
WORKSPACE_DIR = "./tiny_claw_workspace"
SESSION_TIMEOUT = 3600 * 8  # 对话超时时间（秒），超过后自动开始新对话

# AgentFlow 工作流引擎（多Agent协作）
from tiny_claw_core.agent_flow import AgentFlowRunner
from tiny_claw_core.agent import EventType as FlowEventType, SessionEvent as FlowSessionEvent
@dataclass
class Skill:
    name: str
    description: str
    instruction: str
    path: str
# ====================== 技能加载 ======================
def parse_skill_md(skill_path: str) -> tuple[dict, str]:
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

def load_flows_metadata() -> List[Dict[str, str]]:
    """加载 flows/ 目录下所有 agent_flow YAML 的元数据"""
    import yaml as _yaml
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
            with open(flow_path, "r", encoding="utf-8") as f:
                raw = _yaml.safe_load(f)
            flow = raw.get("flow", raw) if raw else {}
            flow_name = fname.rsplit(".", 1)[0]
            flows_meta.append({
                "name": flow_name,
                "title": flow.get("name", flow_name),
                "description": flow.get("description", ""),
                "path": flow_path,
            })
        except Exception:
            pass
    return flows_meta


def load_skills_metadata() -> List[Dict[str, str]]:
    skills_meta = []
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
        except Exception as e:
            error_console.print(f" ⚠️ 加载此Skill失败 - {skill_name}: {e}")
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
    except Exception as e:
        error_console.print(f" ⚠️ 加载此Skill失败 - {skill_name}: {e}")
        return None
    
def extract_tool_calls(text: str) -> List[Dict]:
    """
    从模型输出中提取约定格式的工具调用。
    格式示例：
        <tool_call>skill_call {"skill_name": "pdf_processor", "user_request": "提取PDF文本"}</tool_call>
        <tool_call>execute_bash {"command": "tasklist | findstr python","command_type": "powershell"}</tool_call>
    返回列表，每个元素为 {"name": func_name, "arguments": dict}
    """
    pattern = r"<tool_call>(\w+)\s+({.*?})\s*</tool_call>"
    matches = re.findall(pattern, text, re.DOTALL|re.I)
    tool_calls = []
    for func_name, args_str in matches:
        try:
            args = json.loads(args_str, strict=False)
            tool_calls.append({"name": func_name, "arguments": args})
        except json.JSONDecodeError:
            error_console.print(f" ⚠️ 无法解析工具参数 JSON: {args_str}")
    return tool_calls

# ====================== 执行script必要方法 ======================
def build_script_command(script_path: str, args: List[str]) -> Optional[str]:
    """
    根据脚本类型和操作系统构建执行命令
    
    Returns:
        完整的命令行字符串
    """
    script_name = os.path.basename(script_path)
    ext = os.path.splitext(script_name)[1].lower()
    is_windows = platform.system().lower() == "windows"
    
    # 参数字符串（自动添加引号保护空格）
    args_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in args)
    
    if ext == ".py":
        # Python 脚本
        python_cmd = "python" if is_windows else "python3"
        return f'{python_cmd} "{script_path}" {args_str}'
    
    elif ext == ".bat" or ext == ".cmd":
        # Windows 批处理
        if not is_windows:
            return None  # Linux 不支持 .bat
        return f'"{script_path}" {args_str}'
    
    elif ext == ".ps1":
        # PowerShell 脚本
        if not is_windows:
            return f'pwsh -File "{script_path}" {args_str}'
        return f'powershell -ExecutionPolicy Bypass -File "{script_path}" {args_str}'
    
    elif ext == ".sh":
        # Shell 脚本
        if is_windows:
            # Windows 下使用 Git Bash 或 WSL
            return f'bash "{script_path}" {args_str}'
        else:
            return f'bash "{script_path}" {args_str}'
    
    elif ext == ".js":
        # Node.js 脚本
        return f'node "{script_path}" {args_str}'
    
    elif ext == ".exe":
        # 可执行文件
        if not is_windows:
            return None
        return f'"{script_path}" {args_str}'
    
    else:
        return None

def decode_output(stdout: str, stderr: str) -> str:
    """
    处理不同平台的输出编码
    """
    output_parts = []
    if stdout:
        try:
            # 尝试多种编码
            for encoding in ["utf-8", "gbk", "cp936", "latin-1"]:
                try:
                    decoded = stdout.encode(encoding, errors="ignore").decode(encoding)
                    output_parts.append(decoded)
                    break
                except:
                    continue
        except:
            output_parts.append(stdout)
    if stderr:
        output_parts.append(f"[STDERR]\n{stderr}")
    return "\n".join(output_parts)

# ===================== LLM CLIENT ======================

class LLMClient:
    """Manages communication with the LLM provider."""

    def __init__(self, api_key: str,ai_channel: str = "OpenAI",ai_model: str = "gpt-4.1",
                 ai_api_url: str = "https://api.openai.com/v1/chat/completions",
                 ai_provider: str = "OpenAI",
                 support_stream: bool = True,
                 support_tool_call: bool = False,
                 support_thinking: list = [False,"off"],
                 support_multimodal : bool|None = False,
                 http_proxy: str = "") -> None:
        self.api_key: str = api_key
        self.ai_channel = ai_channel
        self.ai_model = ai_model
        self.ai_api_url = ai_api_url
        self.ai_provider = ai_provider
        self.support_stream = support_stream
        self.support_tool_call = support_tool_call
        self.support_thinking = support_thinking
        self.support_multimodal = support_multimodal
        self.http_proxy = http_proxy

    async def get_response(self, messages: list[dict[str, str]],use_tool_call=False,tools=base_tools) -> tuple[str,dict|None,list|None,str|None]:
        """Get a response from the LLM.
        Args:
            messages: A list of message dictionaries.
        Returns:
            The LLM's response as a string.
        Raises:
            httpx.RequestError: If the request to the LLM fails.
        """

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "messages": messages,
            "model": self.ai_model,
            # "max_completion_tokens": 4096,
            "stream": False,
            "stop": None,
        }

        if self.support_tool_call and use_tool_call:
            payload["tools"] = tools

        # 思考模式
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"]= {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            else:
                payload["thinking"]= {"type": "disabled"}

        try:
            async with httpx.AsyncClient(proxy=self.http_proxy) as client:
                response = await client.post( self.ai_api_url, headers=headers, json=payload,timeout=360)
                response.raise_for_status()
                data = response.json()
                usage = None
                if data.get("usage"):
                    usage={}
                    usage["prompt_tokens"] = data["usage"].get("prompt_tokens")
                    usage["completion_tokens"] = data["usage"].get("completion_tokens")
                    usage["total_tokens"] = data["usage"].get("total_tokens")
                resp_message = data["choices"][0]["message"]
                tool_calls = []
                if resp_message.get("tool_calls"):
                    tool_calls = [func for func in data["choices"][0]["message"]["tool_calls"] if func["type"] == "function"]
                reasoningContent = None
                if resp_message.get("reasoning_content"):
                    reasoningContent = resp_message["reasoning_content"]
                return resp_message.get("content"),usage,tool_calls,reasoningContent # type: ignore
        except httpx.HTTPError as e:
            return (
                f"I encountered an error: Error getting LLM response. {str(e)}. "
                "Please try again or rephrase your request."
            ),None,None,None
        
    async def yield_response(self,messages: list[dict[str, str]],use_tool_call=False,tools=base_tools):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "messages": messages,
            "model": self.ai_model,
            # "max_completion_tokens": 4096,
            "stream": True,
            "stop": None,
        }
        if self.support_tool_call and use_tool_call:
            payload["tools"] = tools

        # 思考模式
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"]= {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            else:
                payload["thinking"]= {"type": "disabled"}
        
        async with httpx.AsyncClient(proxy=self.http_proxy) as client:
            try:
                async with client.stream("POST", url=self.ai_api_url,
                                         headers=headers, json=payload, timeout=360) as response:
                    try:
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            if not line.startswith("data:"):
                                yield 0,line
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
                                    yield 1,data['choices'][0]["delta"]["content"]
                                if data['choices'][0].get("usage"):
                                    yield 2,data['choices'][0]["usage"]
                                if data.get("usage"):
                                    yield 2,data.get("usage")
                                if data['choices'][0].get("delta",{}).get("tool_calls"):
                                    tool = data['choices'][0]["delta"]["tool_calls"][0]
                                    if tool.get("type") == "function" and tool.get("id","").strip():
                                        yield 31,json.dumps(tool)   # 提取工具(带ID才算)
                                    elif tool["function"].get("arguments"): # 提取工具参数(不能trim！)
                                        yield 32,tool["function"]["arguments"] # 提取工具参数流式片段
                                    else:
                                        continue
                                if data['choices'][0].get("delta", {}).get("reasoning_content"):
                                    yield 4,data['choices'][0]["delta"]["reasoning_content"]
                            except Exception as e:
                                continue # 丢包了
                    except Exception as e:
                        yield 0,f" {str(e)}"
            except (httpx.HTTPError,StopAsyncIteration) as e:
                error_message = f"{str(e)}"
                if isinstance(e, httpx.HTTPStatusError):
                    error_message = f"Response code:{e.response.status_code}, content: {repr(e.response.text)}"
                yield 0,error_message

class LLMClient2:
    # 此处为公司内部的AI助手平台API调用客户端的实现
    def __init__(self,fosp_open_id,fosp_developer_secret,ai_channel: str = "OpenAI",
                 ai_model: str = "gpt-4o-mini",
                 ai_provider: str = "FOSP",
                 ai_api_url: str = "http://fosp-gateway.vemic.com/aigc-direct/api/aigc-server-t2/mix/v1/chat/completion/plus",
                 support_stream: bool = False,
                 support_tool_call: bool = False,
                 support_thinking: list = [False,"off"],
                 support_multimodal : bool|None = False,
                 http_proxy: str = ""
                 ) -> None:
        self.fosp_open_id= fosp_open_id
        self.fosp_developer_secret = fosp_developer_secret
        self.ai_channel = ai_channel
        self.ai_api_url = ai_api_url
        self.ai_provider = ai_provider
        self.ai_model = ai_model
        self.support_stream = support_stream
        self.support_tool_call = support_tool_call
        self.support_thinking = support_thinking
        self.support_multimodal = support_multimodal
        self.http_proxy = http_proxy

    async def get_response(self, messages: list[dict[str, str]],use_tool_call: bool = False,tools=base_tools) -> tuple[str,dict|None,dict|None,str|None]:
        headers={ # 请求openai需要的请求头
            "Content-Type": "application/json",
            "Open-ID": self.fosp_open_id,
            "Developer-Secret":self.fosp_developer_secret,
            "Service-Code":"aigc-direct",
            "Service-Type": '3',
            "User-Flag": "DOBA-TEST-MCP",
            "AIGC-Target-DC":"dc-all",
        }
        payload = {
		    'channel': self.ai_channel,
            'model': self.ai_model,
            "messages":messages,
            "maxTokens":4096,
            "temperature":0.7,
            "session":str(uuid.uuid4()),
        }
        try:
            async with httpx.AsyncClient(proxy=self.http_proxy) as client:
                response = await client.post( self.ai_api_url, headers=headers, json=payload,timeout=360)
                response.raise_for_status()
                data = response.json()
                if not data.get('success') or data.get("code")!="AS0000":
                    error_console.print(f" ❌ 从大模型获取响应异常: {data.get('message')}")
                    return data.get("message"),None,None,None
                usage = None
                if data.get("data",{}).get("usage"):
                    usage={}
                if not data.get('success') or data.get("code")!="AS0000":
                    error_console.print(f" ❌ 从大模型获取响应异常: {data.get('message')}")
                    return data.get("message"),None,None,None
                usage = None
                if data.get("data",{}).get("usage"):
                    usage={}
                    usage["prompt_tokens"] = data["data"]["usage"].get("promptTokens")
                    usage["completion_tokens"] = data["data"]["usage"].get("completionTokens")
                    usage["total_tokens"] = data["data"]["usage"].get("totalTokens")
                return data["data"]["text"], usage, None, None

        except httpx.RequestError as e:
            error_message = f"Error getting LLM response. {str(e)}"
            if isinstance(e, httpx.HTTPStatusError):
                error_message = f"Error getting LLM response: {e.response.status_code} | {e.response.text}"
            return (
                f"I encountered an error: {error_message}. "
                "Please try again or rephrase your request."
            ),None,None, None




# ====================== Configuration ======================

class Configuration:
    """Manages configuration and environment variables for the client."""

    def __init__(self):
        """Initialize configuration with environment variables."""
        self.load_env()

    @staticmethod
    def load_env() -> None:
        """Load environment variables from .env file."""
        load_dotenv()

    @staticmethod
    def load_config(file_path: str) -> dict[str, Any]:
        with open(file_path, "r",encoding='utf-8') as f:
            return json.load(f)



# 定义全局的Configuration对象，以便在整个程序中共享配置
config = Configuration()

# ====================== MCP元素 ======================

class Tool:
    """Represents a tool with its properties and formatting."""

    def __init__(
        self, name: str, description: str, input_schema: dict[str, Any],server_name: str|None = None
    ) -> None:
        self.name: str = name
        self.server_name = server_name
        self.description: str = description
        self.input_schema: dict[str, Any] = input_schema

    def format_for_llm(self) -> str:
        """Format tool information for LLM.

        Returns:
            A formatted string describing the tool.
        """
        args_desc = []
        if "properties" in self.input_schema:
            for param_name, param_info in self.input_schema["properties"].items():
                arg_desc = (
                    f"- {param_name}: {param_info.get('description', 'No description')}"
                )
                if param_name in self.input_schema.get("required", []):
                    arg_desc += " (required)"
                args_desc.append(arg_desc)

        return f"""`{self.name}` 
Description: [由MCP服务`{self.server_name}`提供]{self.description.strip()}
Arguments:
{chr(10).join(args_desc)}
"""

class MCPPrompt:
    """Represents a prompt with its properties and formatting."""

    def __init__(
        self, name: str, description: str, arguments: list[PromptArgument],server_name: str|None = None
    ) -> None:
        self.name: str = name
        self.server_name = server_name
        self.description: str = description
        self.arguments: list[PromptArgument] = arguments

    def get_prompt_dict(self) -> dict:
        """Format prompt information for LLM.

        Returns:
            A formatted string describing the prompt.
        """
        prompt={"ServerName":self.server_name,"PromptName": self.name}
        if self.description:
            prompt["Description"]= self.description
        args_desc = []
        if self.arguments:
            for argObj in self.arguments:
                arg_dict = {
                    "Argument": argObj.name,
                    "Required": False
                }
                if argObj.description:
                    arg_dict["Description"]= argObj.description
                if argObj.required:
                    arg_dict["Required"]=True
                args_desc.append(arg_dict)
        if args_desc:
            prompt["Arguments"]=args_desc
        return prompt
    @property
    def format_for_rich(self) -> str:
        """Format prompt information for rich terminal."""
        return f"+ [bold blue]{self.server_name}[/bold blue] - [bold yellow]{self.name}[/bold yellow]" + \
            (f"\n  > [magenta]arguments[/magenta]: {json.dumps([arg.name for arg in self.arguments],ensure_ascii=False)}" if self.arguments else "") + \
            (f"\n  > [magenta]description[/magenta]: {self.description}" if self.description else "")



class Server:
    """Manages MCP server connections and tool execution."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name: str = name
        self.config: dict[str, Any] = config
        self.stdio_context: Any | None = None
        self.session: ClientSession | None = None
        self._cleanup_lock: asyncio.Lock = asyncio.Lock()
        self.exit_stack: AsyncExitStack = AsyncExitStack()
        self.capabilities = set() # set(["prompts","tools","resources"])

    async def initialize(self) -> None:
        """Initialize the server connection."""
        transport = self.config.get("transport")
        if transport == "stdio":
            command = (
                shutil.which("npx")
                if self.config.get("command") == "npx"
                else self.config.get("command")
            ) or "npx"
            server_params = StdioServerParameters(
                command=command,
                args=self.config.get("args",[]),
                # env={**os.environ, **(self.config["env"] if self.config.get("env") else {})}, 
                # 安全考虑，不要将本地的全部环境变量传递给本地服务
                env = self.config.get("env") # 本地服务独立配置在configs.json
            )
            cm = stdio_client(server_params)
            read, write = await self.exit_stack.enter_async_context(cm)
        elif transport == "sse":
            sseUrl = self.config.get("url","")
            if not sseUrl or sseUrl.endswith("/mcp"):
                raise ValueError("sse need url and the url should not end with /mcp")
            cm = sse_client(sseUrl)
            read, write = await self.exit_stack.enter_async_context(cm)
        elif transport == "streamable-http":
            streamableHttpUrl = self.config.get("url","") # 增加StreamableHttp连接支持
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
                async_httpx_client = httpx.AsyncClient(headers=headers,timeout=300,auth=oauth_provider)
                cm = streamable_http_client(streamableHttpUrl,http_client=async_httpx_client)
            else:
                cm = streamable_http_client(streamableHttpUrl)
            read, write, getSessionIdCallback = await self.exit_stack.enter_async_context(cm)
        else:
            raise ValueError("The command or transport: sse/streamable-http must be a valid string and cannot be None.")
        try:
            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write) 
            )
            init_resp = await session.initialize()
            if init_resp.capabilities.prompts:
                self.capabilities.add("prompts")
            if init_resp.capabilities.tools:
                self.capabilities.add("tools")
            self.session = session
        except:
            # traceback.print_exc()
            await self.cleanup()
            raise Exception(f"Initialize MCP Server Session Failed")
        
        
    async def list_tools(self) -> list[Any]|None:
        """List available tools from the server.

        Returns:
            A list of available tools.

        Raises:
            RuntimeError: If the server is not initialized.
        """
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
                        Tool(tool.name, tool.description, tool.inputSchema,server_name=self.name)
                        for tool in item[1]
                    )

            return tools
        except:
            error_console.print(f" ❌ 从此MCP服务获取工具列表失败: {self.name}")
            await self.cleanup()
            return None

    async def cleanup(self) -> None:
        """Clean up server resources."""
        await self.exit_stack.aclose()
        async with self._cleanup_lock:
            self.session = None
            self.stdio_context = None
  

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute a tool with retry mechanism.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            Tool execution result.

        Raises:
            RuntimeError: If server is not initialized.
        """

        try:
            if not self.session:
                raise RuntimeError(f"Server {self.name} not initialized")
            result = await self.session.call_tool(tool_name, arguments)
            return result
        except Exception as e:
            return CallToolResult(content=[TextContent(type="text",text=f"Error executing tool {self.name} - {tool_name}. {str(e)}.")],
                                  isError=True)

    async def list_prompts(self) -> list[MCPPrompt]:
        """Get prompts from the server.

        Returns:
            A list of prompts.

        Raises:
            RuntimeError: If the server is not initialized.
        """
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
                        MCPPrompt(
                            prompt.name,
                            prompt.description,
                            prompt.arguments,
                            server_name=self.name
                        )
                        for prompt in item[1]
                    )
            return prompts
        except Exception as e:
            server_console.print(f" [ERROR] 无法从此MCP服务获取Prmpts - {escape(self.name)}: {escape(str(e))}")
            return []

    async def get_prompt(self, prompt_name: str, arguments: dict[str, Any]) -> GetPromptResult | None:
        """Call a prompt with retry mechanism.

        Args:
            prompt_name: Name of the prompt to call.
            arguments: Prompt arguments.

        Returns:
            Prompt execution result.

        Raises:
            RuntimeError: If server is not initialized.
        """
        try:
            if not self.session: 
                raise RuntimeError(f"Server {self.name} not initialized")        
            result = await self.session.get_prompt(prompt_name, arguments) 
            return result   
        except Exception as e:
            return None


# ====================== 对话 ======================

class ChatSession:
    """Orchestrates the interaction between user, LLM, and tools."""
    def __init__(self) -> None:
        self.servers: list[Server] = []
        self.invalid_servers: set[Server] = set() # 将连接失败地服务器加入不可用中
        self.llm_client: LLMClient | LLMClient2 | None  = None
        self.usage :dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.markdown_theme : str = code_themes[0]
        self.log_file = None
        self.messages = []
        self.img_path_list = []
        self.agent_switch = 0 # agent开关，0：关闭，1：开启，2：任务
        self.task_steps = [] # agent_task模式的步骤列表: [{"step_no":1,"step_name":"...","step_desc":"...","status":"pending|done|failed","result":""}]
        self.skills_meta = []
        self.active_skill: Optional[Skill] = None
        self.active_mcp: Optional[Server] = None
        self.tool_handlers = {
            "skill_call": self.handle_skill_call,
            "mcp_call": self.handle_mcp_call,
            "execute_bash": lambda **kwargs: self.execute_bash(kwargs["command"],kwargs.get("command_type",None)),
            "execute_script": lambda **kwargs: self.execute_script(kwargs["script_name"],kwargs.get("args", []),kwargs.get("timeout", 30)),
            "read_file": lambda **kwargs: self.read_file(kwargs["path"],kwargs.get("start_line_num", 1),kwargs.get("lines", -1)),
            "read_file_and_linenum": lambda **kwargs: self.read_file_and_linenum(kwargs["path"], kwargs.get("start_line_num", 1), kwargs.get("lines", -1)),
            "write_file": lambda **kwargs: self.write_file(kwargs["path"], kwargs["content"]),
            "append_file": lambda **kwargs: self.append_file(kwargs["path"], kwargs["content"]),
            "insert_file_at_line": lambda **kwargs: self.insert_file_at_line(kwargs["path"], kwargs["line_number"], kwargs["content"]),
            "edit_file": lambda **kwargs: self.edit_file(kwargs["path"], kwargs["old_text"], kwargs["new_text"], kwargs.get("replace_all", False)),
            "insert_images": lambda **kwargs: self.insert_images(kwargs["image_paths"]),
            "gen_task_step": self.handle_gen_task_step,
            "update_task_step": self.handle_update_task_step,
            "get_task_process": lambda :self._gen_task_progress_md(),
            "pre_work_done": lambda :self.handle_pre_work_done(),
            "current_task_done": lambda :self.handle_current_task_done(),
            "ask_user": self.handle_ask_user
        }
        self.mcp_when_to_use = ""
        self.agent_task_batch_switch = 0 # 批量任务开关 0：关闭，1：开启
        self.agent_batch_pre_work_msg_len = 1 # 批量任务消息体长度
        self._batch_cleanup_pending = False
        self._skill_command_pending = False  # /skill:<skillname> 命令待处理标志


        try:
            self.base_configs = config.load_config("configs.json") # 重新读取配置
        except Exception as e:
            error_console.print(f" ❌ 无法加载config.json: {e}")
            self.base_configs = {"llm_models": [], "mcp_servers": [],"search_switch": {}}

        # 开启web搜索时，添加web_search工具
        if not self.base_configs.get("web_search",{}).get("disabled", True):
            self.tool_handlers["web_search"] = lambda **kwargs: self.web_search(kwargs["query"])

        # 初始化llm_client列表
        self.client_models = {}
        model_no = 1
        for model in self.base_configs["llm_models"]:
            if model.get("disabled"):
                continue
            self.client_models[str(model_no)]=model
            model_no+=1
        
        if not self.client_models:
            error_console.print(" ❌ 无有效的AI大模型，亲在configs.json中配置AI大模型。")

    # ====================== BASE TOOL ======================
    def execute_bash(self, command: str, command_type: str = None) -> str:
        """
        执行 shell 命令（经过安全过滤）。
        Windows 固定使用 PowerShell，Linux/macOS 使用 bash。

        Args:
            command: 要执行的命令字符串
            command_type: 仅在 Linux/macOS 下可用（"bash"），Windows 下忽略

        Returns:
            命令执行结果（stdout 或 stderr）
        """
        is_windows = platform.system().lower() == "windows"

        if not command.strip():
            return "[ERROR]命令不能为空"

        if is_windows:
            if command_type == "bash":
                return "[ERROR]Windows只支持powershell类型"
            # ===== Windows：只用 PowerShell =====
            ps_dangerous_patterns = [
                # 编码/混淆执行
                r"-enc(?:odedcommand)?\s", r"\biex\b", r"invoke-expression",
                # 网络下载
                r"invoke-webrequest.*-outfile", r"\biwr\b.*-outfile",r"new-object\s+net\.webclient", r"downloadfile", 
                r"downloadstring",r"invoke-restmethod.*-outfile", r"\birm\b.*-outfile",
                # 强制删除
                r"\bremove-item\b", r"\bdel\b", r"\brmdir\b",
                # 磁盘/卷操作
                r"format-volume", r"clear-disk", r"initialize-disk",r"set-disk", r"clear-recyclebin",
                # 系统关机/重启
                r"stop-computer", r"restart-computer",r"shutdown\.exe", r"shutdown\s",
                # 进程终止
                r"stop-process.*-force", r"\bkill\b.*-force",r"taskkill", r"tskill",
                # 注册表修改
                r"remove-itemproperty.*hk", r"set-itemproperty.*hk",r"new-itemproperty.*hk",r"set-executionpolicy",
                # 用户/组管理
                r"net\s+(localgroup|user|group)\s",
                # 服务操作
                r"sc\.exe\s+(stop|config|delete)", r"stop-service.*-force",r"set-service.*-status.*stopped",
                # 权限提升
                r"start-process.*-verb\s+runas",r"-windowstyle\s+hidden",
                # 远程执行
                r"invoke-command", r"invoke-wmimethod",r"new-cimsession", r"enter-pssession",
                # Windows 功能
                r"disable-windowsoptionalfeature",r"dism\.exe\s+/online\s+/disable"
            ]
            matched = next((p for p in ps_dangerous_patterns if re.search(p, command, re.IGNORECASE)), None)
            if matched:
                result = input(f"\033[41m\033[1;37m该PowerShell命令有风险(含「{matched}」)，是否执行？\033[0m ⚠️ (Yes/No): ")
                if result.strip().lower() != "yes":
                    return f"[ERROR]PowerShell命令包含潜在危险操作，已被拒绝。"

            # 自动补全 powershell 前缀
            if not command.lower().startswith("powershell "):
                # 转义命令中的双引号，防止PowerShell解析错误
                escaped_command = command.replace('"', '\\"')
                command = f'powershell -Command "{escaped_command}"'
        else:
            # ===== Linux/macOS：使用 bash =====
            linux_dangerous_patterns = [
                r"\brm\b", r"\brmdir\b", r"\bdd\b", r"\bmkfs\b", r"\bfdisk\b",
                r"\bsudo\b", r"\bsu\b", r"\bpasswd\b",
                r"\breboot\b", r"\bshutdown\b", r"\bpoweroff\b", r"\bhalt\b",
                r"\bkill\s+-9\b", r"\bpkill\b",
                r"\bwget\s+\S+\s+\|\s+sh\b", r"\bcurl\s+\S+\s+\|\s+sh\b",
                r"\bsystemctl\s+disable\b", r"\bchkconfig\s+off\b",
                r"\biptables\s+-F\b", r"\bufw\s+disable\b", r"\bsetenforce\s+0\b",
            ]
            matched = next((p for p in linux_dangerous_patterns if re.search(p, command, re.IGNORECASE)), None)
            if matched:
                result = input(f"\033[41m\033[1;37m该bash命令有风险(含「{matched}」)，是否执行？\033[0m ⚠️ (Yes/No): ")
                if result.strip().lower() != "yes":
                    return "[ERROR]bash命令包含潜在危险操作，已被拒绝。"

        try:
            cp_env = os.environ.copy()
            cp_env["PYTHONIOENCODING"] = "utf-8"

            # 获取系统首选编码，Mac和Linux通常是utf-8，Windows可能是其他编码
            system_encoding = locale.getpreferredencoding(False)

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=600,
                encoding=system_encoding,
                errors="replace",
                cwd=os.path.dirname(WORKSPACE_DIR),
                env=cp_env,
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
        """调用搜索引擎进行搜索"""
        payload  = {
            "messages": [
                {
                    "content": f"{query}",
                    "role": "user"
                }
            ],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": 20}],
            "search_recency_filter": "year"
        }
        with httpx.Client() as client:
            search_key = self.base_configs.get("web_search",{}).get("api_key","**********")
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
                    # 组合每条结果的标题和内容
                    formatted_context += f"[{i+1}] 标题: {result['title']}\n"
                    formatted_context += f"    摘要: {result['content']}\n\n"
                    formatted_context += f"    原文链接: {result['url']}\n\n"
                return formatted_context
            except Exception as e:
                return f"[ERROR]Web Search Error: {e}"


    def write_file(self, path: str, content: str) -> str:
        """保存信息到用户本地文件"""
        if not os.path.isabs(path): 
            if not path.startswith(WORKSPACE_DIR) and not path.startswith(WORKSPACE_DIR.removeprefix("./")):
                # 优先写入工作区目录
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
        """修改文件中的文本内容
        
        Args:
            path: 文件路径
            old_text: 需要被修改的文本内容
            new_text: 新的文本内容
            replace_all: 是否修改所有匹配项，True为修改所有，False为只修改第一个
        
        Returns:
            操作结果信息
        """
        if not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            source_file_path = os.path.abspath(path)
        else:
            source_file_path = path.removeprefix("file:///")
        
        # 检查源文件是否存在
        if not os.path.exists(source_file_path):
            return f"[ERROR]源文件不存在: {source_file_path}"
        
        try:
            file_name = os.path.basename(source_file_path)
            file_size = os.path.getsize(source_file_path)
            if file_size > 1024 * 1024 * 5:
                return f"[ERROR]文件过大，无法进行文本替换，文件大小: {file_size/1024/1024} MB"
            
            with open(source_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            if old_text not in content:
                return f"[WARNING]未找到需要替换的文本内容"

            # 需要修改， 将原文件备份
            backup_file_path = os.path.join(os.path.dirname(source_file_path), f"{file_name}.backup")
            if not os.path.exists(backup_file_path): # 备份文件不存在，则创建
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


    def append_file(self,path: str, content: str) -> str:
        """追加信息到用户本地文件"""
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
        
    def insert_file_at_line(self,path, line_number:int, content:str):
        """在指定行号前插入文本"""
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
                return f"[ERROR]文件过大，无法在指定行号后插入信息，文件大小: {file_size/1024/1024} MB"
            with open(file_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()
            # 检查行号是否有效
            if isinstance(line_number, str):
                try:
                    line_number = int(line_number)
                except ValueError:
                    return f"[ERROR]行号 {line_number} 不是一个有效的整数"
            if 1 <= line_number <= len(lines) + 1:
                # 在指定位置插入
                lines.insert(line_number - 1, content + '\n')

                # 写回文件
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.writelines(lines)

                return f"在第 {line_number} 行前插入了文本"
            else:
                return f"[ERROR]行号 {line_number} 超出范围（文件共 {len(lines)} 行）"

        except Exception as e:
            return f"[ERROR]文件在指定行后插入信息失败：{e}"


    def read_file(self, path: str, start_line_num: int = 1, lines: int = -1) -> str:
        """读取文件

        Args:
            path: 文件路径
            start_line_num: 开始行号（从1开始），默认1
            lines: 读取行数，-1表示读取全部行（使用原逻辑），>1时按行号范围逐行读取
        """
        if path.startswith("http:") or path.startswith("https:"):
            return f"[ERROR]此路径为网页：{file_path}，无法直接读取"
        path = os.path.expanduser(path)
        if path.startswith("@skill/") and self.active_skill: # 针对skill目录下的文件情况
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path.removeprefix("file:///")
        # 检查源文件是否存在
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            # lines == -1: 读取全部行，使用原逻辑

            file_size = os.path.getsize(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                if lines == -1:
                    if file_size <= 1024 * 1024 * 1:
                        return f.read()
                    if file_size <= 1024 * 1024 * 2:
                        all_lines = []
                        for line in f:
                            all_lines.append(line)
                        return "".join(all_lines)
                    else:
                        return f"[ERROR]文件过大，无法一次性读取，文件大小: {file_size/1024/1024} MB，可改用指定起始行号和读取行数(<=5000)方式读取"
                else:
                    if lines > 5000:
                        return f"[ERROR]读取的行数不能超过5000"
                    end_line = start_line_num + lines - 1
                    result = []
                    for lineno, line in enumerate(f, start=1):
                        if lineno > end_line:
                            break
                        if lineno >= start_line_num:
                            result.append(line)
                    return "".join(result)

        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"
        
    def read_file_and_linenum(self, path: str, start_line_num: int = 1, lines: int = -1) -> str:
        """读取文件并返回带有行号的内容"""
        path = os.path.expanduser(path)
        if path.startswith("@skill/") and self.active_skill: # 针对skill目录下的文件情况
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            if not os.path.exists(path):
                path = os.path.join(WORKSPACE_DIR, path)
            file_path = os.path.abspath(path)
        else:
            file_path = path.removeprefix("file:///")
        # 检查源文件是否存在
        if not os.path.exists(file_path):
            return f"[ERROR]源文件不存在: {file_path}"
        try:
            file_size = os.path.getsize(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                if lines == -1:
                    if file_size <= 1024 * 1024 * 1:
                        read_lines = f.readlines()
                        return "".join([f"LINE[{i+1}] {line}" for i, line in enumerate(read_lines)])
                    if file_size <= 1024 * 1024 * 2:
                        line_no = 0
                        read_lines = []
                        for line in f:
                            read_lines.append(f"LINE[{line_no+1}] {line}")
                            line_no += 1
                        return "".join(read_lines)
                    else:
                        return f"[ERROR]文件过大，无法一次性读取，文件大小: {file_size/1024/1024} MB，可改用指定起始行号和读取行数(<=5000)方式读取"
                else:
                    if lines > 5000:
                        return f"[ERROR]读取的行数不能超过5000"
                    end_line = start_line_num + lines - 1
                    delta_no = 0
                    result = []
                    for lineno, line in enumerate(f, start=1):
                        if lineno > end_line:
                            break
                        if lineno >= start_line_num:
                            result.append(f"LINE[{start_line_num+delta_no}] {line}")
                            delta_no += 1
                    return "".join(result)
        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"

    
            
        
    def execute_script(self,script_name: str,args: List[str]|None = None,timeout: int = 30) -> str:
        """
        执行当前激活技能目录下 scripts/ 中的脚本

        Args:
            script_name: 脚本文件名（如 "process.py", "helper.bat"）
            args: 传递给脚本的参数列表
            timeout: 超时时间（秒）

        Returns:
            脚本执行结果
        """
        if not self.active_skill:
            return "[ERROR]当前没有激活的技能"
        # 相对路径处理
        script_path = os.path.join(self.active_skill.path, "scripts", script_name) # 默认skill本身的脚本
        if not os.path.exists(script_path):
            script_path = os.path.join(WORKSPACE_DIR, script_name) # 如果skill本身的脚本不存在，则尝试执行工作区目录下的脚本
            if not os.path.exists(script_path):
                return f"[ERROR]脚本不存在: {script_path}"
        # 构建命令
        cmd = build_script_command(script_path, args or [])
        if not cmd:
            return f"[ERROR]不支持的脚本类型: {script_name}"

        try:
            # 执行脚本
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                errors='replace',
                cwd=os.path.dirname(WORKSPACE_DIR),  # 在脚本所在目录执行
                env=os.environ.copy()
            )

            # 处理编码
            output = decode_output(result.stdout, result.stderr)

            if result.returncode == 0:
                return output if output else "(脚本执行成功，无输出)"
            else:
                return f"[ERROR]脚本执行失败 (退出码 {result.returncode}):\n{output}"

        except subprocess.TimeoutExpired:
            return f"[ERROR]脚本执行超时（{timeout}秒）"
        except Exception as e:
            return f"[ERROR]脚本执行出错: {str(e)}"

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


    async def gen_agent_system_content(self):
        """ 生成Agent的系统信息 """
        
        # 用户习惯
        user_memory = self.read_file("AGENT.md")
        if not user_memory or user_memory.strip().startswith("[ERROR]读取文件失败"):
            server_console.print(" ℹ️ 没有在工作区AGENT.md中维护用户使用习惯")
            user_memory = "[暂无]"
        
        # 技能列表
        skills_list = "\n".join([f"- {s['name']}: {s['description']}" for s in self.skills_meta])
        
        # 以下将系统提示词分段，尽量保证一个任务内的头部保持一致，提高缓存命中率
        public_header = f"""## 角色定义      
你是一个智能助手（风格：不装、说干就干、基于现有数据、绝不捏造信息）, 运行的系统为{platform.system()}，可以根据用户需求进行回答或调用工具/技能/MCP服务。

## 核心工作
根据用户的问题、用户的技能工具使用习惯(如果有)，从提供的工具列表中选择合适的工具、技能或MCP服务进行调用；如果无需调用，或无匹配工具、技能或MCP服务情况，直接根据用户问题进行回答即可。

## 当前用户信息及技能工具使用习惯
{user_memory}

"""
        base2_enable_tools=f"""
## 可用基本工具
1. `skill_call` - 调用并激活技能。参数：{{"skill_name": "...", "user_request": "..."}}
2. `mcp_call` - 激活MCP服务，调用其提供的工具。参数：{{"server_name": "...","user_request": "..."}}
3. `execute_bash` - 执行bash/cmd/powershell命令(根据当前系统)。参数：{{"command": "...","command_type": "bash/cmd/powershell其中之一"}}
4. `read_file` - 读取文件、`read_file_and_linenum` - 读取文件(返回的内容带行号) 。参数：{{"path": "...","start_line_num":1,"lines":-1}}。
    - 文件限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `htm`, `xml`源文件，其他类型文件由其他工具处理.
    - 参数start_line_num：开始的行号，默认从1开始；
    - 参数lines：读取的行数，默认-1表示全部，500表示500行
5. `append_file` - 追加方式写入文件。参数：{{"path": "...", "content": "..."}}
6. `write_file` - 一次性写入文件。参数：{{"path": "...", "content": "..."}}
7. `insert_file_at_line` - 在文件指定行号前插入内容。参数举例：{{"path": "..."}}
8. `edit_file` - 修改文件的指定文本内容。参数：{{"path": "...", "old_text": ...,"new_text": ..., "replace_all": true/false}}
9. `execute_script` - 执行脚本。参数举例：{{"script_name": "...", "args": ["..."], "timeout": 30}}
{"10. `web_search` - 网络搜索。参数为：{\"query\": \"...\"}\n" if not self.base_configs.get("web_search",{}).get("disabled",True) else ""}
{"11. `gen_task_step` - 生成任务步骤（参数step_no，新任务从1开始递增，调整任务从调整位置开始递增）。参数：{\"task_steps\": [{\"step_no\":1,\"step_name\":\"步骤名\",\"step_desc\":\"步骤描述\"},...]}\n12. `update_task_step` - 更新Agent任务步骤(step_no为步骤序号)。参数：{\"step_no\": 1,\"new_status\":\"步骤状态，'1'-进行中,'2'-执行失败,'3'-已完成\",\"step_result\":\"步骤执行成功/失败结果\"}\n13. `get_task_process` - 获取Agent任务进度。无参数。" if self.agent_switch==2 else ""}
{"14. `pre_work_done` - 用于批量任务中标记前置工作已完成。无参数。\n15. `current_task_done` - 用于批量任务中标记当前任务已完成。无参数。" if self.agent_switch==2 and self.agent_task_batch_switch==1 else ""}


## 调用格式
当你需要使用工具及MCP工具时，严格按此格式回复：`<tool_call>工具名称 参数字典JSON</tool_call>`, 即：以`tool_call`类似xml tag包裹工具名称和参数字典JSON。
格式举例例如：  
<tool_call>skill_call {{"skill_name": "xx—skill", "user_request": "提取sample.pdf文本"}}</tool_call>  
<tool_call>mcp_call {{"server_name": "xx_mcp", "user_request": "调用工具query_user,查询用户xxx信息"}}</tool_call>   
<tool_call>execute_bash {{"command": "where xx","command_type": "cmd"}}</tool_call>  
<tool_call>read_file {{"path": "./xx.md","start_line_num":1,"lines":-1}}</tool_call>  
<tool_call>append_file {{"path": "./xx.md", "content": "追加内容"}}</tool_call>  
<tool_call>execute_script {{"script_name": "xx.py", "args": ["arg1", "arg2"], "timeout": 40}}</tool_call>  
{"<tool_call>web_search {\"query\": \"今天天气怎样？\"}</tool_call>\n" if not self.base_configs.get("web_search",{}).get("disabled",True) else ""}
{"<tool_call>gen_task_step {\"task_steps\":[{\"step_no\":1,\"step_name\":\"分析需求\",\"step_desc\":\"理解用户需求\"},{\"step_no\":2,\"step_name\":\"执行操作\",\"step_desc\":\"执行具体任务\"}]}</tool_call>\n<tool_call>pre_work_done {}</tool_call>" if self.agent_switch==2 else ""}


调用工具后，用户并将执行结果以"<tool_results>...</tool_results>"形式返回给你，你可以继续处理。  

"""
        
        public_enable_skills_mcp=f"""
## 可用技能
{skills_list}

## 可用MCP服务
{self.mcp_when_to_use}

"""
        
        base_active_mcp = f"""  
## MCP服务激活  
当用户需求匹配某个MCP服务时，需要判断：提供的tools列表中是否包含此MCP服务提供的工具（MCP工具描述开头大致为“[由MCP服务`xxx`提供]”）？  
    - 不包含：那么调用`mcp_call`激活该MCP服务，这样提供的tools列表中将增加此mcp服务提供的工具。  
    - 包含：不要重复激活，直接调用对应工具  
> MCP服务没有匹配功能时，不要使用。
"""
    
        public_active_skill=f"""  
## 技能激活
当你调用 skill_call 后，你会获得该技能的详细指令。之后处理该任务时，请遵循技能指令。  
如果激活的技能说明中有参考文件，请根据必要使用`read_file`读取文件内容作为参考:  
    - 参考文件路径若以`@skill/`开头,如`@skill/reference.md`等，**以此直接**作为技能的参考文件路径(“读取文件”工具会自动将@skill替换为当前skill目录路径)。
    - 否则根据**技能路径**及**参考文件路径**获取其参考文件的完整路径

""" 
        
        # agent_task模式：添加任务规划指引和当前进度
        public_task = "" 
        if self.agent_switch == 2:
            public_task = f"""
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
        # 重排系统提示词，尽量保证头部不变，提高缓存命中率
        if self.llm_client.support_tool_call: # pyright: ignore
            base = public_header + public_enable_skills_mcp + base_active_mcp + public_active_skill + public_task
            base += f"  \n---  \n现在请开始处理用户的问题。\n  [今天:{datetime.now().strftime('%Y-%m-%d')}][默认工作目录:{WORKSPACE_DIR}]"
        else:
            base = public_header + base2_enable_tools + public_enable_skills_mcp + base_active_mcp + public_active_skill + public_task
            base += f"  \n---  \n现在请开始处理用户的问题，若要调用工具，严格按格式回复`<tool_call>工具名称 参数字典JSON</tool_call>`，其中：tool_call是个标签关键字，**不能修改**！\n  [今天:{datetime.now().strftime('%Y-%m-%d')}][默认工作目录:{WORKSPACE_DIR}]"
        return base


    def gen_chat_system_content(self):
        """ 生成一般 Chat的system信息 """
        return f"你是一个智能助手，根据用户的提问，直接、正确、绝不捏造信息地回答用户问题[今天:{datetime.now().strftime('%Y-%m-%d')}]"

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
        
        status_text = {"pending": "📋 待办","ongoing": "🔄 进行", "done": "✅ 完成", "failed": "❌ 失败"}
        c = 0
        for step in self.task_steps:
            c += 1 
            no = step.get("step_no", c)
            status = status_text.get(step.get("status", "pending"), "待执行")
            desc = f"{step.get('step_name', '')}: {step.get('step_desc', '')}"
            result = (step.get("result", "") or "-")
            lines.append(f"| {no} | {desc} | {status} | {result} |")
        
        return "\n".join(lines) + "\n"

    async def handle_gen_task_step(self, task_steps: list) -> str:
        """处理 gen_task_step 工具调用：接收并存储任务步骤列表"""
        if not task_steps or not isinstance(task_steps, list):
            return "[错误] task_steps 必须是非空数组"
        
        # 根据新任务的开始序号
        try:
            task_steps_1st_no = int(task_steps[0].get("step_no",1))
        except (TypeError, ValueError):
            task_steps_1st_no = 1
        if task_steps_1st_no == 1: # 步骤重新开始
            self.task_steps=[]
        else:  # 步骤调整
            if len(self.task_steps)>=task_steps_1st_no:
                del self.task_steps[task_steps_1st_no-1:]

        for step in task_steps:
            self.task_steps.append({
                "step_no": step.get("step_no", len(self.task_steps) + 1),
                "step_name": step.get("step_name", f"步骤{step.get('step_no', '?')}"),
                "step_desc": step.get("step_desc", ""),
                "status": "pending",
                "result": ""
            })
        
        # 返回格式化后的步骤列表供大模型确认
        steps_summary = "\n".join([
            f"  {s['step_no']}. [{s['step_name']}] {s['step_desc']}" 
            for s in self.task_steps
        ])
        return f"已生成 {len(self.task_steps)} 个任务步骤：\n{steps_summary}\n\n请按照步骤顺序逐一执行，每完成一步调用 `update_task_step` 更新状态。"

    async def handle_update_task_step(self, step_no: int, new_status: str, step_result: str = "") -> str:
        """处理 update_task_step 工具调用：更新步骤状态并刷新系统提示词"""
        # 查找对应步骤
        target_step = None
        for step in self.task_steps:
            if step["step_no"] == step_no:
                target_step = step
                break
        
        if not target_step:
            return f"[错误] 未找到步骤序号 {step_no}，当前步骤列表共 {len(self.task_steps)} 步。"
        new_status = str(new_status)
        # 更新状态
        if new_status == "1":
            target_step["status"] = "ongoing"
        elif new_status == "2":
            target_step["status"] = "failed"
        elif new_status == "3":
            target_step["status"] = "done"
        else:
            target_step["status"] = "pending"
        
        target_step["result"] = step_result or ""
        
        # 统计进度
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
            result += f"  \n → 下一步：步骤{next_step['step_no']}「{next_step['step_name']}」"
        
        if done + failed >= total:
            if self.agent_task_batch_switch == 0:
                result += "\n🎉 所有步骤已执行完毕！请向用户汇报该任务执行结果。"
            self.task_steps.clear() # 清理任务的步骤
        return result

    def handle_pre_work_done(self) -> str:
        """处理批量任务中，标签前置工作已完成"""
        self.agent_batch_pre_work_msg_len = len(self.messages) # 当前最后一个消息role为tool
        return "...前置工作已完成，开始批量任务..."

    def handle_current_task_done(self) -> str:
        """处理批量任务中，标记当前任务已经完成"""
        self._batch_cleanup_pending = True
        return "...已完成一个任务，开始下一个..."

    # ====================== 会话压缩与加载 ======================

    async def _compact_session(self):
        """压缩当前会话并保存到 his_sessions/ 目录"""
        # 1. 确保 his_sessions 目录存在
        his_dir = os.path.join(WORKSPACE_DIR, "his_sessions")
        os.makedirs(his_dir, exist_ok=True)

        # 2. 从 messages 中提取对话内容（跳过 system 消息）
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
            # 截断过长的内容
            if isinstance(content, str) and len(content) > 600:
                content = content[:600] + "\n...[截断]..."
            # 标记角色
            role_label = {"user": "用户", "assistant": "助手"}.get(role, role)
            conv_parts.append(f"## {role_label}\n{content}")

            # 追加 reasoning_content（思考过程）
            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                if len(str(reasoning)) > 300:
                    reasoning = str(reasoning)[:300] + "..."
                conv_parts.append(f"[思考过程]: {reasoning}")

            # 追加 tool_calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tc_summary = []
                for tc in tool_calls:
                    fname = tc.get("function", {}).get("name", "?")
                    tc_summary.append(f"调用 `{fname}`")
                conv_parts.append(f"[工具调用]: {' → '.join(tc_summary)}")

        conversation_text = "\n\n".join(conv_parts)
        msg_count = sum(1 for m in self.messages if m.get("role") != "system")

        # 3. 调用 LLM 生成压缩摘要
        server_console.print(" 🧠 正在压缩会话（调用 AI 生成摘要）...")
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
            error_console.print(f" ❌ 压缩失败（AI 调用异常）: {e}")
            return None

        # 4. 从第一条用户消息中提取主题，生成文件名
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

        # 5. 写入文件
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

    async def _pick_session_file(self):
        """让用户从 his_sessions/ 中选择一个会话文件（纯交互，无 status 干扰）"""
        his_dir = os.path.join(WORKSPACE_DIR, "his_sessions")
        if not os.path.isdir(his_dir):
            error_console.print(f" ❌ 目录不存在: {his_dir}")
            return None

        files = sorted(
            [f for f in os.listdir(his_dir) if f.startswith("session-") and f.endswith(".md")],
            reverse=True
        )
        if not files:
            error_console.print(" ❌ his_sessions/ 中没有已保存的会话文件")
            return None

        self.showItemIn3Cols(
            "📂 已保存的会话文件",
            [f"[cyan]{i+1}[/cyan]. {f}" for i, f in enumerate(files)],
            cols=1
        )
        choice = Prompt.ask(
            "[bold cyan]选择要加载的会话 (输入编号，或按 Enter 取消)[/bold cyan]",
            default=""
        )
        if not choice.strip():
            server_console.print(" ℹ️ 已取消加载")
            return None

        try:
            idx = int(choice.strip()) - 1
            if idx < 0 or idx >= len(files):
                error_console.print(" ❌ 无效的编号")
                return None
        except ValueError:
            error_console.print(" ❌ 请输入有效数字")
            return None

        return os.path.join(his_dir, files[idx]), files[idx]

    async def _load_session_file(self, filepath, filename):
        """读取已保存的会话文件并作为 assistant 消息插入到消息列表（不修改 system prompt，避免破坏缓存）"""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        load_message = f"""---
## 已加载的压缩会话

以下是从 `{filename}` 加载的之前压缩的会话摘要，可以基于此继续之前的任务：

{content}

---"""

        # 作为 assistant 消息插入到 system 消息之后
        # 用 assistant 角色表示"这是已知的背景上下文"，避免连续 user 消息
        if len(self.messages)>=2 and self.messages[0].get("role") == "system" and self.messages[1].get("role") != "assistant":
            self.messages.insert(1, {"role": "assistant", "content": load_message})
        else:
            return None
        return filename

    async def handle_ask_user(self, choices: list) -> str:
        """向用户展示各项选择，等待用户选择后返回结果。"""
        if not choices:
            return "[ERROR] 选项列表不能为空"
        # 默认追加一个"都不选择"的选项
        all_choices = list(choices) + ["# 都不选择（跳过）"]
        options = [(str(i), HTML(f"<ansigreen>{escape(c)}</ansigreen>")) for i, c in enumerate(all_choices)]
        input_selection = ChoiceInput(
            message=HTML("<cyan><b>🤔 请选择一个选项</b></cyan>"),
            options=options,
            style=Style.from_dict({"frame.border": "#884444", "selected-option": "fg:#884444 bold"}),
            show_frame=True,
            bottom_toolbar=HTML("Use [↑][↓]·[Enter] to confirm, [Esc] to cancel.")
        )
        try:
            result = await input_selection.prompt_async()
            chosen = all_choices[int(result)]
        except (KeyboardInterrupt, EOFError):
            return "[用户取消了选择]"
        return chosen


    async def initialize_servers(self) -> None:
        """Initialize all servers."""
        self.servers = [
            Server(name, srv_config)
            for name, srv_config in self.base_configs["mcp_servers"].items() if not srv_config.get("disabled")
        ]
        for server in self.servers:
            try:
                await server.initialize()
            except Exception as e:
                error_console.print(f" ❌ 初始化MCP服务 {server.name} 失败，请检查configs.json配置。")
                self.invalid_servers.add(server)
    
    async def reinitialize_servers(self,new_servers: list[Server]) -> None:
        """Reinitialize all servers."""
        await self.cleanup_servers()
        self.servers = new_servers
        for server in self.servers:
            try:
                server.exit_stack = AsyncExitStack()
                await server.initialize()
            except Exception as e:
                error_console.print(f" ❌ 初始化MCP服务{server.name} 失败，请检查configs.json配置。")
                self.invalid_servers.add(server)

    def showSysInfo(self,msg:str|Markdown|Text,title:str,subtitle:str|None=None,is_tool_call=False):
        """Show system info."""
        sys_info_pannel = Panel(
            msg,
            title=title,
            title_align="left",
            subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
            subtitle_align="right",
            padding=(1, 2)
        )
        if is_tool_call:
            sys_info_pannel.border_style = "blue"
        console.print(sys_info_pannel)

    def assistantResponse(self,msg:str|Markdown|Text,subtitle:str|None=None):
        panel = Panel(
            msg,
            title="[Assistant 🤖]",
            title_align="left",
            border_style="green",
            subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
            subtitle_align="right",
            box = NoSlideBox(),
            padding=(1, 2)
        )
        return panel

    
    def showItemIn3Cols(self,title,items:list[str],cols:int=3):
        """
        将一些短信息列表分对齐列显示
        """
        table = Table(box=None,show_header=False,width=console.width)
        tmpRows=[]
        for idx,item in enumerate(items):
            if (idx+1) % cols !=0:
                tmpRows.append(item)
                if idx+1 == len(items):
                    table.add_row(*tmpRows)
            else:
                tmpRows.append(item)
                table.add_row(*tmpRows)
                tmpRows.clear()
        main_panel = Panel.fit(table, title=title,title_align="left")
        console.print(main_panel)      
    
    def getAIModelInfo(self)->Text:
        """
        获取AI模型相关信息
        """
        fillMarks_len = (console.width - (len(self.llm_client.ai_channel)+len(self.llm_client.ai_model)+len(self.llm_client.ai_provider)+36))//2 - 10 # pyright: ignore
        modleInfo = ""
        modleInfo+="─"*fillMarks_len + ("  " if fillMarks_len>0 else "")
        modleInfo+=self.llm_client.ai_channel
        modleInfo+="  |  "
        modleInfo+=self.llm_client.ai_model
        modleInfo+="  |  "
        modleInfo+=self.llm_client.ai_provider
        modleInfo+="  |  "
        modleInfo+=f"{'AGENT OFF' if self.agent_switch==0 else ('AGENT ON' if self.agent_switch==1 else 'AGENT TASK')}"
        modleInfo+=("  " if fillMarks_len>0 else "") + "─"*fillMarks_len
        margin_fix = " " * max((console.width - len(modleInfo))//2,0)
        modleInfo=margin_fix + modleInfo
        return modleInfo

    async def showAndGetAssistantResponse(self,call_llm: callable,subtitle:str|None=None)->typing.Tuple[str,dict|None,str|None]: # pyright: ignore
        """Show Assistant response.

        Args:call_llm (callable): A function that returns a tuple of (assistant_response, usage).
        Returns: opitimazied_assistant_response (str|list|dict),  assistant_response (str)
        """
        tool_calls = None
        reasoning_content = ""
        with Live(console=console,auto_refresh=False) as live:
            start_time = asyncio.get_running_loop().time()
            task = asyncio.create_task(call_llm())
            input_obj = create_input()
            waiting_spinners = ['∵','∴']
            spinner_idx=0
            try:
                while not task.done():
                    break_flag = False
                    with input_obj.raw_mode():
                        for key in input_obj.read_keys():
                            if key.data == '/' or key.key == '/':
                                break_flag = True
                                break
                    if break_flag:
                        if not task.done(): task.cancel()
                    cur_spinner=waiting_spinners[spinner_idx]
                    elapsed = asyncio.get_running_loop().time() - start_time
                    process_info = f"Waiting: {cur_spinner} Cost [bold red]{elapsed:.2f}[/bold red] Sec"
                    assistant_panel = self.assistantResponse(process_info,escape("Press [/] to Cancel"))
                    live.update(assistant_panel,refresh=True)
                    await asyncio.sleep(0.2)  # 降低 CPU 占用
                    spinner_idx = (0 if spinner_idx else 1)
                result,usage,tool_calls,reasoning_content = task.result()
            except (asyncio.CancelledError,Exception) as e:
                result,usage,tool_calls,reasoning_content = "[*用户中断了大模型输出*]" if not(str(e)) else f"⚠️ Exception Occurred: {str(e)}",None,None,None
            finally:
                input_obj.close()
            if usage: # 更新usage
                if not self.usage:
                    self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                self.usage["prompt_tokens"] += int(usage.get("prompt_tokens",0))
                self.usage["completion_tokens"] += int(usage.get("completion_tokens",0))
                self.usage["total_tokens"] += int(usage.get("total_tokens",0))
            if not result:
                show_result = result = "[Error] Received empty response from LLM"
            else:
                show_result  = result.strip()
            cost_info = f"✅ Cost {elapsed:.2f} Sec"
            if reasoning_content:
                reasoning_content = "".join(reasoning_content[:500].splitlines())
            show_result = show_result + (f"  \n\n---  \n> **思考过程**：*{reasoning_content}*..." if reasoning_content else "")
            assistant_panel = self.assistantResponse(Markdown(show_result,code_theme=self.markdown_theme),cost_info if not subtitle else f"{cost_info} | {subtitle}")
            live.update(assistant_panel,refresh=True)
        if tool_calls and not result.strip():
            func_names = [func.get('function',{}).get("name","API格式不匹配") for func in tool_calls]
            result = f"🔔 我找到如下工具需调用: {",".join(func_names)} ..."
        if not tool_calls and not result.strip():
            result = "[Error] Received empty response from LLM"
        return result,tool_calls,reasoning_content

    async def showAndGetAssistantResponseStream(self,llmClient:LLMClient,messages:list[dict[str, str]],use_tool_call:bool=False,tools=base_tools)-> typing.Tuple[str,list|None]:
        """Show Assistant response. Stream version. only for no mcp mode
        Returns: assistant_response (str)
        """
        src_response = ""
        tool_calls = []
        tool_args_str = ""
        reasoning_content = ""
        start_time = asyncio.get_running_loop().time()
        with Live(console=console, refresh_per_second=2) as live:
            input_obj = create_input()
            break_flag = False

            try:
                async for code,chunk in llmClient.yield_response(messages,use_tool_call,tools):
                    break_flag = False
                    with input_obj.raw_mode():
                        for key in input_obj.read_keys():
                            if key.data == '/' or key.key == '/':
                                break_flag = True
                                break
                    if break_flag:
                        break
                    if code == 2: # 表示token使用信息
                        usage = chunk
                        if not usage or not isinstance(usage,dict):
                            continue
                        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens",0))
                        self.usage["completion_tokens"] += int(usage.get("completion_tokens",0))
                        self.usage["total_tokens"] += int(usage.get("total_tokens",0))
                    elif code == 31: # 新工具定义开始
                        if len(tool_calls)>=1: # 如果之前有工具定义，则保存尾部工具参数
                            tool_calls[-1]["function"]["arguments"]=tool_args_str
                        tool_calls.append(json.loads(chunk))
                        tool_args_str=""
                    elif code == 32:
                        tool_args_str+=chunk
                    elif code == 4:
                        reasoning_content += chunk
                        resp_lines = reasoning_content.splitlines()
                        show_content = ("•••  \n" if len(resp_lines) > 10 else "")+ "\n".join(resp_lines[-10:])
                        assistant_panel = self.assistantResponse(show_content,f"🧠 思考中({escape("按【/】取消")}...)")
                        live.update(assistant_panel, refresh=True)
                    else: # 将新内容追加到文本对象
                        src_response += chunk
                        resp_lines = src_response.splitlines()
                        show_content = ("•••  \n" if len(resp_lines) > 10 else "")+ "\n".join(resp_lines[-10:])
                        assistant_panel = self.assistantResponse(show_content,f"🏭 生成中({escape("按【/】取消")}...)")
                        live.update(assistant_panel, refresh=True)
                    await asyncio.sleep(0.05)  # 降低 CPU 占用
                    continue
            except (asyncio.CancelledError,Exception) as e:
                src_response = f" ⚠️ 发生了异常：{traceback.format_exc()}"
                error_console.print(src_response)
            finally:
                input_obj.close()
                elapsed = asyncio.get_running_loop().time() - start_time
            cost_info = f"✅ Cost {elapsed:.2f} Sec"
            if break_flag:
                src_response += f"  \n\n[*用户中断了大模型输出*]"
                tool_calls = []
                reasoning_content = ""
            elif len(tool_calls)>=1:
                tool_calls[-1]["function"]["arguments"]=tool_args_str
                if not src_response.strip():
                    func_names = [func["function"].get("name","API格式不匹配") for func in tool_calls]
                    src_response = f"🔔 我有如下工具需调用: {",".join(func_names)} ..."
            elif not src_response.strip():
                src_response = "[Error] 大模型没有进行任何回复，可能出现了异常，可重新开始新的对话"
            if reasoning_content:
                reasoning_content = "".join(reasoning_content[:500].splitlines())
            show_result =  src_response.strip() + (f"  \n\n---  \n> **思考过程**：*{reasoning_content}*..." if reasoning_content else "")
            assistant_panel = self.assistantResponse(Markdown(show_result, code_theme=self.markdown_theme),cost_info)
            live.update(assistant_panel, refresh=True)
        
        return src_response,tool_calls,reasoning_content

    
    def mcpToolCalledPanel(self,toolName:str,args: None | dict,process_info:str=None,out_put:str=None,subtitle:str=None):
        """Show Tool called."""
        process_msg=(
            f"工具: [bold yellow]{toolName}[/bold yellow]\n"
            f"参数: [bold light_sea_green]{args}[/bold light_sea_green]"
            f"{('\n'+process_info) if process_info else ''}"
        )
        result_msg = Markdown(
f"""工具 `{toolName}` 的执行结果: {('\n'+out_put) if out_put else ''}""",
code_theme=self.markdown_theme
        )
        tool_panel = Panel(
                process_msg if not out_put else result_msg,
                title="[MCP Result 🔧]",
                title_align="left",
                border_style="magenta",
                subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
                subtitle_align="right",
                box = NoSlideBox(),
                padding=(1, 2)
            )
        return tool_panel



    def switch_model(self, model_no: str):
        """Switch the model of the LLM client.
        Args:
            model_no: The model number to switch to.
        Returns:
            True if the model was switched successfully, False otherwise.
        """
        model_info = self.client_models.get(model_no)
        if not model_info:
            error_console.print(f" ❌ 无效的模型编号: {model_no}")
            return
        if model_info["api_style"].lower() == "openai":
            self.llm_client = LLMClient(api_key=model_info["api_key"],
                                        ai_channel=model_info["ai_channel"],
                                        ai_model=model_info["ai_model"],
                                        ai_api_url=model_info["ai_api_url"],
                                        ai_provider=model_info["ai_provider"],
                                        support_tool_call=model_info["support_tool_call"],
                                        support_stream=model_info["support_stream"],
                                        support_thinking=model_info["support_thinking"],
                                        support_multimodal=model_info.get("support_multimodal",False),
                                        http_proxy=model_info["api_proxy"])
        elif model_info["api_style"].lower() == "fosp":
            fosp_keys = model_info["api_key"].split(",")
            if len(fosp_keys)!=2:
                error_console.print(f" ❌ 无效的FOSP API Key: {model_info['api_key']}, 应该是2个key(open_id & developer_secret) separated by comma")
                return
            self.llm_client = LLMClient2(fosp_open_id=fosp_keys[0],
                                         fosp_developer_secret=fosp_keys[1],
                                         ai_channel=model_info["ai_channel"],
                                         ai_model=model_info["ai_model"],
                                         ai_api_url=model_info["ai_api_url"],
                                         ai_provider=model_info["ai_provider"],
                                         support_tool_call=model_info["support_tool_call"],
                                         support_stream=model_info["support_stream"],
                                         support_thinking=model_info["support_thinking"],
                                         http_proxy=model_info["api_proxy"])
        else:
            error_console.print(f" ❌ 不支持的AI API 风格: {model_info["api_style"]}")
      

    async def cleanup_servers(self) -> None:
        """Clean up all servers properly."""
        for server in reversed(self.servers):
            try:
                await server.cleanup()
            except Exception as e:
                error_console.print(f" ❌ 清理MCP服务 {server.name} 时发生异常: {e}")
        self.servers = []
            

    async def process_use_prompt(self, input_prompt: str) -> list[PromptMessage]|None:
        """"
        Process the use prompt and return the list of prompts.
        Args:
            input_prompt: The prompt to process.
        Returns:
            The list of prompts.
        """
        selected_prompt = None
        selected_server = None
        for server in self.servers:
            if server in self.invalid_servers:
                continue
            try:
                mcp_prompts = await server.list_prompts()
            except RuntimeError as e:
                error_console.print(f" ❌ 无法从此MCP服务获取prompts列表 - {server.name}. {str(e)}")
                continue
            if not mcp_prompts:
                continue
            for mcp_prompt in mcp_prompts:
                if mcp_prompt.name == input_prompt:
                    selected_prompt = mcp_prompt
                    selected_server = server
                    break
            else:
                continue
            break
        if not selected_prompt:
            return None
        self.showSysInfo(selected_prompt.format_for_rich,"[Selected Prompt]","If Arguments are present, please fill them.")
        args = {}
        fill_arg_session=PromptSession()
        if selected_prompt.arguments:
            for arg in selected_prompt.arguments:
                user_input = (await fill_arg_session.prompt_async(HTML(f"> Fill <cyan>{arg.name}</cyan>: "),multiline=False,vi_mode=True)).strip()
                if not user_input and not arg.required:
                    continue
                args[arg.name] = user_input
        fill_arg_session=None
        tool_resp_prompts = await selected_server.get_prompt(selected_prompt.name, args)
        if not tool_resp_prompts:
            return None
        return tool_resp_prompts.messages
                
    async def process_mcp_response(self,server:Server|str, tool_call:dict) -> str:
        """Process the LLM response and execute tools if needed.
        Args:
            tool_call: tool_call dict.
        Returns:
            The result of tool execution or the original response.
        """
        if server in self.invalid_servers:
            return f"No server found with tool: {tool_call['name']}"
        try:
            with Live(auto_refresh=False) as live:
                start_time = asyncio.get_running_loop().time()
                task = asyncio.create_task(server.execute_tool(tool_call["name"], tool_call.get("arguments")))
                # 实时计算并显示耗时
                while not task.done():
                    elapsed = asyncio.get_running_loop().time() - start_time
                    process_info = f"Running: 🕒 Cost [bold red]{elapsed:.2f}[/bold red] Sec"
                    ctPanel = self.mcpToolCalledPanel(tool_call["name"],tool_call.get("arguments"),process_info=process_info)
                    live.update(ctPanel,refresh=True)
                    await asyncio.sleep(0.1)  # 降低 CPU 占用  
                result = task.result()
                if result.content and result.content[0].type=='text':
                    calledRst = result.content[0].text.strip()
                    try:
                        data=json.loads(calledRst)
                        out_put = f"""\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"""
                    except Exception as e:
                        out_put = f"\n{calledRst}"
                else:
                    out_put = calledRst = f"{str(result.content).strip()}"
                finish_info = f"{'❌' if result.isError else '✅'} Cost {elapsed:.2f} Sec"
                ctPanel = self.mcpToolCalledPanel(tool_call["name"],tool_call.get("arguments"),out_put=out_put,subtitle=finish_info)
                live.update(ctPanel,refresh=True)
            return f"{calledRst}"
        except Exception as e:
            error_console.print(f"❌ {str(e)}")
            return  f"Tool Execute Failed:\n{str(e)}"

    def get_tool_details(self,all_tools:list[Tool],tool_part_name: str) -> str:
        """
        Show the details of the tools that match the given part of the name.
        Args:   
            tool_part_name: The part of the name to match.
        """
        if tool_part_name == "*":
            filter_tools = all_tools
        else:
            filter_tools = [tool for tool in all_tools if tool_part_name.lower() in tool.name.lower()]
        if not filter_tools:
            return "No tools found with that name."
        tools_details = "\r\n".join([
            f"+ [bold blue]{tool.server_name}[/bold blue] - [bold yellow]{tool.name}[/bold yellow]" + \
            (f"\n  > [magenta]description[/magenta]: {tool.description.strip()}" if tool.description else "")
             for tool in filter_tools])
        return tools_details
    
    def get_prompt_details(self,all_prompts:list[MCPPrompt],prompt_part_name: str) -> str:
        """
        Show the details of the prompts that match the given part of the name.
        Args:   
            prompt_part_name: The part of the name to match.
        """
        # Filter prompts that match the given part of the name
        if prompt_part_name == "*":
            filter_prompts = all_prompts
        else:
            filter_prompts = [prompt for prompt in all_prompts if prompt_part_name.lower() in prompt.name.lower()]  
        # If no prompts match, return a message
        if not filter_prompts:
            return "No prompts found with that name."
        # Join the details of the prompts into a string
        prompts_details = "\r\n".join([prompt.format_for_rich for prompt in filter_prompts])
        # Return the details
        return prompts_details
    
    def showTitle(self):
        # patorjk.com
        margin_left_len = (server_console.width-30)//2-1
        server_console.print(
            " "*margin_left_len+"┏┳┓•      ┏┓┓       ┏┓┓•     "+"\n"+
            " "*margin_left_len+" ┃ ┓┏┓┓┏  ┃ ┃┏┓┓┏┏  ┃ ┃┓┏┓┏┓╋"+"\n"+
            " "*margin_left_len+" ┻ ┗┛┗┗┫  ┗┛┗┗┻┗┻┛  ┗┛┗┗┗ ┛┗┗(v2.5)"+"\n"+
            " "*margin_left_len+"       ┛                     "
        )
        self.showSysInfo(("[blue]对话[/blue]: 输入查询信息，等待大模型回答\n"
            "[blue]模式[/blue]: 命令`/agent、/agent-task`开启Agent、Task模式，可以进行调用工具、技能、MCP、联网搜索\n"
            "[blue]功能[/blue]: AI对话，调用工具、MCP、SKILLS、联网搜索\n"
            "[blue]命令[/blue]: 输入【/】联想命令列表, 使用【↑↓】·【Enter】确定\n" 
            "[blue]输入[/blue]: 【Ctrl+J】换行, 按【Esc】使用VI命令(【g·g】回开头,【a/i】插入,【d·d】删行...)"),
            "[快速开始]",subtitle="输入 /help 💡") # 展示当前模型信息

    def creatNewLog(self):
        """
        创建一个新的日志文件
        """
        if self.log_file and not self.log_file.closed:
            self.log_file.close()
        formatted_time = datetime.now().strftime('%Y%m%d')
        new_log_file_path =  f"logs/{formatted_time}.md"
        if not os.path.exists(new_log_file_path):
            os.makedirs(os.path.dirname(new_log_file_path), exist_ok=True)
        self.log_file = open(new_log_file_path,mode="a+",encoding='utf-8')

    def appendInfo2Log(self,role:str,info:str):
        """
        日志追加记录
        """
        if self.log_file.name != f"logs/{datetime.now().strftime('%Y%m%d')}.md": # 如果当前日志文件不是今天的日志文件，则创建一个新的日志文件
            self.creatNewLog()
        log = f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[{role}]  \n{info}  \n\n"
        
        self.log_file.write(log)
        self.log_file.flush()

    def handle_skill_call(self, skill_name: str, user_request: str) -> str:
        skill = load_skill_full(skill_name)
        if not skill:
            return f"错误：技能 '{skill_name}' 不存在。"
        old_skill_desc = ""
        if self.active_skill:
            old_skill_desc = f"（之前激活的技能`{self.active_skill.name}`已替换）\n"
        self.active_skill = skill
        return f"""技能`{skill.name}`已激活{old_skill_desc}。以下是技能指令：
{skill.instruction}
---
现在请按照技能指令处理用户的请求：
{user_request}"""

    async def handle_mcp_call(self,server_name:str, user_request:str) -> str:
        query_servers = list(filter(lambda x:x.name.lower().strip() == server_name.lower().strip() ,self.servers))
        if not query_servers:
            return f"⚠️ No such MCP server - {server_name} !"    
        self.active_mcp = query_servers[0] # MCPServer
        if self.llm_client.support_tool_call:
            return f"MCP服务`{server_name}`已激活，请立刻继续处理用户的请求：  \n{user_request}"
        else:
            current_mcp_tools_desc = "\n\n".join([f"{i + 1}、{tool.format_for_llm()}" for i, tool in enumerate(await self.active_mcp.list_tools())])
            return f"""MCP服务`{server_name}`已激活，以下是其提供的工具信息：  
{current_mcp_tools_desc}  
---  
请立刻继续处理用户的请求： 
{user_request}"""

    async def run_flow(self, flow_path: str, flow_name: str, user_request: str):
        """运行 AgentFlow 工作流，使用 Live + Panel 流式显示进度。

        类似于 showAndGetAssistantResponseStream，使用 Rich Live 实时刷新 Panel，
        展示工作流的 STREAMING/THINKING/TOOL_CALL 等事件。
        """
        streaming_buffer = ""
        thinking_buffer = ""
        log_lines: list[str] = []          # 所有事件日志行（显示在 Panel 内）
        runner: Optional[AgentFlowRunner] = None
        flow_result: Optional[dict] = None
        flow_error: Optional[str] = None
        cancelled = False

        def _on_flow_event(event: FlowSessionEvent):
            nonlocal streaming_buffer, thinking_buffer, log_lines
            et = event.type
            d = event.data or {}

            if et == FlowEventType.SYSTEM_INFO:
                msg = d.get("message", "")
                if msg:
                    log_lines.append(f"  {msg}")
            elif et == FlowEventType.TOOL_CALL:
                name = d.get("tool_name", "?")
                args = d.get("arguments", {})
                s = json.dumps(args, ensure_ascii=False)
                log_lines.append(f"  🔧 {name}({s[:80]})")
            elif et == FlowEventType.TOOL_RESULT:
                icon = "✅" if d.get("status") == "success" else "❌"
                result = str(d.get("result", ""))[:200]
                log_lines.append(f"  {icon} {result}")
            elif et == FlowEventType.STREAMING:
                streaming_buffer += d.get("content", "")
            elif et == FlowEventType.THINKING:
                thinking_buffer = d.get("content", "")
            elif et == FlowEventType.ERROR:
                msg = d.get("message", "")
                log_lines.append(f"  ❌ {msg}")
            elif et == FlowEventType.STREAM_DONE:
                # 将累积的流式内容归档到日志行，清空缓冲区
                if streaming_buffer.strip():
                    log_lines.append(f"  {streaming_buffer.strip()}")
                    streaming_buffer = ""
                thinking_buffer = ""

        start_time = asyncio.get_running_loop().time()
        log_lines.append(f"🚀 工作流: {flow_name}")
        log_lines.append(f"需求: {user_request[:100]}")
        log_lines.append("")
        log_lines.append(f"  ▶ 启动引擎...")
        log_lines.append(f"  ▶ 开始执行...")

        with Live(console=console, refresh_per_second=4) as live:
            try:
                runner = AgentFlowRunner(
                    flow_path,
                    event_callback=_on_flow_event,
                )
                runner.load()

                # 启动执行任务
                task = asyncio.create_task(
                    runner.run(initial_context={"user_request": user_request})
                )

                # 轮询更新 Live panel，同时检测 / 取消
                input_obj = create_input()
                while not task.done():
                    # 检测取消按键
                    break_flag = False
                    with input_obj.raw_mode():
                        for key in input_obj.read_keys():
                            if key.data == '/' or key.key == '/':
                                break_flag = True
                                break
                    if break_flag:
                        if not task.done():
                            runner.cancel()
                            task.cancel()
                        cancelled = True
                        break

                    # 构建流式面板：最后15行滚动渲染
                    elapsed = asyncio.get_running_loop().time() - start_time
                    # 从日志行开始，全部展开为单行列表
                    show_lines = []
                    for line in log_lines:
                        show_lines.extend(line.splitlines())

                    if thinking_buffer.strip() and not streaming_buffer.strip():
                        resp_lines = thinking_buffer.splitlines()
                        show_lines.append(f"💭 思考中...")
                        show_lines.extend(resp_lines[-10:])
                        subtitle = f"💭 思考中({escape('按【/】取消')}...) | Cost {elapsed:.1f}s"
                    elif streaming_buffer.strip():
                        resp_lines = streaming_buffer.splitlines()
                        show_lines.append("")
                        show_lines.extend(resp_lines[-10:])
                        subtitle = f"🏭 生成中({escape('按【/】取消')}...) | Cost {elapsed:.1f}s"
                    else:
                        subtitle = f"Cost {elapsed:.1f}s | {escape('按【/】取消')}"

                    show_content = "\n".join(show_lines[-15:]) if show_lines else "⏳ 工作流运行中..."

                    panel = Panel(
                        show_content,
                        title=f"[📋 Flow: {flow_name}]",
                        title_align="left",
                        border_style="magenta",
                        subtitle=f"[gray37]{subtitle}[/gray37]",
                        subtitle_align="right",
                        box=NoSlideBox(),
                        padding=(1, 2)
                    )
                    live.update(panel, refresh=True)
                    await asyncio.sleep(0.1)

                input_obj.close()

                if not cancelled:
                    flow_result = task.result()
            except (asyncio.CancelledError, Exception) as e:
                if not cancelled:
                    flow_error = str(e)
                    log_lines.append(f"  ❌ 异常: {e}")

            # 最终结果展示
            elapsed = asyncio.get_running_loop().time() - start_time
            if cancelled:
                result_text = "⏹ 用户中断了工作流"
                border_style = "yellow"
                cost_subtitle = f"⏹ Cost {elapsed:.1f}s"
            elif flow_error:
                result_text = f"❌ 工作流异常: {flow_error}"
                border_style = "red"
                cost_subtitle = f"❌ Cost {elapsed:.1f}s"
            elif flow_result and flow_result.get("success"):
                n_steps = len(flow_result.get("outputs", {}))
                result_text = f"✅ 工作流执行成功！完成 {n_steps} 个步骤"
                outputs = flow_result.get("outputs", {})
                if outputs:
                    result_text += "\n\n[bold]📝 各步骤输出预览:[/bold]"
                    for step_id, out in outputs.items():
                        preview = str(out)[:200].replace("\n", " ")
                        result_text += f"\n  [{step_id}] {preview}..."
                border_style = "green"
                cost_subtitle = f"✅ Cost {elapsed:.1f}s"
            elif flow_result:
                result_text = "❌ 工作流执行失败"
                for e in flow_result.get("errors", []):
                    result_text += f"\n  ⚠️  {e}"
                border_style = "red"
                cost_subtitle = f"❌ Cost {elapsed:.1f}s"
            else:
                result_text = "⚠️ 工作流未返回结果"
                border_style = "yellow"
                cost_subtitle = f"⚠️ Cost {elapsed:.1f}s"

            final_panel = Panel(
                Markdown(result_text, code_theme=self.markdown_theme),
                title=f"[📋 Flow: {flow_name}]",
                title_align="left",
                border_style=border_style,
                subtitle=f"[gray37]{cost_subtitle}[/gray37]",
                subtitle_align="right",
                box=NoSlideBox(),
                padding=(1, 2)
            )
            live.update(final_panel, refresh=True)

        # log
        self.appendInfo2Log("flow", f"工作流: {flow_name}\n需求: {user_request}\n结果: {result_text[:500]}")

    async def start_new_chat(self):
        # 清理会话
        del self.messages[:]
        self.img_path_list.clear()
        self.active_skill = None
        self.active_mcp = None
        self.task_steps = []
        self.agent_task_batch_switch = 0 # 恢复非批量模式
        self.agent_batch_pre_work_msg_len = 1
        self._batch_cleanup_pending = False
        self.usage ={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if self.agent_switch == 0:
            self.messages.append({"role": "system", "content": self.gen_chat_system_content()})
        else:
            self.messages.append({"role": "system", "content": await self.gen_agent_system_content()})
        server_console.print(" ℹ️ 开始了新对话 ...")
        # log
        self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")
    
    async def start(self) -> None:
        """Main chat session handler."""
        global base_tools
        prompt_style = Style.from_dict(
            {
                "frame.border": "#884444",
                "accepted frame.border": "gray",
                'bottom-toolbar': 'bg:#ansimagenta bold',
                'toolbar.info': '#ansicyan',  # 自定义更细粒度的样式类
            }
        )
        try:
            # 开启log
            self.creatNewLog()
            # 设置LLM Client
            self.switch_model('1')

            # 设置工具
            self.active_skill = None
            # 设置消息
            self.messages = []

            # help
            help_items=[
                ("关于输入","【Ctrl+J】换行, 按【Esc】使用VI命令(【g·g】回开头,【a/i】插入,【d·d】删行...)"),
                ("使用命令","键入【/】显示命令列表, 键入【Tab】·【↑↓】选择，键入【Enter】确认."),
                ("关于MCP Prompts", "输入命令/usmp后, 键入[Tab]或内容显示选项, 使用[↑][↓]·[Enter]确认."),
                ("关于Configs","\".env\": 环境变量设置; [b]\"configs.json\"[/b]: 配置AI大模型及MCP服务、网络搜索"),
                ("关于MCP设置","采用渐进式披露，提供[b]\"when-to-use\"[/b]来描述该Server下的工具作用及调用时机"),
                ("关于工作区",f"目录：[b]{WORKSPACE_DIR}[/b]，其下AGENT.md中可以编写Skill、MCP工具的使用习惯"),
                ("关于技能","[b]skills[/b]目录下存放自定义或从github下载的技能"),
                ("关于工作流","[b]flows[/b]目录下存放AgentFlow工作流YAML，使用/flow:命令运行"),
                ("关于日志",f"[b]logs[/b]目录下是每天的对话记录历史")
            ]
            # 命令处理格式
            commands_item=[
                ("/agent","开启agent模式"),
                ("/agent-task","开启agent-task模式"),
                ("/batch ","agent-task模式下批量任务"),
                ("/agent-off","关闭agent模式"),
                ("/lst","列出所有工具"),
                ("/smtd *","显示MCP工具详情"),   
                ("/lsmp","列出MCP Prompts"),    
                ("/smpd *","显示MCP Prompt详情"),
                ("/snc","开始新对话"),
                ("/cls","清理屏幕"),
                ("/clh","清理输入历史"),
                ("/usmp","使用MCP Prompt"),
                ("/swt","切换Markdown主题"),
                ("/swm","切换AI大模型"),  
                ("/img","上传图片(仅支持多模态的大模型)"),
                ("/reload","重新加载配置文件"),
                ("/stu","显示本轮对话Token使用量"),
                ("/compact","压缩当前会话并保存到 his_sessions/"),
                ("/load","加载已保存会话的快捷命令"),
                ("/help","显示帮助信息"),
                ("/log","查看日志文件路径"),
                ("/exit","退出本应用")
            ]
            short_commands_map={x:HTML(f"<red>{x}</red>:{y}") for x,y in commands_item}
            short_commands = [k[0] for k in commands_item]
            # 动态 /skill:<name> 命令补全（仅当技能已加载时）
            skill_cmd_items = []
            # 动态 /flow:<name> 命令补全
            flow_cmd_items = []
            for fm in load_flows_metadata():
                fc = f"/flow:{fm['name']}"
                flow_cmd_items.append(fc)
                short_commands_map[fc] = HTML(f"<red>{fc}</red> <cyan>输入你的需求</cyan>")
            short_commands.extend(flow_cmd_items)
            # 开头的帮助提示信息
            
            self.showTitle()
            
            # 具体的工具及变量信息
            all_tools = []
            all_tools_nameFormat = []
            all_prompts = []
            all_prompts_nameFormat = []
            # 展示base tools
            if not self.base_configs.get("web_search",{}).get("disabled",True):
                base_tools.append(web_search_tool)
            base_tool_names = [tool["function"]["name"]  for tool in base_tools]
            base_tools_nameFormat=""
            for idx,tool in enumerate(base_tool_names):
                base_tools_nameFormat += f"+ [blue]{tool}[/blue]" + ((" | " if (idx+1) % 4 != 0 else "\n") if idx < len(base_tools)-1 else "")  
            # 展示所有tools
            tools_name = ""
            prompts_name = ""
            async def load_mcp_servers_info():
                """
                Load the tools and prompts from all servers.
                """
                nonlocal tools_name, prompts_name # 需要修改
                all_tools.clear()
                all_tools_nameFormat.clear()
                all_prompts.clear()
                all_prompts_nameFormat.clear()
                self.mcp_when_to_use = ""
                self.active_mcp = None
                for server in self.servers:
                    if server in self.invalid_servers:
                        continue
                    tools = await server.list_tools()
                    if tools is None: # 针对遇到Server异常情况
                        self.invalid_servers.add(server)
                        continue
                    if not tools:
                        # 获取所有tools
                        continue
                    # 获取所有tools
                    all_tools.extend(tools) 
                    # 根据server分类tools，将tools的名称每3个一行合并展示
                    ser_tools_nameFormat=""
                    for idx,tool in enumerate(tools):
                        ser_tools_nameFormat += f"+ [blue]{tool.name}[/blue]" + ((" | " if (idx+1) % 3 != 0 else "\n") if idx < len(tools)-1 else "")  
                    all_tools_nameFormat.append({
                        "server_name": server.name,
                        "ser_tools_nameFormat": ser_tools_nameFormat
                    })
                    # console.log(await server.get_prompt("Debug Assistant", {"error":"the arg xx is not definined"}))
                    mcp_prompts = await server.list_prompts()
                    all_prompts.extend(mcp_prompts)
                    ser_prompts_nameFormat=""
                    for idx,prompt in enumerate(mcp_prompts):
                        ser_prompts_nameFormat += f"+ [blue]{prompt.name}[/blue]" + ((" | " if (idx+1) % 3 != 0 else "\n") if idx < len(mcp_prompts)-1 else "")
                    all_prompts_nameFormat.append({
                        "server_name": server.name,
                        "ser_prompts_nameFormat": ser_prompts_nameFormat
                    })
                    self.mcp_when_to_use = self.mcp_when_to_use+"\r\n"+f"""
**Server**: {server.name}
**When-to-use**: {server.config.get("when_to_use","")}
""" # 核心prompts使用！！
                tools_name = "\r\n".join([f"[bold yellow]{tool['server_name']}[/bold yellow]\n{tool['ser_tools_nameFormat']}" for tool in all_tools_nameFormat])
                prompts_name = "\r\n".join([f"[bold yellow]{prompt['server_name']}[/bold yellow]\n{prompt['ser_prompts_nameFormat']}" for prompt in all_prompts_nameFormat])

            # 对话开始
            self.messages = [{"role": "system", "content": self.gen_chat_system_content()}]
            chat_start_time = datetime.now()

            # 对话内输入session
            inMemoryHistory = InMemoryHistory()
            input_session = PromptSession(history=inMemoryHistory)
            # 使用prompts的输入session
            prmt_session = PromptSession()
            self.img_path_list.clear()

            # Ctrl+J 换行（终端通用，类似 ipython/bash 行为）
            input_kb = KeyBindings()
            @input_kb.add(Keys.ControlJ)
            def _(event):
                event.current_buffer.insert_text('\n')

            while True:
                try:
                    console.print("") # 增加一个空行
                    cmd_completer = WordCompleter(short_commands, display_dict=short_commands_map,ignore_case=True,match_middle=False,sentence=True)
                    user_input = (await input_session.prompt_async(HTML("⌨︎ <cyan> > </cyan>"), completer=cmd_completer,
                                                                   multiline=False,  # 需要按Enter快速发送，而不是换行
                                                                   vi_mode=True, # 使用VI编辑模式
                                                                   key_bindings=input_kb,  # 通过自定义组合键换行
                                                                   bottom_toolbar=self.getAIModelInfo(),
                                                                   show_frame=False,
                                                                   style=prompt_style)).strip()
                    # 超过超时时间，重新开始
                    if (datetime.now() - chat_start_time).seconds > SESSION_TIMEOUT: 
                        console.print(" 💡 对话超时，开始新对话...")
                        await self.start_new_chat()
                        chat_start_time = datetime.now()

                    if not user_input:
                        error_console.print(" ⚠️ 你需要输入内容...")
                        continue
                    # /skill:<name> :<name> 动态命令跳过无效命令检查
                    is_skill_cmd = user_input.startswith("/skill:") and len(user_input) > 7
                    is_flow_cmd = user_input.startswith("/flow:") and len(user_input) > 6 and len(flow_cmd_items) > 0
                    if user_input.startswith("/") and not is_skill_cmd and not is_flow_cmd and user_input.split(" ")[0].lower() not in [c.split(" ")[0] for c in short_commands]:
                        error_console.print(" ⚠️ 无效命令...")
                        continue
                    if user_input.lower() in ["/smtd","/smpd"]:
                        error_console.print(" ⚠️ 命令不完整...")
                        continue
                    # ===== /flow:<name> 命令：运行 AgentFlow 工作流 =====
                    if user_input.startswith("/flow:"):
                        flow_match = re.match(r"^/flow:(\S+)\s*(.*)", user_input, re.DOTALL)
                        if not flow_match:
                            error_console.print(" ⚠️ 命令格式错误，正确格式：/flow:<工作流名> <用户需求>")
                            continue
                        flow_name = flow_match.group(1).strip()
                        user_request = flow_match.group(2).strip()
                        if not user_request:
                            error_console.print(" ⚠️ 请提供用户需求，格式：/flow:<工作流名> <用户需求>")
                            continue
                        # 查找 flow 文件
                        flows_meta = load_flows_metadata()
                        flow_info = None
                        for fm in flows_meta:
                            if fm["name"] == flow_name:
                                flow_info = fm
                                break
                        if not flow_info:
                            available = [fm['name'] for fm in flows_meta]
                            error_console.print(f" ⚠️ 工作流 '{flow_name}' 不存在。可用：{available}")
                            continue
                        console.print("")
                        self.appendInfo2Log("user", user_input)
                        await self.run_flow(flow_info["path"], flow_info["title"], user_request)
                        continue

                    # ===== /skill:<name> 命令：直接激活技能并干活 =====
                    if user_input.startswith("/skill:"):
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 需要先开启 Agent 模式...")
                            continue
                        # 解析命令：/skill:<skillname> <用户需求>
                        skill_match = re.match(r"^/skill:(\S+)\s*(.*)", user_input, re.DOTALL)
                        if not skill_match:
                            error_console.print(" ⚠️ 命令格式错误，正确格式：/skill:<技能名> <用户需求>")
                            continue
                        skill_name = skill_match.group(1).strip()
                        user_request = skill_match.group(2).strip()
                        if not user_request:
                            error_console.print(" ⚠️ 请提供用户需求，格式：/skill:<技能名> <用户需求>")
                            continue
                        skill = load_skill_full(skill_name)
                        if not skill:
                            available = [s['name'] for s in self.skills_meta]
                            error_console.print(f" ⚠️ 技能 '{skill_name}' 不存在。可用技能：{available}")
                            continue
                        if self.active_skill:
                            console.print(f" ℹ️ 技能「{self.active_skill.name}」已替换为「{skill.name}」")
                        self.active_skill = skill
                        console.print(f" ✅ 技能「{skill.name}」已激活，正在按指令处理...")
                        # 追加用户消息：技能指令 + 用户需求
                        self.messages.append({
                            "role": "user",
                            "content": f"""请按照以下技能指令处理我的需求：

## 当前激活的技能指令
{skill.instruction}

---
## 我的需求
{user_request}"""
                        })
                        self.appendInfo2Log("user", user_input)
                        console.print("")
                        # 设置标志位，进入 tool call 循环
                        self._skill_command_pending = True
                        # 跳转到 tool call 循环（通过不 continue，直接走到循环处理）
                        # 但此时 user_input 是命令，需要跳过下面的 elif user_input:
                        # 用标志位处理
                    
                    if user_input.lower() == "/help":
                        self.showItemIn3Cols("[Help]",[f"[bold blue]{s}[/bold blue]: {d}" for s,d in help_items],cols=1)
                        continue
                    if user_input.lower() == "/lst":
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 需要先开启Agent模式...")
                            continue
                        self.showSysInfo(base_tools_nameFormat,"[Base Tools]")
                        self.showSysInfo(tools_name,"[MCP Tools]")
                        # 展示技能列表
                        skills_nameFormat = ""
                        for idx,skill in enumerate(self.skills_meta if self.skills_meta else []):
                            skills_nameFormat += f"+ [bold blue]{skill['name']}[/bold blue] \n{skill['description']}" \
                                + ("\n" if idx < len(self.skills_meta)-1 else "")
                        self.showSysInfo(skills_nameFormat,"[Loaded Skills]")
                        continue
                    if user_input.lower().startswith("/smtd "):
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 需要先开启Agent模式...")
                            continue
                        prompt_name = user_input[6:].strip()
                        tool_name = user_input[6:].strip()
                        tools_details = self.get_tool_details(all_tools,tool_name)
                        self.showSysInfo(tools_details,"[Tools Details]")
                        continue
                    if user_input.lower().startswith("/smpd "):
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 需要先开启Agent模式...")
                            continue
                        prompt_name = user_input[6:].strip()
                        prompts_details = self.get_prompt_details(all_prompts,prompt_name)
                        self.showSysInfo(prompts_details,"[Prompts Details]")
                        continue
                    if user_input.lower() == '/snc':
                        await self.start_new_chat()
                        chat_start_time = datetime.now()
                        continue
                    if user_input.lower() in ('/agent','/agent-task'):
                        if user_input.lower() == '/agent' and self.agent_switch == 1:
                            server_console.print(" ℹ️ 已经是Agent模式了...")
                            continue
                        if user_input.lower() == '/agent-task' and self.agent_switch == 2:
                            server_console.print(" ℹ️ 已经是AgentTask模式了...")
                            continue
                        # 无论如何先清理当前任务列表
                        self.task_steps = []
                        if self.agent_switch == 0:
                            # 加载Skills
                            self.skills_meta=load_skills_metadata()
                            server_console.print(" ℹ️ Skills已经加载...")
                            # 动态生成 /skill:<name> 补全
                            skill_cmd_items.clear()
                            for s in self.skills_meta:
                                sc = f"/skill:{s['name']}"
                                skill_cmd_items.append(sc)
                                short_commands_map[sc] = HTML(f"<red>{sc}</red> <cyan>输入你的需求</cyan>")
                            short_commands.extend(skill_cmd_items)
                            # 启动配置的servers
                            await self.initialize_servers() # 启动MCP
                            await load_mcp_servers_info()
                            server_console.print(" ℹ️ MCP服务已经加载...")
                        if user_input.lower() == '/agent':
                            self.agent_switch = 1
                            server_console.print(" ℹ️ Agent模式已经开启...")
                        else:
                            self.agent_switch = 2
                            server_console.print(" ℹ️ AgentTask模式已经开启...")
                        self.messages[0]={"role": "system", "content": await self.gen_agent_system_content()}
                        chat_start_time = datetime.now()
                        continue
                    if user_input.lower() == '/agent-off':
                        # 清空Skills
                        self.active_skill = None
                        self.skills_meta = []
                        self.active_mcp = None
                        server_console.print(" ℹ️ Skills已经加载...")
                        # 关闭配置的servers
                        await self.cleanup_servers() # 关闭MCP
                        server_console.print(" ℹ️ MCP服务已经关闭...")
                        self.messages[0]={"role": "system", "content": self.gen_chat_system_content()}
                        chat_start_time = datetime.now()
                        self.agent_switch = 0
                        self.task_steps = []
                        server_console.print(" ℹ️ Agent模式已经关闭...")
                        # 清理技能补全
                        for sc in skill_cmd_items:
                            short_commands_map.pop(sc, None)
                            if sc in short_commands:
                                short_commands.remove(sc)
                        skill_cmd_items.clear()
                        continue
                    if user_input.lower() == "/clh":
                        input_session.history._loaded_strings = []
                        prmt_session.history._loaded_strings = []
                        server_console.print(" ℹ️ 已经清理输入历史...")
                        continue
                    if user_input.lower() == "/cls":
                        if os.name == 'posix':  # Unix/Linux/Mac
                            print("\033c", end="")
                        elif os.name in ('nt', 'dos'):  # Windows
                            os.system('cls')
                        self.showTitle()
                        continue
                    if user_input.lower() == "/swm":
                        models_options = [(no,HTML(f"<ansiblue>{model['ai_channel']}</ansiblue> | <ansiblue>{model['ai_model']}</ansiblue> | <ansiblue>{model['ai_provider']}</ansiblue>")) for no, model in self.client_models.items()]
                        input_selection = ChoiceInput(
                            message=HTML("<cyan><b>Choose the AI model</b></cyan> 🤔"),
                            options=models_options,
                            style=Style.from_dict({"frame.border": "#884444","selected-option": "fg:#884444 bold"}),
                            show_frame=True,
                            bottom_toolbar=HTML("Use [↑][↓]·[Enter] to accept.")
                        )
                        result = await input_selection.prompt_async()
                        self.switch_model(result)
                        server_console.print(f" ℹ️ AI大模型已经切换到 \"{escape(self.llm_client.ai_model)}\", "
                                             f"频道:\"{escape(self.llm_client.ai_channel)}\", "
                                             f"提供方:\"{escape(self.llm_client.ai_provider)}\"!")
                        await self.start_new_chat()     # 开始新对话
                        chat_start_time = datetime.now()
                        continue
                    if user_input.lower() == "/stu":
                        usageInfo = (f"Prompt Tokens: [yellow]{self.usage['prompt_tokens']}[/yellow]\n"
                                    f"Completion Tokens: [yellow]{self.usage['completion_tokens']}[/yellow]\n"
                                    f"Total Tokens: [yellow]{self.usage['total_tokens']}[/yellow]")
                        self.showSysInfo(usageInfo,"[This Chat Session Token Usage]")
                        continue    
                    if user_input.lower() == "/lsmp":
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 需要先开启Agent模式...")
                            continue
                        self.showSysInfo(prompts_name,"[Prompt List]")
                        continue
                    if user_input.lower() == "/swt":
                        self.showItemIn3Cols("Markdown Theme Options",[f"[cyan]{num}[/cyan]. [blue]{theme}[/blue]" for num,theme in enumerate(code_themes, start=1)],cols=4)
                        chose = Prompt.ask("[bold cyan]Choose 🤔[/bold cyan]", choices=[str(i) for i in range(1, len(code_themes)+1)])
                        chose_theme  = code_themes[int(chose)-1]
                        if chose_theme not in code_themes:
                            continue
                        self.markdown_theme = chose_theme
                        server_console.print(f" ℹ️ Markdown主题切换到了: {chose_theme.replace('[','[[').replace(']',']]')}")
                        continue
                    if user_input.lower() == "/reload":
                        try:
                            self.base_configs.clear()
                            self.base_configs = config.load_config("configs.json") # 重新读取Severs配置
                        except Exception as e:
                            error_console.print(f" ❌ 加载configs.json失败: {e}")
                            self.base_configs = {"llm_models": [], "mcp_servers": [], "web_search":{}}
                            continue
                        # 初始化llm_client列表
                        self.client_models.clear()
                        model_no = 1
                        for model in self.base_configs["llm_models"]:
                            if model.get("disabled"):
                                continue
                            self.client_models[str(model_no)]=model
                            model_no+=1
                        if self.agent_switch in (1,2):
                            new_servers = [
                                Server(name, srv_config)
                                for name, srv_config in self.base_configs["mcp_servers"].items() if not srv_config.get("disabled")
                            ]
                            await self.reinitialize_servers(new_servers)
                            await load_mcp_servers_info()
                            # 加载Skills
                            self.skills_meta=load_skills_metadata()
                            self.messages[0] = {"role": "system", "content": await self.gen_agent_system_content()}
                            server_console.print(" 🖥️ 重新加载了MCP、SKILLS及AI大模型配置, 重新切换大模型可生效 ...")
                        else:
                            server_console.print(" 🖥️ 重新加载了AI大模型配置, 重新切换大模型可生效 ...")
                        continue
                    if user_input.lower() == "/log":
                        log_path = os.path.abspath(self.log_file.name).replace("\\","/")
                        self.showSysInfo(Markdown(f"📄 [{log_path}]({log_path})"),"[Log File Path]")
                        # subprocess.run(["open", log_path])
                        pyperclip.copy(log_path)
                        server_console.print(" 📁 日志文件路径已复制到剪贴板!")
                        continue
                    if user_input.lower() == "/exit":
                        server_console.print(" 🖥️ 应用退出中...")
                        break
                    if user_input.lower() == "/compact":
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 压缩功能建议在 Agent 模式下使用...")
                            continue
                        if len(self.messages)<=3:
                            error_console.print(" ⚠️ 压缩功能建议对话上下文信息超过3...")
                            continue
                        with server_console.status(" 🧠 正在压缩会话..."):
                            filepath = await self._compact_session()
                        if filepath:
                            fname = os.path.basename(filepath)
                            server_console.print(f" ✅ 会话已压缩保存: [bold]{fname}[/bold]")
                        continue
                    if user_input.lower() in ("/load"):
                        if self.agent_switch == 0:
                            error_console.print(" ⚠️ 加载 session 建议在 Agent 模式下使用...")
                            continue
                        # 第一步：选择文件（普通交互，不能放在 status 内）
                        picked = await self._pick_session_file()
                        if not picked:
                            continue
                        filepath, filename = picked
                        # 第二步：读取并注入（快速操作，可包 status）
                        with server_console.status(f" 📂 加载 {filename} ..."):
                            result = await self._load_session_file(filepath, filename)
                        if result:
                            server_console.print(f" ✅ 已加载会话: [bold]{result}[/bold]")
                            server_console.print(" 💡 已加载的会话已作为上下文消息插入（system prompt 未修改，缓存不受影响）。")
                        else:
                            error_console.print(" ⚠️ 当前会话已经加载过了/加载会话报错..")
                        continue
                    if user_input.lower() == "/img":
                        if not self.llm_client.support_multimodal:
                            error_console.print(" ⚠️ 此大模型不支持图片...")
                            continue
                        self.img_path_list.clear()
                        while True:
                            img_path = Prompt.ask(" 🖼️ 输入图片路径(输入'/'终止)")
                            if img_path.strip() == "/":
                                break
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
                        continue      
                    # 使用server提供的prompts,此项必须在最后位置
                    if user_input.lower() == "/usmp":
                        if self.agent_switch==0:
                            error_console.print(" ⚠️ 需要先开启Agent模式...")
                            continue
                        server_console.print(" 键入【Tab】或内容联想，使用[↑][↓]·[Enter]确定)")
                        word_completer = WordCompleter([prompt.name for prompt in all_prompts], ignore_case=True,match_middle=True)
                        prm_input = (await prmt_session.prompt_async("> ", completer=word_completer,multiline=False,vi_mode=True)).strip()
                        console.print("") # 增加一个空行
                        if prm_input not in word_completer.words:
                            error_console.print(" ❌ 无效的Prompt名称...")
                            continue
                        prompt_messages = await self.process_use_prompt(prm_input)
                        if not prompt_messages:
                            error_console.print(f" ❌ 无法从MCP服务获取此Prompt - {prm_input}")
                            continue 

                        self.messages.append({"role": "user", "content": user_input})
                        show_prompts = []
                        for prompt_message in prompt_messages:
                            if prompt_message.role.lower() == "user" and isinstance(prompt_message.content, TextContent):
                                self.messages.append({"role": "user", "content": prompt_message.content.text})
                                # log
                                self.appendInfo2Log("user",prompt_message.content.text)
                                show_prompts.append(f"[bright_cyan]User[/bright_cyan]: {prompt_message.content.text}")
                            elif prompt_message.role.lower() == "assistant" and isinstance(prompt_message.content, TextContent):
                                self.messages.append({"role": "assistant", "content": prompt_message.content.text})
                                # log
                                self.appendInfo2Log("assistant",prompt_message.content.text)
                                show_prompts.append(f"[green]Assistant[/green]: {prompt_message.content.text}")
                        if not show_prompts:
                            error_console.print(f" ❌ 无法从Prompt({prm_input})获取文本信息")
                            continue 
                        console.print("") # 增加一个空行
                        self.showSysInfo("\n".join(show_prompts),"[Used Prompt]")
                        # user_input = "Show me!"
                    elif user_input.lower().startswith('/batch '):
                        if self.agent_switch != 2:
                            error_console.print(f" ❌ 仅AgentTask模式可以此命令")
                            continue
                        cmd_slice = [x.strip() for x in user_input.split(" ",maxsplit=1)]
                        if len(cmd_slice) < 2:
                            error_console.print(f" ❌ 命令不完整，请提供agent的任务描述文件路径")
                            continue
                        task_desc = self.read_file(cmd_slice[1])
                        if task_desc.startswith("[ERROR]"):
                            error_console.print(f" ❌ {task_desc}")
                            continue
                        await self.start_new_chat() # 开始新对话
                        self.agent_task_batch_switch = 1
                        self.messages.append({
                            "role": "user", 
                            "content": f"""我有批量任务需你严格按要求完成（任务描述在之后提供），请严格按如下进行：  
# 前置任务（此任务**不要使用**“Agent任务模式”）  
概述：在工作目录下需要有“以整体任务名称命名”的任务文件夹，里面需要存放“批量任务清单.txt”(一个任务一行)(必要文件)、各个“单个任务执行结果”(任务x结果.md、任务y结果.md...)、“共性任务执行总结.md”。
1. 先判断工作目录下相关任务文件夹及必要文件已经存在？
2-1. 不存在或缺失：
    1. 生成“批量任务清单”，将其写入任务文件夹中。 
    2. 仔细分析任务描述并拆分出各项任务，判断各项任务之间是“**输入参数**不同->**执行过程**一致->**输出格式**一致”的共性任务，还是其他类型。
        - 共性任务：提炼出“输出部分”、“执行步骤部分”、“输出部分”，“执行步骤部分”中拆分出具体的操作流程；将这些生成「任务规划」模版供对应共性任务使用。
        - 其他类型：按各自任务要求生成「任务规划」。
2-2. 已经存在：  
    读取“批量任务清单”、“共性任务执行总结”（如果存在），开始后续任务  
3. 以上结束后，调用工具`pre_work_done`（标记前置工作完成），继续下面的工作。  

# 批量任务依次执行（每个任务独立使用“Agent任务模式”，各自构建构建自己的「任务规则」）
按“批量任务清单”依次执行（注：已完成任务在对话历史中只保留结果），每个任务执行完成后，需要做如下处理：  
    - 将各个任务执行结果归档到整体任务文件夹中
    - 如果是共性任务，第1个任务执行完成后，总结过程中正确步骤及需要避免的项目到”共性任务执行总结.md”，后续任务执行请参考该文件
    - 必做项：更新任务文件夹中“批量任务清单”中对应任务的完成状态，如“任务3. 判断用户张三的信息。 -- 已完成”
        - 不知任务完成进度，请读取“批量任务清单”进行了解
    - 必做项：总结任务结果后，调用工具`current_task_done`(标记当前任务已完成)

--- 【任务描述-开始】 ---  
{task_desc}
--- 【任务描述-结束】 ---  
"""})
                    elif user_input and not self._skill_command_pending:
                        if self.llm_client.support_multimodal and len(self.img_path_list) > 0:
                            user_content = [{ "type": "text", "text": user_input}]
                            for img_path in self.img_path_list:
                                try:
                                    img_b64=image_to_base64(img_path,max_size_kb=500)
                                    user_content.append({"type": "image_url", "image_url": {"url":img_b64}})
                                except Exception as e:
                                    error_console.print(f" ⚠️ 无法将此图片转Base64 - {img_path} - {e}")
                            self.messages.append({"role": "user", "content": user_content})                         
                            self.img_path_list.clear()
                        else:
                            self.messages.append({"role": "user", "content": user_input})
                        # log
                        self.appendInfo2Log("user",user_input)
                    console.print("") # 增加一个空行

                    # -- 处理大模型响应 --
                    if self.agent_switch == 0:
                        if self.llm_client.support_stream:
                            orig_llm_response,_,_ =  await self.showAndGetAssistantResponseStream(self.llm_client,self.messages)
                        else:
                            orig_llm_response,_,_ =  await self.showAndGetAssistantResponse(lambda: self.llm_client.get_response(self.messages))
                        self.messages.append({"role": "assistant", "content": orig_llm_response})
                        # log
                        self.appendInfo2Log("assistant", orig_llm_response)
                        
                        # 处理多模态api，image_url有误情况
                        if orig_llm_response.lower().find('invalid_request_error') >=0 and orig_llm_response.lower().find('unsupported image url')>=0:
                            for message in reversed(self.messages):
                                if message['role'] == 'user' and isinstance(message.get("content"),list):
                                    if any([x.get("image_url") for x in message.get("content")]):
                                        message['content'] = "...请继续..."
                                        break
                        continue # 结束处理

                    # Agent模式,处理自动工具调用：u：问题 -> A: 找到工具1 -> T: 工具1答复 -> A：分析工具1答复，找到工具2 ->  T: 工具2答复 -> ...
                    # /skill:<name> 命令：重置标志位
                    if self._skill_command_pending:
                        self._skill_command_pending = False
                    max_loop_count = 50 if self.agent_task_batch_switch == 0 else 500
                    current_loop_count = 0
                    input_tc_obj = create_input()
                    support_tools = base_tools.copy()
                    base_tools_count = len(base_tools)
                    while True:
                        # 最大任务循环次数限制
                        current_loop_count += 1
                        if current_loop_count > max_loop_count:
                            error_console.print(f" ⚠️ Tool调用迭代次数超过上限：{max_loop_count} !")
                            break

                        # 用户按键检测结束任务
                        user_break_flag = False
                        for key in input_tc_obj.read_keys():
                            if key.data.upper() == '/' :
                                error_console.print(f" ⚠️ 用户中止了Tool调用迭代!")
                                user_break_flag = True 
                                break
                        if user_break_flag:
                            break

                        # 恢复原工具列表
                        del(support_tools[base_tools_count:])
                        # agent_task模式：每轮添加任务规划/更新工具
                        if self.agent_switch == 2:
                            support_tools.append(gen_task_step)
                            support_tools.append(update_task_step)
                            support_tools.append(get_task_process)
                            if self.agent_task_batch_switch == 1:
                                support_tools.append(pre_work_done)
                                support_tools.append(current_task_done)

                        # 加载skill内容
                        if self.active_skill or self.active_mcp: # 有激活的skill/MCP情况下，渐进式加载skill内容、MCP工具
                            if self.active_mcp:
                                mcp_tools = [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": tool.name,
                                            "description": f"[由MCP服务`{self.active_mcp.name}`提供]{tool.description}" ,
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


                        # 每轮重新初始化
                        if self.llm_client.support_stream:
                            orig_llm_response,tool_calls,reasoning_content =  await self.showAndGetAssistantResponseStream(self.llm_client,self.messages,use_tool_call=True,tools=support_tools)
                        else:
                            orig_llm_response,tool_calls,reasoning_content =  await self.showAndGetAssistantResponse(lambda: self.llm_client.get_response(self.messages,use_tool_call=True,tools=support_tools))
                        
                        # 处理多模态api，image_url有误情况
                        if orig_llm_response.lower().find('invalid_request_error') >=0 and orig_llm_response.lower().find('unsupported image url')>=0:
                            for message in reversed(self.messages):
                                if message['role'] == 'user' and isinstance(message.get("content"),list):
                                    if any([x.get("image_url") for x in message.get("content")]):
                                        message['content'] = "...请继续..."
                                        break

                        # 添加assistant消息
                        functions = []
                        if tool_calls: # 针对有支持function_call的模型
                            # 工具解析
                            fix_id_idx = 0
                            for tc in tool_calls:
                                try:
                                    if not tc.get("id") or tc["id"].strip()=="": # 补充ID
                                        tc["id"] = f"{tc["function"]["name"]}-{fix_id_idx}"
                                        fix_id_idx += 1
                                    tool_id = tc["id"]
                                    func_name = tc["function"]["name"]
                                    func_args = json.loads(tc["function"]["arguments"])
                                    functions.append({"id":tool_id, "name": func_name, "arguments": func_args})
                                except Exception as e:
                                    error_console.print(f" ⚠️ 工具解析异常: {str(e)}")
                                    continue
       
                        # 针对支持function_call/不支持function_call的模型，分别处理
                        if self.llm_client.support_tool_call and functions:
                            tool_execute_list = functions
                            reasoning_content = (reasoning_content or "正在调用工具...")
                            self.messages.append({"role": "assistant", "content": orig_llm_response, "tool_calls": tool_calls, "reasoning_content": reasoning_content })
                            # 写入日志 
                            self.appendInfo2Log("assistant",(f"**content**:{orig_llm_response}  \n**tool_calls**:{tool_calls}  \n**reasoning_content**:{reasoning_content}"))
                        else:
                            tool_execute_list = extract_tool_calls(orig_llm_response)
                            self.messages.append({"role": "assistant", "content": orig_llm_response})
                             # 写入日志 
                            self.appendInfo2Log("assistant",orig_llm_response)
                        # 无工具调用，结束处理
                        if not tool_execute_list: 
                            # 批量任务情况下，单个任务完成，清理过程
                            break

                        # 处理所有工具调用
                        tool_results = []
                        for tc in tool_execute_list:
                            func_id = tc.get("id") # 不支持function_call的模型，id为None
                            func_name = tc["name"]
                            func_args = tc["arguments"]
                            func_info=(
                                f"工具: [bold yellow]{func_name}[/bold yellow]\n"
                                f"参数: [bold light_sea_green]{func_args}[/bold light_sea_green]"
                            )
                            self.showSysInfo(func_info,"[Call Tool 🛠️]",is_tool_call=True)
                            handler = self.tool_handlers.get(func_name)
                            if handler:
                                if inspect.iscoroutinefunction(handler):
                                    try:
                                        # 如果是异步函数，使用await执行
                                        result = await handler(**func_args)
                                    except Exception as e:
                                        result = f"工具执行出错: {str(e)}"
                                else:
                                    try:
                                        # 同步函数直接调用
                                        result = handler(**func_args)
                                    except Exception as e:
                                        result = f"工具执行出错: {str(e)}"
                                self.showSysInfo(Markdown(str(result),code_theme=self.markdown_theme) if self.agent_switch==2 and func_name=="update_task_step" else str(result[:500])+" ... ","[Tool Result ℹ️]")
                            elif self.active_mcp and func_name in [tool.name for tool in await self.active_mcp.list_tools()]:
                                result = await self.process_mcp_response(self.active_mcp,{"name": func_name,"arguments": func_args} )
                            else:
                                result = f"未知工具: {func_name}"
                 
                            # 记录工具结果
                            tool_results.append({"tool_call_id": func_id,"tool_name":func_name,"content":result})

                        # 不是所有模型兼容如下格式
                        # 将工具结果作为一条 user 消息追加，供模型继续处理
                        if self.llm_client.support_tool_call and self.messages[-1]["role"]=="assistant" and self.messages[-1]["tool_calls"]:
                            # 填充tool—result信息
                            is_insert_images = False
                            for tool_rst in tool_results:
                                if not tool_rst["tool_call_id"]:
                                    continue
                                self.messages.append({"role": "tool","tool_call_id": tool_rst["tool_call_id"], "content": str(tool_rst['content'])})
                                # 写入日志
                                self.appendInfo2Log("tool",f"**tool_call_id**:{tool_rst['tool_call_id']}  \n**content**:{tool_rst['content']}")  
                                if tool_rst['tool_name']=='insert_images' and self.img_path_list:
                                    is_insert_images = True
                            if is_insert_images and self.llm_client.support_multimodal and len(self.img_path_list) > 0:
                                user_content = [{ "type": "text", "text": "请继续处理提供的图片"}]
                                for img_path in self.img_path_list:
                                    try:
                                        img_b64=image_to_base64(img_path,max_size_kb=1000) 
                                        user_content.append({"type": "image_url", "image_url": {"url":img_b64}})
                                    except Exception as e:
                                        error_console.print(f" ⚠️ 无法将此图片转Base64 - {img_path} - {e}")
                                self.messages.append({"role": "user", "content": user_content})                         
                                self.img_path_list.clear()
                        else:
                            combined_result = "  \n".join([f"工具`{tr['tool_name']}`(id:{tr['tool_call_id']})的执行结果为：\n{tr['content']}" for tr in tool_results])
                            user_content =f"<tool_results>\n{combined_result}\n</tool_results>"
                            self.messages.append({"role": "user", "content": user_content}) 
                            # 写入日志
                            self.appendInfo2Log("user",user_content)

                        # 批量任务：current_task_done 后清理中间消息，注入下一任务指引
                        if self._batch_cleanup_pending:
                            self._batch_cleanup_pending = False
                            del self.messages[self.agent_batch_pre_work_msg_len + 1:]
                            self.messages.append({
                                "role": "user",
                                "content": """之前的任务已完成(...为节省上下文，省略之前任务的过程...)
请根据"任务文件夹"中"批量任务清单"进度及"共性任务执行总结"，请进行下个任务；若无，则根据各个"单个任务执行结果"总结整个批量任务的结果。"""
                            })
                    input_tc_obj.close()

                except KeyboardInterrupt:
                    server_console.print(" 💻 应用退出...")
                    break
        finally:
            if self.log_file and not self.log_file.closed:
                self.log_file.close()
            await self.cleanup_servers()




async def main() -> None:
    chat_session = ChatSession()
    await chat_session.start()

if __name__ == "__main__":
    asyncio.run(main())
