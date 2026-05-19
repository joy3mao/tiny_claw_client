# coding:utf-8
import asyncio,json,typing,os,re,shutil,math,uuid,inspect,traceback,httpx
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
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult,TextContent,PromptArgument,GetPromptResult,PromptMessage,TextContent
from typing import Dict, Any, Optional, List, Union, get_type_hints
from functools import partial
from dataclasses import dataclass
import subprocess,platform
import base64


def image_to_base64(image_path):
    """将图片文件转换为base64编码的字符串"""
    with open(image_path, 'rb') as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
    return f"data:image/png;base64,{encoded_string}"

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
                        "description": "用户请求"
                    }
                },
                "required": ["server_name", "user_request"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "execute_bash",
            "description": "执行bash/cmd命令",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "完整的bash/cmd命令",
                    }
                },
                "required": ["command"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "read_file",
            "description": "读取文本类型文件（限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `xml`源文件，其他类型文件由其他工具处理.）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    }
                },
                "required": ["path"]
            }
        }
    },{
        "type": "function",
        "function":{
            "name": "read_file_with_lineno",
            "description": "读取文本类型文件, 且读取的内容带有行号（限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `xml`源文件，其他类型文件由其他工具处理.）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
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
    }
]

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
WORKSPACE_DIR = "./workspace"
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

def load_skills_metadata() -> List[Dict[str, str]]:
    skills_meta = []
    for skill_name in os.listdir(SKILLS_DIR):
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
            error_console.print(f"⚠️ Failed to load skill {skill_name}: {e}")
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
        error_console.print(f"⚠️ Error loading full skill {skill_name}: {e}")
        return None
    
def extract_tool_calls(text: str) -> List[Dict]:
    """
    从模型输出中提取约定格式的工具调用。
    格式示例：
        <tool_call>skill_call {"skill_name": "pdf_processor", "user_request": "提取PDF文本"}</tool_call>
        <tool_call>execute_bash {"command": "tasklist | findstr python"}</tool_call>
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
            error_console.print(f"⚠️ 无法解析工具参数 JSON: {args_str}")
    return tool_calls

def extract_mcp_tools(text: str) -> List|None:
    """
    从模型输出中提取约定格式的工具调用。
    格式示例：
    { "tool": "skill_call", 
    "arguments": {
        "skill_name": "pdf_processor", 
        "user_request": "提取PDF文本"
    }
    }
    """
    pattern = r"{\s*\"tool\"\s*:\s*\".+?\",\s*\"arguments\"\s*:\s*(?:null|{.*?})\s*}"
    matches = re.findall(pattern, text, re.DOTALL|re.I|re.M)
    if not matches:
        return None
    mcp_tools = []
    for mt in matches:
        try:
            mcp_tools.append(json.loads(mt,strict=False))
        except json.JSONDecodeError as e:
            continue
    return mcp_tools
    

# ====================== 执行script必要方法 ======================
def build_script_command(script_path: str, args: List[str]) -> Optional[str]:
    """
    根据脚本类型和操作系统构建执行命令
    
    Returns:
        完整的命令行字符串
    """
    script_name = os.path.basename(script_path)
    ext = os.path.splitext(script_name)[1].lower()
    is_windows = platform.system() == "Windows"
    
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
                 support_thinking: tuple[bool,str] = [False,"off"],
                 support_multimodal : bool|None = False,
                 http_proxy: str = None) -> None:
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

    async def get_response(self, messages: list[dict[str, str]],use_tool_call=False) -> tuple[str,dict|None,list|None]:
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
            payload["tools"] = base_tools

        # 思考模式
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"]= {"type": "enabled"}
                payload["extra_body"]={"enable_thinking": True}
            else:
                payload["thinking"]= {"type": "disabled"}
                payload["extra_body"]={"enable_thinking": False}

        try:
            async with httpx.AsyncClient(proxy=self.http_proxy) as client:
                response = await client.post( self.ai_api_url, headers=headers, json=payload,timeout=120)
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
                return resp_message.get("content"),usage,tool_calls,reasoningContent
        except httpx.HTTPError as e:
            return (
                f"I encountered an error: Error getting LLM response. {str(e)}. "
                "Please try again or rephrase your request."
            ),None,None,None
        
    async def yield_response(self,messages: list[dict[str, str]],use_tool_call=False):
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
            payload["tools"] = base_tools

        # 思考模式
        if self.support_thinking[0]:
            if self.support_thinking[1] == "on":
                payload["thinking"]= {"type": "enabled"}
                payload["extra_body"]={"enable_thinking": True}
            else:
                payload["thinking"]= {"type": "disabled"}
                payload["extra_body"]={"enable_thinking": False}
        
        async with httpx.AsyncClient(proxy=self.http_proxy) as client:
            try:
                async with client.stream("POST", url=self.ai_api_url,
                                         headers=headers, json=payload, timeout=120) as response:
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
                                    elif tool["function"].get("arguments",""): # 不能trim
                                        yield 32,tool["function"]["arguments"] # 提取工具参数流式片段
                                    else:
                                        continue
                                if data['choices'][0].get("delta", {}).get("reasoning_content"):
                                    yield 4,data['choices'][0]["delta"]["reasoning_content"]
                            except Exception as e:
                                continue # 丢包了
                    except Exception as e:
                        yield 0,f" {str(e)}"
            except (httpx.RequestError,StopAsyncIteration) as e:
                error_message = f"{str(e)}"
                if isinstance(e, httpx.HTTPStatusError):
                    error_message = f" {e.response.status_code} | {e.response.text}"
                yield 0,error_message





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
        self, name: str, description: str, input_schema: dict[str, Any],server_name: str = None
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

        return f"""Tool: {self.name}
