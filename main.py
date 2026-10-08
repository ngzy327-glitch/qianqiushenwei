import os
import random
import asyncio
import logging
import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, AuthKeyUnregisteredError, UserDeactivatedError, SessionRevokedError

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

# ================= 3. 全局状态 =================
my_sent_numbers = set()      # 记录自己发过的号码
number_lock = asyncio.Lock() # 并发锁
is_sending = False           # 全局开关（任何一个账号存活都会继续）
spam_tasks = []

# ================= 4. 初始化客户端（扩充设备库） =================
# 扩充了12个设备，确保你即使挂5个号也不会设备指纹冲突
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
    {"device_model": "Huawei P60", "system_version": "HarmonyOS 3.1", "app_version": "9.6.1"},
    {"device_model": "Oppo Find X6", "system_version": "Android 13", "app_version": "9.7.0"},
    {"device_model": "Vivo X90", "system_version": "Android 13", "app_version": "9.6.5"}
]

clients = []
for i, s in enumerate(session_strings):
    dev = DEVICES[i % len(DEVICES)]
    clients.append(TelegramClient(StringSession(s), API_ID, API_HASH, **dev))

controller_client = TelegramClient(
    StringSession(CONTROLLER_SESSION), API_ID, API_HASH,
    device_model="Telegram Desktop", system_version="Linux", app_version="4.8.1"
)

# ================= 5. 发送工作线程 =================
async def spam_worker(client, worker_id):
    global is_sending
    
    try:
        me = await client.get_me()
        client_name = me.username or str(me.id)
    except Exception:
        client_name = f"账号{worker_id}"

    while is_sending:
        # --- 1. 时间检查 (基于 UTC+8) ---
        now_utc8 = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        if now_utc8.hour > 22 or (now_utc8.hour == 22 and now_utc8.minute >= 10):
            logger.info(f"⏰ [{client_name}] 时间已到 22:10，自动停止发送。")
            is_sending = False
            break

        async with number_lock:
            # --- 2. 检查号码是否发完，发完即止 ---
            if len(my_sent_numbers) >= 216:
                logger.info("🎉 216个不重复号码已全部发送完毕，系统自动停止。")
                is_sending = False
                break
            
            # --- 3. 动态生成随机号码 ---
            max_retries = 50
            rand_num = None
            for _ in range(max_retries):
                temp_num = f"{random.randint(1, 6)}{random.randint(1, 6)}{random.randint(1, 6)}"
                if temp_num not in my_sent_numbers:
                    rand_num = temp_num
                    my_sent_numbers.add(rand_num)
                    break
                    
            if rand_num is None:
                logger.warning(f"⚠️ [{client_name}] 抽取异常，跳过此轮。")
                continue

        # --- 4. 发送消息 ---
        content = f"八号担保，全网收购新币公群-{rand_num}"
        try:
            await client.send_message(TARGET_CHAT_ID, content)
            logger.info(f"🚀 [{client_name}] 发送成功: {rand_num} (进度: {len(my_sent_numbers)}/216)")
            
            # 350ms 间隔
            await asyncio.sleep(0.35)
            
        # --- 5. 异常处理（核心改动：区分风控与账号死亡） ---
        except ChatWriteForbiddenError:
            # 群组被禁言，所有账号都会发不出去，直接全局停止
            logger.error(f"❌ [{client_name}] 群组被禁言或账号无权限，全局停止！")
            is_sending = False
            break
        except (AuthKeyUnregisteredError, UserDeactivatedError, SessionRevokedError) as e:
            # 【关键】某个账号彻底死掉（被封禁/被注销），该账号任务退出，不影响其他账号
            logger.error(f"💀 [{client_name}] 账号已失效或被封禁 ({type(e).__name__})，该账号退出，剩余账号继续发送。")
            return # 仅退出当前worker，不修改is_sending
        except FloodWaitError as e:
            # 触发限制，该账号休息等待，其他账号继续
            logger.warning(f"⚠️ [{client_name}] 触发限制，等待 {e.seconds} 秒...")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"❌ [{client_name}] 发送失败: {e}")
            await asyncio.sleep(1)

# ================= 6. 主程序逻辑 =================
async def main():
    global is_sending, spam_tasks, my_sent_numbers

    for i, client in enumerate(clients):
        try:
            await client.start()
            logger.info(f"✅ 打手账号 {i+1} 启动成功")
        except Exception as e:
            logger.error(f"❌ 打手账号 {i+1} 启动失败 (可能已死): {e}")

    try:
        await controller_client.start()
        logger.info("👑 独立控制账号启动成功，正在监听群组指令...")
    except Exception as e:
        logger.error(f"❌ 控制账号启动失败: {e}")
        exit(1)

    @controller_client.on(events.NewMessage(chats=TARGET_CHAT_ID))
    async def handler(event):
        global is_sending, spam_tasks, my_sent_numbers
        text = event.text.strip() if event.text else ""

        if event.sender_id != ADMIN_ID:
            return

        if text == "1":
            if is_sending:
                logger.info("⏳ 已经在发送中...")
                return
            
            logger.info(f"🔥 收到指令 '1'，启动 {len(clients)} 个账号并发轰炸！(350ms/条)")
            my_sent_numbers.clear()
            is_sending = True
            
            for i, client in enumerate(clients):
                task = asyncio.create_task(spam_worker(client, i+1))
                spam_tasks.append(task)

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
