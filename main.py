import os
import re
import random
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, AuthKeyUnregisteredError

# ================= 1. 日志配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)

# ================= 2. 环境变量 =================
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
SESSION_STRINGS_RAW = os.environ.get('SESSION_STRINGS', '')
TARGET_CHAT_ID = os.environ.get('TARGET_CHAT_ID', '')
CONTROLLER_SESSION = os.environ.get('CONTROLLER_SESSION', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

session_strings = [s.strip() for s in SESSION_STRINGS_RAW.split(',') if s.strip()]

if not all([API_ID, API_HASH, session_strings, TARGET_CHAT_ID, CONTROLLER_SESSION, ADMIN_ID]):
    logger.error("❌ 环境变量缺失！请检查相关配置")
    exit(1)

# ================= 3. 全局状态与并发锁 =================
used_numbers = set()         # 记录全局已出现的号码（群友的 + 自己发的）
number_lock = asyncio.Lock() # 核心：防止两个号同时抽到同一个号
is_sending = False           # 发送开关
total_sent = 0               # 自己发出去的总条数（上限40）
MAX_SEND_COUNT = 40          # 最多发送40条（两个号各20条）
spam_tasks = []

# ================= 4. 初始化客户端 =================
DEVICES = [
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 13", "app_version": "10.2.1"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 16.5", "app_version": "10.1.0"},
]

clients = []
for i, s in enumerate(session_strings):
    dev = DEVICES[i % len(DEVICES)]
    clients.append(TelegramClient(StringSession(s), API_ID, API_HASH, **dev))

controller_client = TelegramClient(
    StringSession(CONTROLLER_SESSION), API_ID, API_HASH,
    device_model="Telegram Desktop", system_version="Linux", app_version="4.8.1"
)

# ================= 5. 动态随机发送工作线程 =================
async def spam_worker(client, worker_id):
    global is_sending, total_sent
    
    try:
        me = await client.get_me()
        client_name = me.username or str(me.id)
    except Exception:
        client_name = f"账号{worker_id}"

    while is_sending:
        async with number_lock: # 加锁，确保此刻只有这一个号在生成随机数
            if total_sent >= MAX_SEND_COUNT:
                logger.info(f"✅ [{client_name}] 已达最大发送数量（{MAX_SEND_COUNT}条），准备停止。")
                is_sending = False # 触发全局停止
                break
            
            # 动态生成 1-6 的随机三位数
            max_retries = 30
            rand_num = None
            for _ in range(max_retries):
                # 每次即时生成，不预存
                temp_num = f"{random.randint(1, 6)}{random.randint(1, 6)}{random.randint(1, 6)}"
                
                # 双重去重：不能和群友重复，也不能和自己重复
                if temp_num not in used_numbers:
                    rand_num = temp_num
                    used_numbers.add(rand_num) # 立刻登记，防止另一个号马上抢走
                    total_sent += 1
                    break
                    
            if rand_num is None:
                logger.warning(f"⚠️ [{client_name}] 连续 {max_retries} 次抽取都撞号，等待 0.5 秒重试...")
                await asyncio.sleep(0.5)
                continue

        # ---- 释放锁之后再进行网络发送，避免阻塞另一个号抽号 ----
        content = f"八号担保，全网收购新币公群-{rand_num}"
        try:
            await client.send_message(TARGET_CHAT_ID, content)
            logger.info(f"🚀 [{client_name}] 发送成功: {rand_num} (自己进度: {total_sent}/{MAX_SEND_COUNT})")
        except FloodWaitError as e:
            logger.warning(f"⚠️ [{client_name}] 触发限制，等待 {e.seconds} 秒...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"❌ [{client_name}] 发送失败: {e}")
            await asyncio.sleep(1)
        
        # 500ms 间隔
        await asyncio.sleep(0.5)

# ================= 6. 主程序逻辑 =================
async def main():
    global is_sending, spam_tasks, used_numbers, total_sent

    # 启动打手
    for i, client in enumerate(clients):
        try:
            await client.start()
            logger.info(f"✅ 打手账号 {i+1} 启动成功")
        except Exception as e:
            logger.error(f"❌ 打手账号 {i+1} 启动失败: {e}")

    # 启动控制账号
    try:
        await controller_client.start()
        logger.info("👑 独立控制账号启动成功，正在监听群组指令...")
    except Exception as e:
        logger.error(f"❌ 控制账号启动失败: {e}")
        exit(1)

    @controller_client.on(events.NewMessage(chats=TARGET_CHAT_ID))
    async def handler(event):
        global is_sending, spam_tasks, used_numbers, total_sent
        text = event.text.strip() if event.text else ""
        sender_id = event.sender_id

        # ---- 实时监控群友的号码，动态加入黑名单 ----
        if sender_id != ADMIN_ID:
            match = re.search(r"八号担保，全网收购新币公群-([1-6]{3})", text)
            if match:
                num = match.group(1)
                async with number_lock:
                    if num not in used_numbers:
                        used_numbers.add(num)
                        logger.info(f"📥 监控到群内新号码: {num} (已自动排除)")
            return

        # ---- 管理员指令区 ----
        if text == "1":
            if is_sending:
                logger.info("⏳ 已经在发送中...")
                return
            
            logger.info("🔥 收到指令 '1'，动态随机轰炸模式启动！")
            is_sending = True
            total_sent = 0 # 重置计数器
            
            # 启动2个打手任务并发消费
            for i in range(len(clients)):
                task = asyncio.create_task(spam_worker(clients[i], i+1))
                spam_tasks.append(task)
            
            logger.info("🚀 已启动 2 个并发线程，目标 40 条，每条间隔 500ms。")

        elif text == "2":
            logger.info("🛑 收到指令 '2'，强行停止发送...")
            is_sending = False
            for task in spam_tasks:
                task.cancel()
            spam_tasks.clear()
            logger.info("✅ 已彻底停止。")

    await controller_client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
