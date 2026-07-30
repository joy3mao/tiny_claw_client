# coding:utf-8
"""Tiny Claw Core — Agent Execution Engine

Modules:
    agent:      独立 Agent 类，每个 Agent 拥有独立工作目录/技能/MCP/工具
    agent_flow: 多 Agent 工作流编排引擎（AgentFlow）
    core:       原始 ChatSession（兼容旧版，内部可引用 Agent）
    lark_bot:   飞书 Bot 集成
"""

from .agent import (
    Agent,
    LLMClient,
    Server,
    Tool,
    Skill,
    SessionEvent,
    EventType,
    BASE_TOOLS,
    image_to_base64,
    load_skills_metadata,
    load_skill_full,
)

from .agent_flow import (
    AgentFlowRunner,
    AgentFlowDefinition,
    FlowAgentConfig,
    FlowStep,
    run_flow,
    load_flow,
    validate_flow,
)

from .core import (
    ChatSession,
    config,
    WORKSPACE_DIR,
    SKILLS_DIR,
    _PROJECT_DIR,
)

__all__ = [
    # Agent
    "Agent",
    "LLMClient",
    "Server",
    "Tool",
    "Skill",
    "SessionEvent",
    "EventType",
    "BASE_TOOLS",
    "image_to_base64",
    "load_skills_metadata",
    "load_skill_full",
    # AgentFlow
    "AgentFlowRunner",
    "AgentFlowDefinition",
    "FlowAgentConfig",
    "FlowStep",
    "run_flow",
    "load_flow",
    "validate_flow",
    # Core (legacy)
    "ChatSession",
    "config",
    "WORKSPACE_DIR",
    "SKILLS_DIR",
    "_PROJECT_DIR",
]
