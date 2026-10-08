import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, AuthKeyUnregisteredError

# 1. 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 2. 读取环境变量
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
SESSION_STRINGS_RAW = os.environ.get('SESSION_STRINGS', '')
MESSAGE_CONTENTS_RAW = os.environ.get('MESSAGE_CONTENTS', '')
TARGET_CHAT_ID = os.environ.get('TARGET_CHAT_ID', '')

session_strings = [s.strip() for s in SESSION_STRINGS_RAW.split(',') if s.strip()]
message_contents = [m.strip() for m in MESSAGE_CONTENTS_RAW.split('|') if m.strip()]

# 3. 校验环境变量
if not all([API_ID, API_HASH, session_strings, TARGET_CHAT_ID]):
    logger.error("❌ 环境变量缺失！请检查 API_ID, API_HASH, SESSION_STRINGS, TARGET_CHAT_ID")
    exit(1)

if len(session_strings) != len(message_contents):
    logger.error(f"❌ 账号数量与内容数量不匹配！账号 {len(session_strings)} 个，内容 {len(message_contents)} 个。")
    exit(1)

logger.info(f"✅ 配置校验通过，共加载 {len(session_strings)} 个账号。")

# 4. 初始化客户端（加入官方桌面版伪装信息）
clients = []
for s in session_strings:
    clients.append(
        TelegramClient(
            StringSession(s), 
            API_ID, 
            API_HASH,
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.16.8"
        )
    )

is_spamming = False
spam_tasks = []

# 5. 工作线程
async def spam_worker(client, chat_id, content):
    while is_spamming:
        try:
            await client.send_message(chat_id, content)
            await asyncio.sleep(0.3) # 300ms
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"⚠️ 账号触发限制，等待 {wait_time} 秒...")
            await asyncio.sleep(wait_time)
            logger.info(f"⏱️ 倒计时结束，账号立刻继续发送。")
            continue
        except AuthKeyUnregisteredError:
            logger.error(f"❌ 某个账号 Session 已失效或被强制下线，停止该账号任务。")
            return
        except Exception as e:
            logger.error(f"❌ 发送失败: {e}")
            await asyncio.sleep(1)
            continue

# 6. 启动任务
async def start_spam(chat_id):
    global is_spamming, spam_tasks
    if is_spamming:
        return
    is_spamming = True
    logger.info("🚀 收到指令 '1'，开始高频发送...")
    for client, content in zip(clients, message_contents):
        task = asyncio.create_task(spam_worker(client, chat_id, content))
        spam_tasks.append(task)

# 7. 停止任务
async def stop_spam():
    global is_spamming, spam_tasks
    if not is_spamming:
        return
    is_spamming = False
    for task in spam_tasks:
        task.cancel()
    spam_tasks.clear()
    logger.info("🛑 收到指令 '2'，已全部停止发送。")

# 8. 主程序
async def main():
    for client in clients:
        try:
            await client.start()
            logger.info("✅ 一个客户端启动成功")
        except Exception as e:
            logger.error(f"客户端启动失败: {e}")

    @clients[0].on(events.NewMessage(chats=TARGET_CHAT_ID))
    async def handler(event):
        if event.out:
            return
        text = event.text.strip() if event.text else ""
        if text == "1":
            await start_spam(event.chat_id)
        elif text == "2":
            await stop_spam()

    logger.info("🎧 监听中...")
    await clients[0].run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass