# coding:utf-8
"""Lark (飞书) Bot 模块 — 通过飞书卡片消息调用TinyClaw核心消息处理能力。

工作流程：
1. 通过 lark_oapi WebSocket 客户端接收飞书消息
2. 每个飞书会话（chat_id+sender）维护独立的 ChatSession 实例
3. 使用卡片模板回复消息，支持流式更新卡片内容
4. 会话超过30分钟无活动自动清理
"""

import json
import os
import uuid
import hashlib
import asyncio
import time
import traceback
import re
import threading
from io import BytesIO
from datetime import datetime
from typing import Dict, Optional

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.api.cardkit.v1 import *

from tiny_claw_core.core import (
    ChatSession, config, WORKSPACE_DIR,
    load_skills_metadata, base_tools
)

base_card_dict={
    "schema": "2.0",
    "config":
    {
        "update_multi": True,
        "streaming_mode": True,
        "streaming_config":
        {
            "print_step":
            {
                "default": 10
            },
            "print_frequency_ms":
            {
                "default": 30
            },
            "print_strategy": "fast"
        },
        "style":
        {
            "text_size":
            {
                "normal_v2":
                {
                    "default": "normal",
                    "pc": "normal",
                    "mobile": "heading"
                }
            }
        }
    },
    "body":
    {
        "direction": "vertical",
        "horizontal_spacing": "8px",
        "vertical_spacing": "5px",
        "horizontal_align": "left",
        "vertical_align": "center",
        "padding": "12px 12px 12px 12px",
        "elements": [
        {
            "tag": "markdown",
            "text_align": "left",
            "text_size": "normal_v2",
            "margin": "0px 0px 0px 0px",
            "element_id": "ai_msg_content",
            "content":"思考中..."
        }
        ]
    }
}