Description: {self.description.strip()}
Arguments:
{chr(10).join(args_desc)}
"""

class MCPPrompt:
    """Represents a prompt with its properties and formatting."""

    def __init__(
        self, name: str, description: str, arguments: list[PromptArgument],server_name: str = None
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
            )
            server_params = StdioServerParameters(
                command=command,
                args=self.config.get("args"),
                # env={**os.environ, **(self.config["env"] if self.config.get("env") else {})}, 
                # 安全考虑，不要将本地的全部环境变量传递给本地服务
                env = self.config.get("env") # 本地服务独立配置在config.json
            )
            cm = stdio_client(server_params)
            read, write = await self.exit_stack.enter_async_context(cm)
        elif transport == "sse":
            sseUrl = self.config.get("url","")
            if not sseUrl or sseUrl.endswith("/mcp"):
                raise ValueError("sse need url and the url should not end with /mcp")
            cm = sse_client(sseUrl)
            read, write = await self.exit_stack.enter_async_context(cm)
        elif transport == "streamable_http":
            streamableHttpUrl = self.config.get("url","") # 增加StreamableHttp连接支持
            if not streamableHttpUrl or not streamableHttpUrl.endswith("/mcp"):
                raise ValueError("streamable_http need url and the url is should end with /mcp")
            cm = streamablehttp_client(streamableHttpUrl)
            read, write, getSessionIdCallback = await self.exit_stack.enter_async_context(cm)
        else:
            raise ValueError("The command or transport: sse/streamable_http must be a valid string and cannot be None.")
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
            await self.cleanup()
            raise Exception("Initialize MCP Server Session Failed")
        
        
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
            error_console.print(f"❌ Error getting tools from server {self.name}.")
            await self.cleanup()
            return None

    async def cleanup(self) -> None:
        """Clean up server resources."""
        await self.exit_stack.aclose()
        try:
            async with self._cleanup_lock:
                self.session = None
                self.stdio_context = None
        except Exception as e:
            error_console.print(f"❌ Error during cleanup of server {self.name}: {e}")

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
            server_console.print(f"[ERROR] Can't getting prompts from server {escape(self.name)}: {escape(str(e))}")
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
        self.llm_client: LLMClient  | None  = None
        self.usage :dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.markdown_theme : str = code_themes[0]
        self.log_file = None
        self.messages = []
        self.agent_switch = 0 # agent开关，0：关闭，1：开启
        self.skills_meta = []
        self.active_skill: Optional[Skill] = None
        self.tool_handlers = {
            "skill_call": self.handle_skill_call,
            "mcp_call": self.handle_mcp_call,
            "execute_bash": lambda **kwargs: self.execute_bash(kwargs["command"]),
            "execute_script": lambda **kwargs: self.execute_script(kwargs["script_name"],kwargs.get("args", []),kwargs.get("timeout", 30)),
            "read_file": lambda **kwargs: self.read_file(kwargs["path"]),
            "read_file_with_lineno": lambda **kwargs: self.read_file_with_lineno(kwargs["path"]),
            "write_file": lambda **kwargs: self.write_file(kwargs["path"], kwargs["content"]),
            "append_file": lambda **kwargs: self.append_file(kwargs["path"], kwargs["content"]),
            "insert_file_at_line": lambda **kwargs: self.insert_file_at_line(kwargs["path"], kwargs["line_number"], kwargs["content"]),
            "edit_file": lambda **kwargs: self.edit_file(kwargs["path"], kwargs["old_text"], kwargs["new_text"], kwargs.get("replace_all", False)),
        }


        try:
            self.base_configs = config.load_config("configs.json") # 重新读取配置
        except Exception as e:
            error_console.print(f"❌ Failed to load config.json: {e}")
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
            error_console.print("❌ No LLM models available. Please add LLM models in configs.json.")

    # ====================== BASE TOOL ======================
    def execute_bash(self,command: str) -> str:
        """
        在 Windows CMD 中执行命令（经过安全过滤）

        Args:
            command: 要执行的命令字符串

        Returns:
            命令执行结果（stdout 或 stderr）
        """
        # Windows 危险命令黑名单（大小写不敏感）
        dangerous_patterns = [
            # 删除命令
            r"\bdel\b", r"\berase\b", r"\brmdir\b", r"\brd\b",
            # 格式化
            r"\bformat\b",
            # 磁盘操作
            r"\bdiskpart\b", r"\bchkdsk\b",
            # 权限提升
            r"\brunas\b", r"\bsudo\b", r"\belevate\b",
            # 网络下载执行
            r"\bcertutil\s+-urlcache\b", r"\bbitsadmin\b", r"\bpowershell\s+-enc\b",
            # 注册表修改
            r"\breg\s+add\b", r"\breg\s+delete\b",
            # 系统关机
            r"\bshutdown\b", r"\btaskkill\s+/f\b", r"\btskill\b"
        ]
        if any(re.search(pattern, command, re.IGNORECASE) for pattern in dangerous_patterns):
            return "[ERROR]禁止执行该命令"
        command_lower = command.lower().strip()

        if not command_lower:
            return "[ERROR]命令不能为空"

        is_powershell = command_lower.strip().startswith('powershell') or command_lower.strip().startswith('pwsh')
        is_nodejs = 'node' in command_lower.lower() and command_lower.lower().endswith('.js')
        if is_powershell:
            ps_dangerous = ["-enc", "-encodedcommand", "invoke-expression", "iex", "downloadfile", "downloadstring"]
            if any(d in command_lower for d in ps_dangerous):
                return "[ERROR]PowerShell 命令包含潜在危险操作，已被拒绝。" 
        try:
            # Windows 下使用 shell=True 会调用 cmd.exe
            if is_powershell or is_nodejs:
                command = f'powershell -Command "{command}"'
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=20,
                encoding='utf-8',
                errors='replace',
                cwd=WORKSPACE_DIR,
                env=os.environ.copy()  # 继承系统环境变量
            )

            # 处理输出编码问题（Windows CMD 默认 GBK）
            try:
                stdout = result.stdout.encode('gbk', errors='ignore').decode('gbk')
                stderr = result.stderr.encode('gbk', errors='ignore').decode('gbk')
            except:
                stdout = result.stdout
                stderr = result.stderr

            if result.returncode == 0:
                return stdout if stdout else "(命令执行成功，无输出)"
            else:
                return f"[ERROR]命令执行失败 (退出码 {result.returncode}):\n{stderr}"

        except subprocess.TimeoutExpired:
            return "[ERROR]命令执行超时（20秒）"
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
            file_path = os.path.join(WORKSPACE_DIR, path)
        else:
            file_path = path
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, mode="w", encoding="utf-8") as f:
                f.write(content)
            return f"文件保存成功，文件路径:{file_path}",

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
            source_file_path = os.path.join(WORKSPACE_DIR, path)
        else:
            source_file_path = path.removeprefix("file:///")
        
        # 检查源文件是否存在
        if not os.path.exists(source_file_path):
            return f"[ERROR]源文件不存在: {source_file_path}"
        
        try:
            # 将原文件复制到workspaces目录
            file_name = os.path.basename(source_file_path)
            target_file_path = os.path.join(WORKSPACE_DIR, file_name)
            
            # 如果目标文件已存在，添加时间戳避免覆盖
            if os.path.exists(target_file_path):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                name, ext = os.path.splitext(file_name)
                target_file_path = os.path.join(WORKSPACE_DIR, f"{name}_{timestamp}{ext}")
            
            # 复制文件到workspaces目录
            shutil.copy2(source_file_path, target_file_path)
            
            file_size = os.path.getsize(target_file_path)
            if file_size > 1024 * 1024 * 5:
                return f"[ERROR]文件过大，无法进行文本替换，文件大小: {file_size/1024/1024} MB"
            
            with open(target_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            if old_text not in content:
                return f"[WARNING]未找到需要替换的文本内容，文件已复制到: {target_file_path}"
            
            if replace_all:
                new_content = content.replace(old_text, new_text)
                count = content.count(old_text)
            else:
                new_content = content.replace(old_text, new_text, 1)
                count = 1
            
            with open(target_file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            return f"文件已复制到workspaces目录并成功替换了 {count} 处文本内容，文件路径: {target_file_path}"
        
        except Exception as e:
            return f"[ERROR]文件替换失败：{e}"


    def append_file(self,path: str, content: str) -> str:
        """追加信息到用户本地文件"""
        if not os.path.isabs(path):
            file_path = os.path.join(WORKSPACE_DIR, path)
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
            file_path = os.path.join(WORKSPACE_DIR, path)
        else:
            file_path = path
        try:
            file_size = os.path.getsize(file_size)
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


    def read_file(self,path: str) -> str:
        """读取文件"""
        if path.startswith("@skill/") and self.active_skill: # 针对skill目录下的文件情况
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            file_path = os.path.join(WORKSPACE_DIR, path)
        else:
            file_path = path.removeprefix("file:///")
        file_size = 0
        try:
            file_size = os.path.getsize(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                if file_size <= 1024 * 1024 * 2:
                    return f.read()
                if file_size <= 1024 * 1024 * 5:
                    lines = []
                    for line in f:
                        lines.append(line)
                    return "".join(lines)
                else:
                    return f"[ERROR]文件过大，无法读取，文件大小: {file_size/1024/1024} MB"
        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"
        
    def read_file_with_lineno(self,path: str) -> str:
        """读取文件并带有行号"""
        if path.startswith("@skill/") and self.active_skill: # 针对skill目录下的文件情况
            file_path = os.path.join(self.active_skill.path, path.removeprefix("@skill/"))
        elif not os.path.isabs(path):
            file_path = os.path.join(WORKSPACE_DIR, path)
        else:
            file_path = path.removeprefix("file:///")
        try:
            file_size = os.path.getsize(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                if file_size <= 1024 * 1024 * 2:
                    lines = f.readlines()
                    return "".join([f"LINE[{i+1}] {line}" for i, line in enumerate(lines)])
                if file_size <= 1024 * 1024 * 5:
                    line_no = 0
                    lines = []
                    for line in f:
                        lines.append(f"LINE[{line_no+1}] {line}")
                        line_no += 1
                    return "".join(lines)
                else:
                    return f"[ERROR]文件过大，无法读取，文件大小: {file_size/1024/1024} MB"
        except Exception as e:
            return f"[ERROR]读取文件失败: {e}"

    def execute_script(self,
        script_name: str,
        args: List[str] = None,
        timeout: int = 30
    ) -> str:
        """
        执行当前激活技能目录下 scripts/ 中的脚本

        Args:
            script_name: 脚本文件名（如 "process.py", "helper.bat"）
            args: 传递给脚本的参数列表
            timeout: 超时时间（秒）
            allowed_tools: 允许的工具列表（需包含 "script"）

        Returns:
            脚本执行结果
        """
        if not self.active_skill:
            return "[ERROR]当前没有激活的技能"
        if os.path.isabs(script_name):
            return f"[ERROR]处于安全考虑，不允许使用绝对路径下的脚本: {script_name}"
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


    def gen_agent_system_content(self,mcp_when_to_use:str):
        """ 生成Agent的系统信息 """
        user_memory = "[暂无]"
        if self.active_skill: # 激活技能情况下，读取技能目录下的memory.md文件
            user_memory = self.read_file("memory.md")
            if not user_memory or user_memory.strip().startswith("[ERROR]读取文件失败"):
                server_console.print("ℹ️没有在工作区memory.md中维护用户使用习惯")
                user_memory = "[暂无]"
        skills_list = "\n".join([f"- {s['name']}: {s['description']}" for s in self.skills_meta])
        base1 = f"""你是一个智能助手（性格：不装，说干就干）, 运行的系统为{platform.system()}(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})，可以通过输出特定格式的文本调用工具、技能或MCP服务。
