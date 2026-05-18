## 大体介绍
这个是小型的龙虾客户端，主要兼容openai的api的模型，部分代码为AI生成。
 - 在`configs.json`中配置了api_key和api_url，及mcp服务的地址。
 - mcp服务配置项中有特殊项`when_to_use`，因为本客户端针对MCP也做了“渐进式披露”处理，需要你维护好，便于大模型准确选择使用
 - 输入`/`可以列出各种命令
 - 在`skills/`文件夹下，可以添加各种技能
 - 在`workspace/`文件夹是各种工作目录，下面可以添加一个`memory.md`, 写入用户的工具调用习惯

## 安装
python >= 3.13  
`pip install -r requirements.txt`


## 类图

```mermaid
classDiagram
    class LLMClient {
        -str api_key
        -str ai_channel
        -str ai_model
        -str ai_api_url
        -str ai_provider
        -bool support_stream
        -bool support_tool_call
        -tuple support_thinking
        -bool support_multimodal
        -str http_proxy
        +get_response(messages, use_tool_call)
        +yield_response(messages, use_tool_call)
    }

    class Configuration {
        +load_env()
        +load_config(file_path)
    }

    class Tool {
        -str name
        -str server_name
        -str description
        -dict input_schema
        +format_for_llm()
    }

    class MCPPrompt {
        -str name
        -str server_name
        -str description
        -list arguments
        +get_prompt_dict()
        +format_for_rich
    }

    class Server {
        -str name
        -dict config
        -ClientSession session
        -AsyncExitStack exit_stack
        -set capabilities
        +initialize()
        +list_tools()
        +list_prompts()
        +execute_tool(tool_name, arguments)
        +get_prompt(prompt_name, arguments)
        +cleanup()
    }

    class ChatSession {
        -list servers
        -set invalid_servers
        -LLMClient llm_client
        -dict usage
        -str markdown_theme
        -list messages
        -int agent_switch
        -list skills_meta
        -Skill active_skill
        -dict tool_handlers
        +initialize_servers()
        +handle_skill_call(skill_name, user_request)
        +handle_mcp_call(server_name, user_request)
        +process_mcp_response(server, tool_call)
        +start()
        +cleanup_servers()
    }

    class Skill {
        -str name
        -str description
        -str instruction
        -str path
    }

    ChatSession --> LLMClient : uses
    ChatSession --> Server : manages
    ChatSession --> Skill : manages
    Server --> Tool : contains
    Server --> MCPPrompt : contains
    Configuration --> ChatSession : configures
```

## 时序图

### 1. 初始化流程

```mermaid
sequenceDiagram
    participant User
    participant ChatSession
    participant Configuration
    participant Server
    participant ClientSession

    User->>ChatSession: start()
    ChatSession->>Configuration: load_env()
    Configuration-->>ChatSession: env loaded
    ChatSession->>Configuration: load_config("configs.json")
    Configuration-->>ChatSession: config loaded
    ChatSession->>ChatSession: initialize_servers()
    loop For each server
        ChatSession->>Server: initialize()
        Server->>ClientSession: initialize()
        ClientSession-->>Server: capabilities
        Server-->>ChatSession: initialized
    end
    ChatSession->>ChatSession: load_mcp_servers_info()
    loop For each server
        ChatSession->>Server: list_tools()
        Server-->>ChatSession: tools list
        ChatSession->>Server: list_prompts()
        Server-->>ChatSession: prompts list
    end
    ChatSession-->>User: Ready for interaction
```

### 2. 工具调用流程

```mermaid
sequenceDiagram
    participant User
    participant ChatSession
    participant LLMClient
    participant ToolHandler
    participant Server
    participant ClientSession

    User->>ChatSession: 输入请求
    ChatSession->>LLMClient: get_response(messages)
    LLMClient-->>ChatSession: response + tool_calls
    loop For each tool call
        ChatSession->>ToolHandler: execute tool
        alt MCP Tool
            ToolHandler->>Server: execute_tool(name, args)
            Server->>ClientSession: call_tool(name, args)
            ClientSession-->>Server: result
            Server-->>ToolHandler: result
        else Base Tool
            ToolHandler-->>ToolHandler: execute locally
        end
        ToolHandler-->>ChatSession: tool result
    end
    ChatSession->>LLMClient: send tool results
    LLMClient-->>ChatSession: final response
    ChatSession-->>User: 显示最终响应
```

### 3. 技能调用流程

```mermaid
sequenceDiagram
    participant User
    participant ChatSession
    participant Skill
    participant LLMClient

    User->>ChatSession: 输入技能调用请求
    ChatSession->>ChatSession: handle_skill_call(name, request)
    ChatSession->>Skill: load_skill_full(name)
    Skill-->>ChatSession: skill details
    ChatSession->>ChatSession: set active_skill
    ChatSession->>LLMClient: get_response(messages with skill context)
    LLMClient-->>ChatSession: response
    ChatSession-->>User: 显示响应
```

### 4. MCP提示使用流程

```mermaid
sequenceDiagram
    participant User
    participant ChatSession
    participant Server
    participant LLMClient

    User->>ChatSession: 输入提示使用命令
    ChatSession->>ChatSession: process_use_prompt()
    loop For each server
        ChatSession->>Server: list_prompts()
        Server-->>ChatSession: prompts list
    end
    User->>ChatSession: 选择提示
    ChatSession->>Server: get_prompt(name, args)
    Server-->>ChatSession: prompt messages
    ChatSession->>ChatSession: add to messages
    ChatSession->>LLMClient: get_response(messages)
    LLMClient-->>ChatSession: response
    ChatSession-->>User: 显示响应
```

## 关键交互说明

1. **初始化阶段**：
   - ChatSession 从配置文件加载LLM模型和MCP服务器配置
   - 初始化所有可用的MCP服务器连接
   - 加载可用的技能列表

2. **工具调用阶段**：
   - LLMClient 识别需要调用工具
   - ChatSession 解析工具调用参数
   - 通过对应的 Server 执行工具
   - 将工具结果返回给LLM继续处理

3. **技能系统**：
   - 技能存储在 `skills/` 目录下
   - 每个技能包含 SKILL.md 文件描述
   - 激活技能后，LLM会遵循技能指令处理请求

4. **多模态支持**：
   - 支持上传图片进行对话
   - 图片转换为base64格式发送给支持多模态的LLM