class LarkBot:
    """飞书机器人 — 与 TinyClaw Core 深度集成。

    使用方式:
        bot = LarkBot()
        bot.start()          # 阻塞，启动 WebSocket 长连接
        # 或在后台运行:
        asyncio.create_task(bot.start_async())
    """

    def __init__(self):
        self._config: Dict = {}
        self._sessions: Dict[str, dict] = {}  # session_key -> {"session": ChatSession, "expire": timestamp}
        self._lark_client: Optional[lark.Client] = None
        self._event_ids: list = []  # 去重事件ID列表（最多100个）
        self._ws_client: Optional = None
        self._running: bool = False
        self._ws_thread: Optional[threading.Thread] = None  # WS 阻塞线程
        self._base_configs: Dict = {}

    # ====================== Configuration ======================

    def load_config(self) -> bool:
        """从 configs.json 加载飞书配置。返回是否已启用。"""
        try:
            self._base_configs = config.load_config("configs.json")
            bot_cfg = self._base_configs.get("lark_bot", {})
            if bot_cfg.get("disabled", True):
                return False
            if not bot_cfg.get("app_id") or not bot_cfg.get("app_secret"):
                print("[LarkBot] app_id 或 app_secret 未配置")
                return False
            self._config = bot_cfg
            return True
        except Exception as e:
            print(f"[LarkBot] 加载配置失败: {e}")
            return False

    # ====================== Session Management ======================

    async def _get_or_create_session(self, chat_id: str, sender_open_id: str) -> ChatSession:
        """根据 chat_id + sender 获取或创建 ChatSession。"""
        session_key = hashlib.md5(
            (chat_id + sender_open_id).encode('utf-8')
        ).hexdigest()

        now = datetime.now().timestamp()
        existing = self._sessions.get(session_key)
        if existing and (now - existing["expire"] < 1800):  # 30分钟
            existing["expire"] = now
            return existing["session"]

        # 创建新会话
        cs = ChatSession()
        cs.creat_new_log()
        cs.base_configs = self._base_configs
        cs.client_models.clear()
        model_no = 1
        for model in cs.base_configs.get("llm_models", []):
            if model.get("disabled"):
                continue
            cs.client_models[str(model_no)] = model
            model_no += 1
        if cs.client_models:
            cs.switch_model("1")

        # 飞书场景下 ask_user 显示选项到卡片，等待用户回复选择
        if "ask_user" in cs.tool_handlers:
            cs.tool_handlers["ask_user"] = lambda choices, cs=cs: self._lark_auto_choose(cs, choices)

        # 启动时就初始化 Agent 模式
        cs.skills_meta = load_skills_metadata()
        try:
            await cs.initialize_servers()
        except BaseException:
            pass
        await cs.load_mcp_servers_info()
        cs.messages = [{"role": "system",
                        "content": await cs.gen_agent_system_content()}]
        cs.agent_switch = 1

        self._sessions[session_key] = {
            "session": cs,
            "expire": now,
            "card_id": None,
        }
        return cs

    def _cleanup_expired_sessions(self):
        """清理过期会话。"""
        now = datetime.now().timestamp()
        expired = [k for k, v in self._sessions.items() if now - v["expire"] > 1800]
        for k in expired:
            self._sessions.pop(k, None)

    async def _lark_auto_choose(self, cs, choices: list) -> str:
        """显示选项到飞书卡片，等待用户回复消息选择。"""
        if not choices:
            return "[ERROR] 选项列表不能为空"

        # 查找对应的 card_id
        card_id = None
        for data in self._sessions.values():
            if data["session"] is cs:
                card_id = data.get("card_id")
                break

        # 组装选项文本
        lines = ["请选择一项（回复对应序号或内容）：\n"]
        for i, choice in enumerate(choices, 1):
            lines.append(f"{i}. {choice}")
        options_text = "\n".join(lines)

        if card_id:
            self._update_card_content(card_id, options_text, 0, cs)

        # 设置挂起标志，等待用户回复
        cs._pending_ask_user = True
        cs._user_choice_event.clear()
        cs._user_choice_result = None
        try:
            await cs._user_choice_event.wait()
        finally:
            cs._pending_ask_user = False

        if cs._user_choice_result is None:
            return "[用户取消了选择]"
        return cs._user_choice_result

    # ====================== Card Operations ======================

    def _create_reply_card(self, sender_open_id: str, chat_session: ChatSession) -> Optional[str]:
        """创建卡片，返回 card_id。"""
        if not self._lark_client:
            return None

        # 使用 base_card_dict 并序列化为 JSON
        safe_json = json.dumps(base_card_dict, ensure_ascii=False)
        request = (CreateCardRequest.builder()
                   .request_body(CreateCardRequestBody.builder()
                                 .type("card_json")
                                 .data(safe_json)
                                 .build())
                   .build())
        response = self._lark_client.cardkit.v1.card.create(request)
        if not response.success():
            chat_session.append_info_to_log("LARK",
                (f"创建卡片失败, code: {response.code}, msg: {response.msg}, "
                f"log_id: {response.get_log_id()}")
            )
            return None
        return response.data.card_id

    def _reply_with_card(self, message_id: str, card_id: str, chat_session: ChatSession) -> bool:
        """将卡片作为消息回复。"""
        if not self._lark_client:
            return False

        card_json = {"type": "card", "data": {"card_id": card_id}}
        request = (ReplyMessageRequest.builder()
                   .message_id(message_id)
                   .request_body(ReplyMessageRequestBody.builder()
                                 .msg_type("interactive")
                                 .content(json.dumps(card_json))
                                 .reply_in_thread(False)
                                 .uuid(str(uuid.uuid4()))
                                 .build())
                   .build())
        response = self._lark_client.im.v1.message.reply(request)
        if not response.success():
            chat_session.append_info_to_log("LARK",(
                f"回复卡片失败, code: {response.code}, msg: {response.msg}, "
                f"log_id: {response.get_log_id()}"
            ))
            return False
        return True

    def _update_card_content(self, card_id: str, content: str, sequence: int, chat_session: ChatSession) -> bool:
        """更新卡片元素内容。"""
        if not self._lark_client:
            return False

        # 元素 ID 对应 base_card_dict 中 markdown 元素的 element_id
        element_id = "ai_msg_content"
        request = (ContentCardElementRequest.builder()
                   .card_id(card_id)
                   .element_id(element_id)
                   .request_body(ContentCardElementRequestBody.builder()
                                 .uuid(str(uuid.uuid4()))
                                 .content(content)
                                 .sequence(sequence)
                                 .build())
                   .build())
        response = self._lark_client.cardkit.v1.card_element.content(request)
        if not response.success():
            chat_session.append_info_to_log("LARK",(
                f"更新卡片内容失败, code: {response.code}, msg: {response.msg}, "
                f"log_id: {response.get_log_id()}"
            ))
            return False
        return True

    def _replace_image_keys(self, markdown_text: str) -> str:
        """将markdown中的图片URL替换为飞书image_key。返回替换后的文本。"""
        pattern = r'(!\[.*?\]\((.*?)\))'
        urls = re.findall(pattern, markdown_text)
        result = markdown_text
        for url_m in urls:
            original_md = url_m[0]
            img_url = url_m[1]
            if not img_url.startswith("http"):
                continue
            try:
                with httpx.Client(follow_redirects=True) as http_client:
                    resp = http_client.get(img_url)
                    resp.raise_for_status()
                    img_bytes = BytesIO(resp.content)
            except Exception as e:
                print(f"[LarkBot] 下载图片失败: {e}")
                continue

            request = (CreateImageRequest.builder()
                       .request_body(CreateImageRequestBody.builder()
                                     .image_type("message")
                                     .image(img_bytes)
                                     .build())
                       .build())
            response = self._lark_client.im.v1.image.create(request)
            if response.success():
                result = result.replace(original_md,
                                        original_md.replace(img_url, response.data.image_key))
            else:
                print(f"[LarkBot] 上传图片失败: {response.msg}")
        return result

    # ====================== Message Processing ======================

    async def _process_message(self, user_prompt: str, message_id: str,
                                chat_id: str, sender_open_id: str):
        """处理一条飞书消息：创建卡片 → 处理消息 → 更新卡片。"""
        cs = await self._get_or_create_session(chat_id, sender_open_id)

        # 如果有挂起的 ask_user 等待选择，将本条消息作为用户的选择结果
        if getattr(cs, '_pending_ask_user', False):
            cs.answer_user_choice(user_prompt)
            return

        # 特殊命令处理
        if user_prompt.strip().lower() in ["帮助", "help"]:
            await cs.start_new_chat()
            card_id = self._create_reply_card(sender_open_id, cs)
            if card_id:
                self._reply_with_card(message_id, card_id, cs)

                # 构建 MCP 服务名列表
                mcp_lines = []
                for s in cs.servers:
                    if s in cs.invalid_servers:
                        continue
                    when = s.config.get("when_to_use", "")
                    mcp_lines.append(f"- **{s.name}**：{when}" if when else f"- **{s.name}**")
                mcp_infos = "\n".join(mcp_lines) if mcp_lines else "（无可用MCP服务）"

                # 构建 Skill 名列表
                skill_lines = [f"- **{s['name']}**：{s['description']}" for s in cs.skills_meta]
                skill_infos = "\n".join(skill_lines) if skill_lines else "（无可用技能）"

                helo_info = f"""### 常用命令
1. 新对话、newchat、new chat、/snc：开启新的对话  
2. 使用多模态、切换多模态、/smm：切换到支持多模态的模型  
3. 恢复默认模型、默认模型、/sdm：切换到默认的模型 

### MCP服务
{mcp_infos}

### SKILLS
{skill_infos}
"""
                self._update_card_content(card_id, helo_info, 1, cs)
            return

        # 特殊命令处理
        if user_prompt.strip().lower() in ["新对话", "newchat", "new chat", "/snc"]:
            await cs.start_new_chat()
            card_id = self._create_reply_card( sender_open_id, cs)
            if card_id:
                self._reply_with_card(message_id, card_id, cs)
                self._update_card_content(card_id, "✨ 已开启新对话 ✨", 1, cs)
            return

        # 切换到支持多模态的模型
        if user_prompt.strip().lower() in ["使用多模态", "切换多模态", "/smm"]:
            card_id = self._create_reply_card(sender_open_id, cs)
            if card_id:
                self._reply_with_card(message_id, card_id, cs) 
                if cs.llm_client.support_multimodal:
                    self._update_card_content(card_id, "✨ 当前模型已经支持多模态 ✨", 1, cs)
                    return
                if cs.client_models:
                    for num, cm in cs.client_models.items():
                        if cm.get("support_multimodal"):
                            cs.switch_model(num)
                            self._update_card_content(card_id, f"✨ 切换到大模型{cm["ai_model"]} ✨", 1, cs)
                            return
                self._update_card_content(card_id, "✨ 无支持多模态的大模型 ✨", 1, cs)
            return

        # 切换到支持多模态的模型
        if user_prompt.strip().lower() in ["恢复默认模型", "默认模型", "/sdm"]:
            card_id = self._create_reply_card(sender_open_id, cs)
            if card_id:
                self._reply_with_card(message_id, card_id, cs) 
                if cs.client_models:
                    cs.switch_model("1")
                self._update_card_content(card_id, "✨ 已经恢复默认大模型 ✨", 1, cs)
            return
            
            

        # 创建卡片（显示"Thinking..."）
        card_id = self._create_reply_card( sender_open_id, cs)
        if not card_id:
            return  # 卡片创建失败，静默处理
        self._reply_with_card(message_id, card_id, cs)

        # 记录 card_id 到 session 数据中，供 _lark_auto_choose 使用
        for data in self._sessions.values():
            if data["session"] is cs:
                data["card_id"] = card_id
                break

        # 处理消息 — 始终使用 Agent 模式
        sequence = 0
        try:
            # 设置事件回调，流式更新卡片（仅更新纯文本，不做图片替换——太慢）
            stream_buffer = [""]
            last_update_len = [0]

            def card_updater(event):
                nonlocal sequence
                from tiny_claw_core.core import EventType
                if event.type == EventType.STREAMING:
                    chunk = event.data.get("content", "")
                    if chunk:
                        stream_buffer[0] += chunk
                        # 每累积~80个新字符更新一次卡片
                        if len(stream_buffer[0]) - last_update_len[0] > 80:
                            last_update_len[0] = len(stream_buffer[0])
                            sequence += 1
                            self._update_card_content(card_id, stream_buffer[0], sequence, cs)

            cs._event_callback = card_updater

            final_response = await cs.process_user_message(user_prompt)
            if final_response:
                sequence += 1
                # 最终结果做一次图片替换
                safe_content = self._replace_image_keys(final_response)
                self._update_card_content(card_id, safe_content, sequence, cs)
        except Exception as e:
            error_msg = f"处理消息出错: {str(e)}, {traceback.format_exc()}"
            sequence += 1
            self._update_card_content(card_id, error_msg, sequence, cs)
        finally:
            cs._event_callback = None
            # 清理过期会话
            self._cleanup_expired_sessions()

    # ====================== Event Handler ======================

    def _on_message_receive(self, data: lark.im.v1.P2ImMessageReceiveV1):
        """飞书消息接收事件处理器。"""
        try:
            message_data = data
            event_id = message_data.header.event_id
            create_time_stamp = message_data.header.create_time
            create_time = datetime.fromtimestamp(int(create_time_stamp) / 1000)

            # 去重
            if event_id in self._event_ids:
                print("[LarkBot] 重复事件，跳过")
                return
            if len(self._event_ids) > 100:
                self._event_ids.pop(0)
            self._event_ids.append(event_id)

            # 过滤超时消息（超过30分钟不处理）
            if (datetime.now() - create_time).total_seconds() > 1800:
                print("[LarkBot] 消息超过30分钟，跳过")
                return

            # 解析消息内容
            message_type = message_data.event.message.message_type
            user_key = message_data.event.message.mentions[0].key if message_data.event.message.mentions else ""
            content_src = message_data.event.message.content
            content_json = json.loads(content_src)

            if message_type == "text":
                user_prompt = content_json.get('text', "").replace(user_key, "").strip()
            elif message_type == "post":
                user_prompt = content_src
                contents = content_json.get('content', [])
                if contents:
                    for tag_content in contents[-1]:
                        if tag_content.get('tag') == "text":
                            user_prompt = tag_content.get('text', "")
            else:
                user_prompt = content_src

            sender_open_id = message_data.event.sender.sender_id.open_id
            chat_id = message_data.event.message.chat_id
            message_id = message_data.event.message.message_id

            print(f"[LarkBot] 收到消息: {user_prompt[:100]} from {sender_open_id}")

            # 异步处理
            asyncio.create_task(
                self._process_message(user_prompt, message_id, chat_id, sender_open_id)
            )
        except Exception as e:
            print(f"[LarkBot] 消息处理错误: {e}")
            print(traceback.format_exc())

    # ====================== Lifecycle ======================

    def start(self):
        """启动飞书Bot（同步阻塞，适用于独立运行）。"""
        if not self.load_config():
            print("[LarkBot] 配置未启用或无效，不启动")
            return

        self._lark_client = (lark.Client.builder()
                             .app_id(self._config["app_id"])
                             .app_secret(self._config["app_secret"])
                             .log_level(lark.LogLevel.ERROR)
                             .build())

        event_handler = (lark.EventDispatcherHandler.builder("", "")
                         .register_p2_im_message_receive_v1(self._on_message_receive)
                         .build())

        self._ws_client = lark.ws.Client(
            self._config["app_id"],
            self._config["app_secret"],
            event_handler=event_handler,
            log_level=lark.LogLevel.ERROR
        )
        self._running = True
        print("[LarkBot] 启动 WebSocket 连接...")
        self._ws_client.start()

    async def start_async(self):
        """在异步后台启动飞书Bot（用于集成到TUI）。
        使用 daemon 线程运行 WS 长连接，避免 executor 阻塞 Python 关闭。"""
        if not self.load_config():
            print("[LarkBot] 配置未启用或无效，不启动")
            return

        self._lark_client = (lark.Client.builder()
                             .app_id(self._config["app_id"])
                             .app_secret(self._config["app_secret"])
                             .log_level(lark.LogLevel.ERROR)
                             .build())

        event_handler = (lark.EventDispatcherHandler.builder("", "")
                         .register_p2_im_message_receive_v1(self._on_message_receive)
                         .build())

        self._ws_client = lark.ws.Client(
            self._config["app_id"],
            self._config["app_secret"],
            event_handler=event_handler,
            log_level=lark.LogLevel.ERROR
        )
        self._running = True
        print("[LarkBot] 后台异步启动 WebSocket 连接...")

        def _run_ws():
            try:
                self._ws_client.start()
            except Exception as e:
                print(f"[LarkBot] WS 线程异常退出: {e}")
            finally:
                self._running = False
                print("[LarkBot] WS 线程已退出")

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True, name="lark-ws")
        self._ws_thread.start()

    def stop(self):
        """停止飞书Bot（同步，用于非异步上下文）。"""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception:
                pass
            self._ws_client = None
        # WS 是 daemon 线程，Python 退出时会自动清理，不阻塞
        self._ws_thread = None
        self._cleanup_sessions()

    async def stop_async(self):
        """停止飞书Bot（异步，用于TUI集成）。通知 WS 停止，daemon 线程自行退出。"""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception:
                pass
            self._ws_client = None
        self._ws_thread = None
        self._cleanup_sessions()

    def _cleanup_sessions(self):
        """清理所有会话。"""
        for session_data in self._sessions.values():
            cs = session_data["session"]
            if cs.agent_switch != 0:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(cs.disable_agent())
                except Exception:
                    pass
        self._sessions.clear()

    @property
    def is_running(self) -> bool:
        return self._running