## 核心工作
根据用户的问题、用户的技能工具使用习惯(如果有)，立刻选择合适的工具、技能或MCP服务进行调用；如果无需调用，或无匹配工具、技能或MCP服务情况，直接根据用户问题进行回答即可。


## 当前用户技能工具使用习惯
{user_memory}

## 可用技能
{skills_list}

## 可用MCP服务
{mcp_when_to_use}

## 可用工具
1. `skill_call` - 调用技能。参数：{{"skill_name": "...", "user_request": "..."}}
2. `mcp_call` - 调用MCP服务中的工具。参数：{{"server_name": "...","user_request": "..."}}
3. `execute_bash` - 执行bash/cmd命令(根据当前系统)。参数：{{"command": "..."}}
4. `read_file` - 读取文件、`read_file_with_lineno` - 读取文件(返回的内容带行号) 。参数：{{"path": "..."}}。
    - 文件限制：只能读取`.txt`, `.md`, `.json`, `.yaml/.yml`, `.csv/.tsv`, `.log`, `.sql`, `ini`, `toml`, `py`, `js`, `html`, `xml`源文件，其他类型文件由其他工具处理.
5. `append_file` - 追加方式写入文件。参数：{{"path": "...", "content": "..."}}
6. `write_file` - 一次性写入文件。参数：{{"path": "...", "content": "..."}}
7. `read_file_with_lineno` - 在文件指定行号前插入内容。参数：{{"path": "..."}}
8. `edit_file` - 修改文件的指定文本内容。参数：{{"path": "...", "old_text": ...,"new_text": ..., "replace_all": true/false}}
9. `execute_script` - 执行脚本。参数：{{"script_name": "...", "args": ["..."], "timeout": 30}}
{"10. `web_search` - 网络搜索。参数：{\"query\": \"...\"}\n" if not self.base_configs.get("web_search",{}).get("disabled",True) else ""}

## 调用格式
当你需要使用工具时，严格按此格式回复：`<tool_call>工具名称 参数字典JSON</tool_call>`, 即：以`tool_call`类似xml tag包裹工具名称和参数字典JSON。
格式举例例如：  
<tool_call>skill_call {{"skill_name": "xx—skill", "user_request": "提取sample.pdf文本"}}</tool_call>  
<tool_call>mcp_call {{"server_name": "xx_mcp", "user_request": "查询用户xxx信息"}}</tool_call>   
<tool_call>execute_bash {{"command": "where xx"}}</tool_call>  
<tool_call>read_file {{"path": "./xx.md"}}</tool_call>  
<tool_call>append_file {{"path": "./xx.md", "content": "追加内容"}}</tool_call>  
<tool_call>execute_script {{"script_name": "xx.py", "args": ["arg1", "arg2"], "timeout": 40}}</tool_call>  
{"<tool_call>web_search {\"query\": \"今天天气怎样？\"}</tool_call>\n" if not self.base_configs.get("web_search",{}).get("disabled",True) else ""} 

