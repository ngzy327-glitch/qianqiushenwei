import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, 
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    UserNotParticipantError
)

# ================= 1. 配置日志 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 屏蔽 Telethon 底层无用的同步日志（防止刷屏）
logging.getLogger('telethon').setLevel(logging.WARNING)

# ================= 2. 读取并校验环境变量 =================
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
SESSION_STRINGS_RAW = os.environ.get('SESSION_STRINGS', '')
MESSAGE_CONTENTS_RAW = os.environ.get('MESSAGE_CONTENTS', '')
TARGET_CHAT_ID = os.environ.get('TARGET_CHAT_ID', '')
CONTROLLER_SESSION = os.environ.get('CONTROLLER_SESSION', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

session_strings = [s.strip() for s in SESSION_STRINGS_RAW.split(',') if s.strip()]
message_contents = [m.strip() for m in MESSAGE_CONTENTS_RAW.split('|') if m.strip()]

if not all([API_ID, API_HASH, session_strings, TARGET_CHAT_ID, CONTROLLER_SESSION, ADMIN_ID]):
    logger.error("❌ 环境变量缺失！请检查 API_ID, API_HASH, SESSION_STRINGS, MESSAGE_CONTENTS, TARGET_CHAT_ID, CONTROLLER_SESSION, ADMIN_ID")
    exit(1)

if len(session_strings) != len(message_contents):
    logger.error(f"❌ 账号数量与内容数量不匹配！账号 {len(session_strings)} 个，内容 {len(message_contents)} 个。")
    exit(1)

logger.info(f"✅ 配置校验通过，共加载 {len(session_strings)} 个打手账号。")
logger.info("👑 正在初始化独立控制账号...")

# ================= 3. 初始化客户端（分配不同的设备伪装） =================
# 预定义设备指纹列表，防止多个账号被 Telegram 识别为同一设备多开
DEVICES = [
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 13", "app_version": "10.2.1"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 16.5", "app_version": "10.1.0"},
    {"device_model": "Xiaomi 13", "system_version": "Android 13", "app_version": "9.6.2"},
    {"device_model": "PC", "system_version": "Windows 11", "app_version": "4.8.1"},
    {"device_model": "MacBook Pro", "system_version": "macOS 13.4", "app_version": "4.9.0"},
    {"device_model": "OnePlus 11", "system_version": "Android 13", "app_version": "10.0.4"},
    {"device_model": "Google Pixel 7", "system_version": "Android 13", "app_version": "10.2.0"},
    {"device_model": "Redmi Note 12", "system_version": "Android 12", "app_version": "9.5.1"},
    {"device_model": "iPad Pro", "system_version": "iOS 16.4", "app_version": "10.0.2"},
    {"device_model": "Huawei P60", "system_version": "HarmonyOS 3.1", "app_version": "9.6.1"}
]

clients = []
for i, s in enumerate(session_strings):
    dev = DEVICES[i % len(DEVICES)]
    clients.append(
        TelegramClient(
            StringSession(s), 
            API_ID, 
            API_HASH,
            device_model=dev["device_model"],
            system_version=dev["system_version"],
            app_version=dev["app_version"]
        )
    )

# 初始化独立控制账号（使用与打手不同的设备指纹）
controller_client = TelegramClient(
    StringSession(CONTROLLER_SESSION),
    API_ID,
    API_HASH,
    device_model="Telegram Desktop",
    system_version="Linux",
    app_version="4.8.1"
)

# ================= 4. 全局状态和核心逻辑 =================
is_spamming = False
spam_tasks = []

async def spam_worker(client, chat_id, content):
    """打手账号工作线程"""
    # 获取账号标识，方便在日志里区分是哪个号在报错
    try:
        me = await client.get_me()
        client_name = me.username or str(me.id)
    except Exception:
        client_name = "未知账号"

    while is_spamming:
        try:
            # 尝试发送消息
            await client.send_message(chat_id, content)
            
            # 【重要】打开成功日志，让你知道哪些号在正常工作
            logger.info(f"✅ [{client_name}] 发送成功: {content}")
            
            # 常规间隔 300ms
            await asyncio.sleep(0.3)
            
        except FloodWaitError as e:
            # 触发慢速模式或风控限制，精准等待
            logger.warning(f"⚠️ [{client_name}] 触发慢速限制，需要等待 {e.seconds} 秒...")
            await asyncio.sleep(e.seconds)
            logger.info(f"⏱️ [{client_name}] 倒计时结束，立刻继续发送。")
            # 注意：这里 continue 会跳过上面的 sleep(0.3)，实现倒计时一结束立刻发
            continue
            
        except (ChatWriteForbiddenError, UserNotParticipantError) as e:
            logger.error(f"❌ [{client_name}] 无权限在该群发言！可能已被禁言或不在群内。错误: {e}")
            return # 结束该账号的任务
            
        except AuthKeyUnregisteredError:
            logger.error(f"❌ [{client_name}] 的 Session 已失效或被强制下线！")
            return
            
        except Exception as e:
            logger.error(f"❌ [{client_name}] 发送失败: {e}")
            await asyncio.sleep(1)
            continue

async def start_spam(chat_id):
    """启动所有打手账号"""
    global is_spamming, spam_tasks
    if is_spamming:
        logger.info("⏳ 已经在发送中，无需重复启动。")
        return
    is_spamming = True
    logger.info("🚀 收到指令 '1'，开始高频发送...")
    
    for client, content in zip(clients, message_contents):
        task = asyncio.create_task(spam_worker(client, chat_id, content))
        spam_tasks.append(task)

async def stop_spam():
    """停止所有打手账号"""
    global is_spamming, spam_tasks
    if not is_spamming:
        return
    is_spamming = False
    for task in spam_tasks:
        task.cancel()
    spam_tasks.clear()
    logger.info("🛑 收到指令 '2'，已全部停止发送。")

# ================= 5. 主程序 =================
async def main():
    # 1. 启动所有打手账号
    for i, client in enumerate(clients):
        try:
            await client.start()
            logger.info(f"✅ 打手账号 {i+1} 启动成功")
        except Exception as e:
            logger.error(f"❌ 打手账号 {i+1} 启动失败: {e}")
            
    # 2. 启动独立控制账号
    try:
        await controller_client.start()
        logger.info("👑 独立控制账号启动成功，正在监听群组指令...")
    except Exception as e:
        logger.error(f"❌ 控制账号启动失败: {e}")
        exit(1)

    # 3. 只让控制账号监听指令，避免打手账号因为风控漏掉消息
    @controller_client.on(events.NewMessage(chats=TARGET_CHAT_ID))
    async def handler(event):
        # 过滤：只有指定的管理员 ID 才能触发指令
        if event.sender_id != ADMIN_ID:
            return
            
        text = event.text.strip() if event.text else ""
        
        if text == "1":
            logger.info("👑 收到管理员指令 '1'")
            await start_spam(event.chat_id)
        elif text == "2":
            logger.info("👑 收到管理员指令 '2'")
            await stop_spam()

    # 保持程序运行
    await controller_client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
