# TinyClaw 配置文件说明文档


## 目录

1. [LLM 模型配置](#一-llm-模型配置)
2. [联网搜索配置](#二-联网搜索配置)
3. [MCP 服务配置](#三-mcp-服务配置)

---

## 一、LLM 模型配置

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `api_style` | string | API 请求风格，当前全部为 `OPENAI`（兼容 OpenAI 接口格式） |
| `ai_channel` | string | AI 渠道/厂商，如 `Deepseek`、`Moonshot`、`OpenAI` |
| `ai_model` | string | 模型名称，如 `deepseek-v4-flash`、`gpt-4.1` |
| `ai_api_url` | string | API 请求地址 |
| `ai_provider` | string | 服务提供商标识，如 `DOBA_LLM`、`KIMI`、`OpenAI` |
| `api_key` | string | 认证密钥 |
| `api_proxy` | string\|null | 代理地址，`null` 表示直连 |
| `support_stream` | bool | 是否支持流式输出 |
| `support_tool_call` | bool | 是否支持工具/函数调用 |
| `support_multimodal` | bool | 是否支持多模态（图片等）输入 |
| `support_thinking` | [bool, "on"\|"off"] | 是否支持思考链模式，以及默认开关状态 |
| `disabled` | bool | 是否停用，`true` 表示停用 |

### 配置示例

```json
{
    "api_style": "OPENAI",
    "ai_channel": "Deepseek",
    "ai_model": "deepseek-v4-flash",
    "ai_api_url": "http://10.110.4.79:80/v1/chat/completions",
    "ai_provider": "DOBA_LLM",
    "api_key": "doba_xxxxx",
    "api_proxy": null,
    "support_stream": true,
    "support_tool_call": true,
    "support_multimodal": false,
    "support_thinking": [true, "off"],
    "disabled": false
}
```

---

## 二、联网搜索配置

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `api_key` | string | 百度搜索 API 密钥 |
| `disabled` | bool | 是否停用 |

### 当前状态

- **搜索引擎**：百度搜索
- **状态**：✅ 已启用
- **用途**：当需要查询实时/最新信息时，自动调用联网搜索

### 配置示例

```json
{
    "api_key": "bce-v3/ALTAK-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "disabled": false
}
```

---

## 三、MCP 服务配置

### MCP 是什么？

MCP（Model Context Protocol）是一种让 LLM 能够调用外部工具/服务的协议。通过 MCP，AI 助手可以：
- 执行自动化操作（登录、注册、下单等）
- 查询业务数据（订单、用户、退款等）
- 操作 TSP 测试系统
- 读取文件、处理文档等

### 公共字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `transport` | string | 传输方式：`stdio`（本地进程）或 `streamable-http`（远程HTTP） |
| `disabled` | bool | 是否停用 |
| `when_to_use` | string | 描述此MCP服务的功能作用 |

### stdio 类型特有字段

| 字段 | 说明 |
|------|------|
| `command` | 启动命令（Python解释器路径） |
| `args` | 命令参数（服务脚本路径） |
| `env` | 环境变量 |

### streamable-http 类型特有字段

| 字段 | 说明 |
|------|------|
| `url` | 服务地址 |
| `headers` | 请求头（如认证信息） |
| `oauth` | bool, 针对使用oauth2一类的验证 |

### 格式例子
```json
{
    "tsp_mcp":
    {
            "url": "http://micceshi217.focuschina.com:5001/mcp",
            "transport": "streamable-http",
            "headers":
            {
                "Authorization": "Bearer xxxxxx"
            },
            "when_to_use": "获取TSP上项目、用例、目录信息，添加/修改目录，添加/修改用例名称及描述，添加/修改用例的测试点，修改更新流程图/思维导图用例数据",
            "disabled": false
    }
}
```

---

## 四、常见问题

### Q1: 如何启用一个停用的服务？
将对应配置项中的 `"disabled": true` 改为 `"disabled": false` 即可。

### Q2: 如何切换使用的模型？
当前会话默认使用激活的模型，具体由 LLM 调用逻辑决定，配置文件中顺序靠前的启用模型优先级较高。

### Q3: MCP 服务启动失败怎么办？
- 检查 `command` 中的 Python 路径是否正确
- 检查服务脚本路径是否存在
- 检查端口是否被占用（针对 HTTP 类型服务）
- 查看日志文件获取详细错误信息