调用工具后，用户并将执行结果以"<tool_results>...</tool_results>"形式返回给你，你可以继续处理。  

## MCP服务激活
重要：匹配MCP服务后，**直接调用**即可，**无需向用户确认数据完整性**（由对应MCP服务自行处理）。
当你调用 mcp_call 后，该MCP服务会调用对应工具，并获得对应工具执行结果

## 技能激活
当你调用 skill_call 后，你会获得该技能的详细指令。之后处理该任务时，请遵循技能指令。  
如果激活的技能说明中有参考文件，请根据必要使用`read_file`读取文件内容作为参考:
    - 参考文件路径一般以`@skill/`开头,如`@skill/README.md`、`@skill/reference.md`等,
        - 如果文件路径为完整的路径，则直接使用完整路径
        - 如果文件路径为相对路径，则需要在前面补齐`@skill/`前缀，如`readme.md` -> `@skill/readme.md`, `doc/xx.txt` -> `@skill/doc/xx.txt`

"""     
        base2 = f"""你是一个智能助手（性格：不装，说干就干）, 运行的系统为{platform.system()}(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})，可以根据用户需求进行回答或调用工具/技能/MCP服务。
## 核心工作
根据用户的问题、用户的技能工具使用习惯(如果有)，选择合适的工具、技能或MCP服务进行调用；如果无需调用，或无匹配工具、技能或MCP服务情况，直接根据用户问题进行回答即可。
可使用的工具列表已通过`tools`参数传入，请根据用户问题选择合适的工具进行调用。

## 当前用户技能工具使用习惯
{user_memory}

## 可用技能
{skills_list}

## 可用MCP服务
{mcp_when_to_use}

调用工具后，用户会将执行结果以"<tool_results>...</tool_results>"形式返回给你，你可以继续处理。  

## MCP服务激活
重要：匹配MCP服务后，**直接调用**即可，**无需向用户确认数据完整性**（由对应MCP服务自行处理）。
当你调用 mcp_call 后，该MCP服务会调用对应工具，并获得对应工具执行结果

## 技能激活
当你调用 skill_call 后，你会获得该技能的详细指令。之后处理该任务时，请遵循技能指令。  
如果激活的技能说明中有参考文件，请根据必要使用`read_file`读取文件内容作为参考:
    - 参考文件路径一般以`@skill/`开头,如`@skill/README.md`、`@skill/reference.md`等,
        - 如果文件路径为完整的路径，则直接使用完整路径
        - 如果文件路径为相对路径，则需要在前面补齐`@skill/`前缀，如`readme.md` -> `@skill/readme.md`, `doc/xx.txt` -> `@skill/doc/xx.txt`

"""
        if not self.llm_client.support_tool_call:
            base = base1
        else:
            base = base2
        if self.active_skill:
            base += f"\n\n## 当前激活的技能：{self.active_skill.name}\n{self.active_skill.instruction}"
        base += "\n现在请开始处理用户的问题。" \
            if self.llm_client.support_tool_call \
            else "\n现在请开始处理用户的问题，若要调用工具，严格按格式回复`<tool_call>工具名称 参数字典JSON</tool_call>`，其中：tool_call是个标签关键字，**不能修改**！"
        return base

    def gen_mcp_system_content(self,tools_description,tool_call_history):
        """ 生成MCP Chat的系统信息 """
        user_memory = self.read_file("memory.md")
        if not user_memory or user_memory.strip().startswith("[ERROR]读取文件失败"):
            server_console.print("ℹ️ 没有在工作区memory.md中维护用户使用习惯")
            user_memory = "[暂无]"
        return f"""## 角色定义及能力
你是一个有用的智能助手(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})，能够访问以下工具:
{tools_description}
## 核心工作
请基于用户请求、历史对话及用户的技能工具使用习惯(如果有)，选择最合适的工具，并生成一个JSON对象来调用该工具；如果没有合适的工具, 直接返回"没有匹配的工具"。  
- 重要点: **只从提供的工具中**找匹配的工具，严格按照如下json格式回复，不添加注释/说明
{{
    "tool": "tool-name",
    "arguments": {{
        "argument-name": "value"
    }}
}}

## 当前用户技能工具使用习惯
{user_memory}

## 历史对话 
```
{tool_call_history}
```
"""

    def gen_chat_system_content(self):
        """ 生成一般 Chat的system信息 """
        return f"你是一个智能助手，根据用户的提问，直接、正确地回答对应问题(Now:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

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
                error_console.print(f"❌ Error for server {server.name}. {str(e)}")
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
                error_console.print(f"❌ Error for server {server.name}. {str(e)}")
                self.invalid_servers.add(server)

    def showSysInfo(self,msg:str|Markdown|Text,title:str,subtitle:str=None):
        """Show system info."""
        sys_info_pannel = Panel(
            msg,
            title=title,
            title_align="left",
            subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
            subtitle_align="right",
            padding=(1, 2)
        )
        console.print(sys_info_pannel)

    def assistantResponse(self,msg:str|Markdown|Text,subtitle:str=None):
        panel = Panel(
            msg,
            title="[Assistant 🤖]",
            title_align="left",
            border_style="green",
            subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
            subtitle_align="left",
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
        fillMarks_len = (console.width - (len(self.llm_client.ai_channel)+len(self.llm_client.ai_model)+len(self.llm_client.ai_provider)+36))//2 - 10
        modleInfo = Text(justify="center")
        modleInfo.append("─"*fillMarks_len+"    " if fillMarks_len>0 else "",style="yellow")
        modleInfo.append(self.llm_client.ai_channel,style="blue")
        modleInfo.append("  |  ")
        modleInfo.append(self.llm_client.ai_model,style="blue")
        modleInfo.append("  |  ")
        modleInfo.append(self.llm_client.ai_provider,style="blue")
        modleInfo.append("  |  ")
        modleInfo.append(f"{'AGENT ON' if self.agent_switch==1 else 'AGENT OFF'}",style="blue")
        modleInfo.append("    "+"─"*fillMarks_len if fillMarks_len>0 else "",style="yellow")
        return modleInfo

    async def showAndGetAssistantResponse(self,call_llm: callable,subtitle:str=None)->typing.Tuple[str,dict|None,str|None]:
        """Show Assistant response.

        Args:call_llm (callable): A function that returns a tuple of (assistant_response, usage).
        Returns: opitimazied_assistant_response (str|list|dict),  assistant_response (str)
        """
        tool_calls = None
        with Live(console=console,auto_refresh=False) as live:
            start_time = asyncio.get_running_loop().time()
            task = asyncio.create_task(call_llm())
            input_obj = create_input()
            waiting_spinners = ['∵','∴']
            spinner_idx=0
            try:
                while not task.done():
                    for key in input_obj.read_keys():
                        if key.data.upper() == '/':
                            if not task.done():
                                task.cancel()
                    cur_spinner=waiting_spinners[spinner_idx]
                    elapsed = asyncio.get_running_loop().time() - start_time
                    process_info = f"Waiting: {cur_spinner} Cost [bold red]{elapsed:.2f}[/bold red] Sec"
                    assistant_panel = self.assistantResponse(process_info,escape("Press [/] to Cancel"))
                    live.update(assistant_panel,refresh=True)
                    await asyncio.sleep(0.2)  # 降低 CPU 占用
                    spinner_idx = (0 if spinner_idx else 1)
                result,usage,tool_calls,reasoning_content = task.result()
            except (asyncio.CancelledError,Exception) as e:
                result,usage,tool_calls,reasoning_content = "[*用户暂停输出*]" if not(str(e)) else f"⚠️ Exception Occurred: {str(e)}",None,None,None
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
            assistant_panel = self.assistantResponse(Markdown(show_result,code_theme=self.markdown_theme),cost_info if not subtitle else f"{cost_info} | {subtitle}")
            live.update(assistant_panel,refresh=True)
        if tool_calls and not result.strip():
            func_names = [func.get('function',{}).get("name","API格式不匹配") for func in tool_calls]
            result = f"🔔 返回了一些工具: {",".join(func_names)} ..."
        if not tool_calls and not result.strip():
            result = "[Error] Received empty response from LLM"
        return result,tool_calls,reasoning_content

    async def showAndGetAssistantResponseStream(self,llmClient:LLMClient,messages:list[dict[str, str]],use_tool_call:bool=False)-> typing.Tuple[str,list|None]:
        """Show Assistant response. Stream version. only for no mcp mode
        Returns: assistant_response (str)
        """
        src_response = ""
        tool_calls = []
        tool_args_str = ""
        reasoningContent = ""
        start_time = asyncio.get_running_loop().time()
        with Live(console=console, refresh_per_second=2) as live:
            input_obj = create_input()
            try:
                async for code,chunk in llmClient.yield_response(messages,use_tool_call):
                    for key in input_obj.read_keys():
                        if key.data.upper() == '/':
                            src_response += "  \n[*用户暂停输出*]"
                            break
                    else:
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
                            reasoningContent+=chunk
                        else: # 将新内容追加到文本对象
                            src_response += chunk
                            assistant_panel = self.assistantResponse(Markdown(src_response.strip(), code_theme=self.markdown_theme),escape("Press [/] to Cancel"))
                            live.update(assistant_panel, refresh=True)
                        await asyncio.sleep(0.05)  # 降低 CPU 占用
                        continue
                    break
            except (asyncio.CancelledError,Exception) as e:
                src_response = f"⚠️ Exception Occurred：{traceback.format_exc()}"
                error_console.print(src_response)
            finally:
                input_obj.close()
                elapsed = asyncio.get_running_loop().time() - start_time
            cost_info = f"✅ Cost {elapsed:.2f} Sec"
            # print("大模型直接回答",src_response)
            if len(tool_calls)>=1:
                tool_calls[-1]["function"]["arguments"]=tool_args_str
                if not src_response.strip():
                    func_names = [func["function"].get("name","API格式不匹配") for func in tool_calls]
                    src_response = f"🔔 返回了一些工具: {",".join(func_names)} ..."
            if len(tool_calls) == 0 and not src_response.strip():
                src_response = "[Error] Received empty response from LLM"
            assistant_panel = self.assistantResponse(Markdown(src_response.strip(), code_theme=self.markdown_theme),cost_info)
            live.update(assistant_panel, refresh=True)
        
        return src_response,tool_calls,reasoningContent

    
    def toolCalledPanel(self,toolName:str,args: None | dict,process_info:str=None,out_put:str=None,subtitle:str=None):
        """Show Tool called."""
        process_msg=(
            f"Tool Calling: [bold yellow]{toolName}[/bold yellow]\n"
            f"Arguments: [bold light_sea_green]{args}[/bold light_sea_green]"
            f"{('\n'+process_info) if process_info else ''}"
        )
        result_msg = Markdown(
f"""{('\n'+out_put) if out_put else ''}""",
code_theme=self.markdown_theme
        )
        tool_panel = Panel(
                process_msg if not out_put else result_msg,
                title="[MCP Tool 🔧]",
                title_align="left",
                border_style="magenta",
                subtitle = f"[gray37]{subtitle}[/gray37]" if subtitle else None,
                subtitle_align="left",
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
            error_console.print(f"❌ Invalid model number: {model_no}")
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
        else:
            error_console.print(f"❌ Unsupport AI API STYLE: {model_info["api_style"]}")
      

    async def cleanup_servers(self) -> None:
        """Clean up all servers properly."""
        for server in reversed(self.servers):
            await server.cleanup()
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
                error_console.print(f"❌ Failed to list prompts from {server.name}. {str(e)}")
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
            return f"No server found with tool: {tool_call['tool']}"
        tools = await server.list_tools()
        if not tools:
            return f"No server found with tool: {tool_call['tool']}"
        if any(tool.name == tool_call["tool"] for tool in tools):
            try:
                with Live(auto_refresh=False) as live:
                    start_time = asyncio.get_running_loop().time()
                    task = asyncio.create_task(server.execute_tool(tool_call["tool"], tool_call.get("arguments")))
                    # 实时计算并显示耗时
                    while not task.done():
                        elapsed = asyncio.get_running_loop().time() - start_time
                        process_info = f"Running: 🕒 Cost [bold red]{elapsed:.2f}[/bold red] Sec"
                        ctPanel = self.toolCalledPanel(tool_call["tool"],tool_call.get("arguments"),process_info=process_info)
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
                        calledRst = f"{result.content.strip()}"
                    finish_info = f"{'❌' if result.isError else '✅'} Cost {elapsed:.2f} Sec"
                    ctPanel = self.toolCalledPanel(tool_call["tool"],tool_call.get("arguments"),out_put=out_put,subtitle=finish_info)
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
        margin_left_len = (server_console.width-33)//2-1
        server_console.print(
            " "*margin_left_len+"┏┳┓•      ┏┓┓       ┏┓┓•     "+"\n"+
            " "*margin_left_len+" ┃ ┓┏┓┓┏  ┃ ┃┏┓┓┏┏  ┃ ┃┓┏┓┏┓╋"+"\n"+
            " "*margin_left_len+" ┻ ┗┛┗┗┫  ┗┛┗┗┻┗┻┛  ┗┛┗┗┗ ┛┗┗(v2.0)"+"\n"+
            " "*margin_left_len+"       ┛                     "
        )

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
        log = f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[{role}]:  \n{info}  \n---\n"
        
        self.log_file.write(log)
        self.log_file.flush()

    def handle_skill_call(self, skill_name: str, user_request: str) -> str:
        skill = load_skill_full(skill_name)
        if not skill:
            return f"错误：技能 '{skill_name}' 不存在。"
        self.active_skill = skill
        return f"技能 {skill.name} 已激活，现在请按照技能指令处理用户的请求：{user_request}"

    async def handle_mcp_call(self,server_name:str, user_request:str) -> str:
        query_servers = list(filter(lambda x:x.name.lower().strip() == server_name.lower().strip() ,self.servers))
        if not query_servers:
            return f"⚠️ No such MCP server - {server_name} !"    
        server = query_servers[0] # MCPServer
        tools_desc = "---  \n".join([tool.format_for_llm() for tool in (await server.list_tools())])
        # 给MCP Tool插入上下文
        tool_call_history_List = []
        for msg in self.messages[-8:]: # 只取最后8条消息
            msg_content = msg["content"].strip()
            if msg["role"] == "user":
                if msg_content.startswith("<tool_results>") and msg_content.endswith("</tool_results>"):
                    tool_call_history_List.append(f"**TOOL**:\n  {msg_content}")
                else:
                    tool_call_history_List.append(f"**USER**:\n  {msg_content}")
            if msg["role"] == "assistant":
                tool_call_history_List.append(f"**ASSISTANT**:\n  {msg_content}")
            if msg["role"] == "tool":
                tool_call_history_List.append(f"**TOOL**:\n  {msg_content}")
        tool_call_history_str = "\n\n".join(tool_call_history_List)
        # 独立会话处理MCP工具调用
        toolcall_message = [
            {"role": "system", "content": self.gen_mcp_system_content(tools_desc,tool_call_history_str)},
            {"role": "user", "content": user_request}
        ]
        if self.llm_client.support_stream:
            orig_tool_llm_response,_,_ =  await self.showAndGetAssistantResponseStream(self.llm_client,toolcall_message)
        else:
            orig_tool_llm_response,_,_ =  await self.showAndGetAssistantResponse(lambda: self.llm_client.get_response(toolcall_message))
        # 此处orig_tool_llm_response要求满足json格式（调用工具）
        self.appendInfo2Log("tool-assistant",f"**Found Tool**:{orig_tool_llm_response}")
        mcp_tool_calls = extract_mcp_tools(orig_tool_llm_response)
        if mcp_tool_calls:
            mcp_tool_rsts = []
            for mcp_tool_call in mcp_tool_calls:
                mcp_tool_rst = await self.process_mcp_response(server,mcp_tool_call)
                mcp_tool_rsts.append(f"**Tool[{mcp_tool_call.get("tool")}] Result**:{mcp_tool_rst}  \n")
            return "  \n".join(mcp_tool_rsts)
        else:
            return f"⚠️ 以下你回复内容不符合工具调用格式，无法调用工具！：  \n`{orig_tool_llm_response}`"
    
    async def start(self) -> None:
        """Main chat session handler."""
        global base_tools
        prompt_style = Style.from_dict(
            {
                "frame.border": "#884444",
                "accepted frame.border": "gray",
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
                ("User Input","Type [Esc] to use vi command: [b]o[/b] newline, [b]g[/b]·[b]g[/b] home, [b]a[/b] or [b]i[/b] insert..."),
                ("Use Command","Type [b]/[/b] show commands, type [Tab]·[↑][↓] select and type [Enter]."),
                ("Use Prompts", "Type [Tab] or content show Completions, use [↑][↓] select and type [Enter] to choose prompts."),
                ("Log Path",f"start \"{os.path.abspath(self.log_file.name)}\"" if self.log_file else 'No log file'),
                ("Configs","\".env\" file supplies enveronment variables; \"configs.json\" file supplies llm models and mcp servers.")
            ]
            # 命令处理格式
            commands_item=[
                ("/agent","start agent mode"),
                ("/agent-off","close agent mode"),
                ("/lst","list tools"),
                ("/smtd *","show mcp tool details"),   
                ("/lsmp","list mcp prompts"),    
                ("/smpd *","show mcp prompt details"),
                ("/snc","start new chat"),
                ("/cls","clean screen"),
                ("/clh","clean input history"),
                ("/usmp","use mcp prompt"),
                ("/swt","switch theme"),
                ("/swm","switch model"),  
                ("/img","upload image for chat(only multimodal-support LLM)"),
                ("/reload","reload llm models and mcp servers"),
                ("/stu","show tokenUsage"),
                ("/help","show helps"),
                ("/exit","exit client")
            ]
            short_commands_map={x:HTML(f"<red>{x}</red>:{y}") for x,y in commands_item}
            short_commands = [k[0] for k in commands_item]
            # 开头的帮助提示信息
            
            self.showTitle()
            self.showSysInfo(self.getAIModelInfo(),"[Current AI Model]",subtitle="type /help 💡") # 展示当前模型信息
            
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
            mcp_when_to_use = ""
            async def load_mcp_servers_info():
                """
                Load the tools and prompts from all servers.
                """
                nonlocal mcp_when_to_use, tools_name, prompts_name # 需要修改
                all_tools.clear()
                all_tools_nameFormat.clear()
                all_prompts.clear()
                all_prompts_nameFormat.clear()
                mcp_when_to_use = ""
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
                    mcp_when_to_use = mcp_when_to_use+"\r\n"+f"""
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
            img_path_list = []

            while True:
                try:
                    console.print("") # 增加一个空行
                    cmd_completer = WordCompleter(short_commands, display_dict=short_commands_map,ignore_case=True,match_middle=False,sentence=True)
                    user_input = (await input_session.prompt_async(HTML("⌨︎ <cyan> > </cyan>"), completer=cmd_completer,
                                                                   multiline=False,vi_mode=True,
                                                                   bottom_toolbar="▪ Type [/] to show commands ▪ Type [Esc] to use vi mode ▪",
                                                                   show_frame=is_done,
                                                                   style=prompt_style)).strip()

                    if not user_input:
                        error_console.print("⚠️ You Need Input Something...")
                        continue
                    if user_input.startswith("/") and user_input.split(" ")[0].lower() not in [c.split(" ")[0] for c in short_commands]:
                        error_console.print("⚠️ Invalid Command")
                        continue
                    if user_input.lower() in ["/smtd","/smpd"]:
                        error_console.print("⚠️ Your Command Is Not Complete...")
                        continue
                    if user_input.lower() == "/help":
                        self.showItemIn3Cols("[Help]",[f"[bold blue]{s}[/bold blue]: {d}" for s,d in help_items],cols=1)
                        continue
                    if user_input.lower() == "/lst":
                        if self.agent_switch == 0:
                            error_console.print("⚠️ Please Turn On Agent First...")
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
                            error_console.print("⚠️ Please Turn On Agent First...")
                            continue
                        prompt_name = user_input[6:].strip()
                        tool_name = user_input[6:].strip()
                        tools_details = self.get_tool_details(all_tools,tool_name)
                        self.showSysInfo(tools_details,"[Tools Details]")
                        continue
                    if user_input.lower().startswith("/smpd "):
                        if self.agent_switch == 0:
                            error_console.print("⚠️ Please Turn On Agent First...")
                            continue
                        prompt_name = user_input[6:].strip()
                        prompts_details = self.get_prompt_details(all_prompts,prompt_name)
                        self.showSysInfo(prompts_details,"[Prompts Details]")
                        continue
                    if user_input.lower() == '/snc':
                        # 清理会话
                        del self.messages[:]
                        img_path_list.clear()
                        self.active_skill = None
                        chat_start_time = datetime.now()
                        self.usage ={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                        if self.agent_switch == 1:
                            self.messages.append({"role": "system", "content": self.gen_agent_system_content(mcp_when_to_use)})
                        else:
                            self.messages.append({"role": "system", "content": self.gen_chat_system_content()})
                        server_console.print("ℹ️ Started New Chat ...")
                        # log
                        self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")
                        continue
                    if user_input.lower() == '/agent':
                        if self.agent_switch == 1:
                            server_console.print("ℹ️ Agent Switch Is Already ON...")
                            continue
                        # 加载Skills
                        self.skills_meta=load_skills_metadata()
                        server_console.print("ℹ️ Skills Are Loaded ...")
                        # 启动配置的servers
                        await self.initialize_servers() # 启动MCP
                        await load_mcp_servers_info()
                        server_console.print("ℹ️ MCP Servers Are Loaded ...")
                        self.messages[0]={"role": "system", "content": self.gen_agent_system_content(mcp_when_to_use)}
                        chat_start_time = datetime.now()
                        self.agent_switch = 1
                        server_console.print("ℹ️ Agent Switch Is ON ...")
                        continue
                    if user_input.lower() == '/agent-off':
                        # 清空Skills
                        self.active_skill = None
                        self.skills_meta = []
                        server_console.print("ℹ️ Skills Are Cleared ...")
                        # 关闭配置的servers
                        await self.cleanup_servers() # 关闭MCP
                        server_console.print("ℹ️ MCP Servers Are Closed ...")
                        self.messages[0]={"role": "system", "content": self.gen_chat_system_content()}
                        chat_start_time = datetime.now()
                        self.agent_switch = 0
                        server_console.print("ℹ️ Agent Switch Is OFF ...")
                        continue
                    if user_input.lower() == "/clh":
                        input_session.history._loaded_strings = []
                        prmt_session.history._loaded_strings = []
                        server_console.print("ℹ️ Cleaned Input History ...")
                        continue
                    if user_input.lower() == "/cls":
                        if os.name == 'posix':  # Unix/Linux/Mac
                            print("\033c", end="")
                        elif os.name in ('nt', 'dos'):  # Windows
                            os.system('cls')
                        self.showTitle()
                        self.showSysInfo(self.getAIModelInfo(),"[Current AI Model]",subtitle="type /help 💡") # 展示当前模型信息
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
                        server_console.print(f"ℹ️ AI model is swithed to \"{escape(self.llm_client.ai_model)}\", "
                                             f"Channel:\"{escape(self.llm_client.ai_channel)}\", "
                                             f"Provider:\"{escape(self.llm_client.ai_provider)}\"!")
                        self.usage ={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                        del self.messages[:] # 清空对话历史
                        img_path_list.clear()
                        # log
                        self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")

                        if self.agent_switch == 1:
                            self.messages.append({"role": "system", "content": self.gen_agent_system_content(mcp_when_to_use)})
                        else:
                            self.messages.append({"role": "system", "content": self.gen_chat_system_content()})
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
                            error_console.print("⚠️ Please Turn On Agent First...")
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
                        server_console.print(f"ℹ️ Switched to markdown theme: {chose_theme.replace('[','[[').replace(']',']]')}")
                        continue
                    if user_input.lower() == "/reload":
                        try:
                            self.base_configs.clear()
                            self.base_configs = config.load_config("configs.json") # 重新读取Severs配置
                        except Exception as e:
                            error_console.print(f"❌ Failed to load configs.json: {e}")
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
                        if self.agent_switch==1:
                            new_servers = [
                                Server(name, srv_config)
                                for name, srv_config in self.base_configs["mcp_servers"].items() if not srv_config.get("disabled")
                            ]
                            await self.reinitialize_servers(new_servers)
                            await load_mcp_servers_info()
                            self.messages[0] = {"role": "system", "content": self.gen_agent_system_content(mcp_when_to_use)}
                            server_console.print("🖥️ Reloaded LLM Models and Servers, re-switch model to effect ...")
                        else:
                            server_console.print("🖥️ Reloaded LLM Models, re-switch model to effect ...")
                        continue
                    if user_input.lower() == "/exit":
                        server_console.print("🖥️ Exiting...")
                        break
                    if user_input.lower() == "/img":
                        if self.agent_switch==0:
                            error_console.print("⚠️ Please Turn On Agent First...")
                            continue
                        if not self.llm_client.support_multimodal:
                            error_console.print("⚠️ This Model Not Support Image...")
                            continue
                        img_path_list = []
                        while True:
                            img_path = Prompt.ask("🖼️ Enter Image Paths(Input '/' to end)")
                            if img_path.strip() == "/":
                                break
                            img_path = img_path.strip().strip("&").strip().strip('"').strip("'")
                            if not os.path.isfile(img_path) \
                                or not img_path.lower().endswith(('.png', '.jpg', '.jpeg')):
                                error_console.print("❌ File not found or not support(only png/jpg/jpeg)...")
                                continue
                            img_path_list.append(img_path)
                        continue      
                    # 使用server提供的prompts,此项必须在最后位置
                    if user_input.lower() == "/usmp":
                        if self.agent_switch==0:
                            error_console.print("⚠️ Please Turn On Agent First...")
                            continue
                        server_console.print("[bold]Press [Tab] or content to show Completions[/bold](Use [↑][↓]·[Enter] Submit)")
                        word_completer = WordCompleter([prompt.name for prompt in all_prompts], ignore_case=True,match_middle=True)
                        prm_input = (await prmt_session.prompt_async("> ", completer=word_completer,multiline=False,vi_mode=True)).strip()
                        console.print("") # 增加一个空行
                        if prm_input not in word_completer.words:
                            error_console.print("❌ Invalid Prompt Name...")
                            continue
                        prompt_messages = await self.process_use_prompt(prm_input)
                        if not prompt_messages:
                            error_console.print(f"❌ Can't get Prompt - {prm_input} from MCP Servers...")
                            continue
                        # 超过1小时，重新开始
                        if (datetime.now() - chat_start_time).seconds > 60*60: 
                            console.print("💡Chat timeout, start new chat.")
                            del self.messages[1:]
                            img_path_list.clear()
                            # log
                            self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")
                            chat_start_time = datetime.now()
                            self.usage ={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}     
                        self.messages.append({"role": "user", "content": user_input})
                        show_prompts = []
                        for prompt_message in prompt_messages:
                            if prompt_message.role.lower() == "system" and isinstance(prompt_message.content, TextContent):
                                self.messages.append({"role": "system", "content": prompt_message.content.text})
                                # log
                                self.appendInfo2Log("system",prompt_message.content.text)
                                show_prompts.append(f"[blue]System[/blue]: {prompt_message.content.text}")            
                            elif prompt_message.role.lower() == "user" and isinstance(prompt_message.content, TextContent):
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
                            error_console.print(f"❌ No text message from Prompt - {prm_input} from MCP Servers...")
                            continue 
                        console.print("") # 增加一个空行
                        self.showSysInfo("\n".join(show_prompts),"[Used Prompt]")
                        # user_input = "Show me!"
                    elif user_input:
                        # 超过1小时，重新开始
                        if (datetime.now() - chat_start_time).seconds > 60*60:
                            server_console.print("💡Chat timeout, start new chat.")
                            del self.messages[1:]
                            img_path_list.clear()
                            # log
                            self.log_file.write("\n\n\n-----------------Started New Chat------------------\n\n\n")
                            chat_start_time = datetime.now()
                            self.usage ={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                        if self.agent_switch == 1 and self.llm_client.support_multimodal and len(img_path_list) > 0:
                            user_content = [{ "type": "text", "text": user_input}]
                            for img_path in img_path_list:
                                try:
                                    img_b64=image_to_base64(img_path)
                                    user_content.append({"type": "image_url", "image_url": {"url":img_b64}})
                                except Exception as e:
                                    error_console.print(f"⚠️ Can't convert image to base64 - {img_path} - {e}")
                                    continue
                            self.messages.append({"role": "user", "content": user_content})                         
                            img_path_list = []
                        else:
                            self.messages.append({"role": "user", "content": user_input})
                        # log
                        self.appendInfo2Log("user",user_input)
                    console.print("") # 增加一个空行

                    # -- 处理大模型响应 --
                    # 非Agent模式
                    if self.agent_switch == 0: 
                        if self.llm_client.support_stream:
                            orig_llm_response,_,_ =  await self.showAndGetAssistantResponseStream(self.llm_client,self.messages)
                        else:
                            orig_llm_response,_,_ =  await self.showAndGetAssistantResponse(lambda: self.llm_client.get_response(self.messages))
                        self.messages.append({"role": "assistant", "content": orig_llm_response})
                        # log
                        self.appendInfo2Log("assistant", orig_llm_response)
                        continue # 结束处理

                    # Agent模式,处理自动工具调用：u：问题 -> A: 找到工具1 -> T: 工具1答复 -> A：分析工具1答复，找到工具2 ->  T: 工具2答复 -> ...
                    max_loop_count = 50
                    current_loop_count = 0
                    input_tc_obj = create_input()
                    while True:
                        # 最大任务循环次数限制
                        current_loop_count += 1
                        if current_loop_count > max_loop_count:
                            error_console.print(f"⚠️ Tool Call Loop count exceed {max_loop_count} !")
                            break
                        # 用户按键检测结束任务
                        for key in input_tc_obj.read_keys():
                            if key.data.upper() == '/':
                                error_console.print(f"⚠️ Tool Call Loop break by user!")
                                break
                        if self.active_skill: # 有激活的skill情况下，渐进式加载skill内容
                            self.messages[0] = {"role": "system", "content": self.gen_agent_system_content(mcp_when_to_use)}
                        
                        # 每轮重新初始化
                        if self.llm_client.support_stream:
                            orig_llm_response,tool_calls,reasoning_content =  await self.showAndGetAssistantResponseStream(self.llm_client,self.messages,use_tool_call=True)
                        else:
                            orig_llm_response,tool_calls,reasoning_content =  await self.showAndGetAssistantResponse(lambda: self.llm_client.get_response(self.messages,use_tool_call=True))


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
                                    error_console.print(f"⚠️ 工具解析异常: {str(e)}")
                                    continue
                        # 添加assistant的基本信息
       
                        

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
                            break

                        # 处理所有工具调用
                        tool_results = []
                        for tc in tool_execute_list:
                            func_id = tc.get("id") # 不支持function_call的模型，id为None
                            func_name = tc["name"]
                            func_args = tc["arguments"]
                            console.print("") # 增加一个空行
                            server_console.print(f"[调用工具🛠️] {escape(func_name)} ，参数: {escape(str(func_args))}")
                            console.print("") # 增加一个空行
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
                            else:
                                result = f"未知工具: {func_name}"
                            if func_name != "mcp_call":
                                self.showSysInfo(str(result[:500])+" ... ","[Tool Result]")
                            tool_results.append({"tool_call_id": func_id,"tool_name":func_name,"content":result})

                        # 不是所有模型兼容如下格式
                        # 将工具结果作为一条 user 消息追加，供模型继续处理
                        if self.llm_client.support_tool_call and self.messages[-1]["role"]=="assistant" and self.messages[-1]["tool_calls"]:
                            # 填充tool—result信息
                            for tool_rst in tool_results:
                                if not tool_rst["tool_call_id"]:
                                    continue
                                self.messages.append({"role": "tool","tool_call_id": tool_rst["tool_call_id"], "content": tool_rst["content"]})
                                # 写入日志
                                self.appendInfo2Log("tool",f"**tool_call_id**:{tool_rst['tool_call_id']}; **content**:{tool_rst['content']}")  
                        else:

                            combined_result = "  \n".join([f"工具**{tr['tool_name']}**(id:{tr['tool_call_id']})的执行结果为：\n{tr['content']}" for tr in tool_results])
                            user_content =f"<tool_results>\n{combined_result}\n</tool_results>"
                            self.messages.append({"role": "user", "content": user_content}) 
                            # 写入日志
                            self.appendInfo2Log("user",user_content)    
        
                except KeyboardInterrupt:
                    server_console.print("💻 Exiting...")
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
