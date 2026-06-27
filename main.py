import os, re, aiohttp, asyncio, yt_dlp, logging, uuid, requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyParameters
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid, MessageNotModified
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

api_id = int(os.getenv("API_ID", 0))
api_hash = os.getenv("API_HASH", "")
bot_token = os.getenv("BOT_TOKEN", "")
db_url = os.getenv("DB_URL", "")
db_name = "InstaDLBot"
support_gc = os.getenv("SUPPORT_GROUP", "")
support_ch = os.getenv("SUPPORT_CHANNEL", "")
owner = list(map(int, os.getenv("OWNER_ID", "7706682472").split()))

direct_fsub_id = os.getenv("DIRECT_FSUB_ID", "")
request_fsub_id = os.getenv("REQUEST_FSUB_ID", "")
auto_delete_time = int(os.getenv("AUTO_DELETE_TIME", "300")) #5mi

YDL_OPTS = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'outtmpl': 'downloads/%(title)s.%(ext)s',
    'quiet': True,
    'no_warnings': True,
}

video_urls_cache = {}
is_broadcasting = False

if not os.path.exists("downloads"):
    os.makedirs("downloads")

class Database:
    def __init__(self, uri, db_name):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.usersdb = self.db.users
        self.chatsdb = self.db.chats
        self.cache = {"users": [], "chats": []}

    async def get_served_users(self):
        if not self.cache["users"]:
            async for user in self.usersdb.find({"user_id": {"$gt": 0}}):
                self.cache["users"].append(user["user_id"])
        return self.cache["users"]

    async def add_served_user(self, user_id):
        await self.get_served_users()
        if user_id not in self.cache["users"]:
            await self.usersdb.insert_one({"user_id": user_id})
            self.cache["users"].append(user_id)

    async def get_served_chats(self):
        if not self.cache["chats"]:
            async for chat in self.chatsdb.find({"chat_id": {"$lt": 0}}):
                self.cache["chats"].append(chat["chat_id"])
        return self.cache["chats"]

    async def add_served_chat(self, chat_id):
        await self.get_served_chats()
        if chat_id not in self.cache["chats"]:
            await self.chatsdb.insert_one({"chat_id": chat_id})
            self.cache["chats"].append(chat_id)

db = Database(db_url, db_name)

app = Client("my_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

INSTA_REGEX = r"^(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reel|tv)/([^/?#&]+).*"

def get_download_url(instagram_url):
    try:
        r = requests.post(
            "https://savereels.io/api/ajaxSearch",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://savereels.io/en/instagram-reels-downloader",
            },
            data={"q": instagram_url, "w": "", "v": "v2", "lang": "en", "cftoken": ""},
            timeout=20,
        ).json()

        a = BeautifulSoup(r["data"], "html.parser").select_one("a[href*='dl.snapcdn.app/get']")
        return a["href"] if a else None
    except Exception as e:
        logger.error(f"get_download_url failed: {e}")
        return None

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    await db.add_served_user(message.from_user.id)
    if message.chat.type.name in ["GROUP", "SUPERGROUP"]:
        await db.add_served_chat(message.chat.id)
    
    me = await client.get_me()
    start_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✙ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ✙", url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("· ᴜᴘᴅᴀᴛᴇ ·", url=support_ch), InlineKeyboardButton("· sᴜᴘᴘᴏʀᴛ ·", url=support_gc)]
    ])

    welcome_text = (
        f"<b>👋 Hello {message.from_user.first_name}!</b>\n\n"
        "I am an <b>Instagram Reels Downloader Bot</b>. Just send me an Instagram link and I will download it for you.\n\n"
        "<b>🔧 Commands:</b>\n"
        "• <code>/start</code> - To start the bot\n"
        "• <code>/stats</code> - Show bot stats (owner only)\n"
        "• <code>/gcast</code> - Broadcast message\n\n"
        "<b>Add me to your group for fast downloading!</b>"
    )
    await message.reply_text(welcome_text, reply_markup=start_buttons, parse_mode=enums.ParseMode.HTML)

async def check_fsub(client, message):
    if message.chat.type.name not in ["PRIVATE"]:
        return True
    if not direct_fsub_id and not request_fsub_id:
        return True
    
    missing = []
    
    if direct_fsub_id:
        try:
            m = await client.get_chat_member(direct_fsub_id, message.from_user.id)
            if m.status.name not in ["MEMBER", "ADMINISTRATOR", "OWNER"]:
                missing.append(("📢 Join Channel", direct_fsub_id))
        except Exception:
            missing.append(("📢 Join Channel", direct_fsub_id))
            
    if request_fsub_id:
        try:
            m = await client.get_chat_member(request_fsub_id, message.from_user.id)
            if m.status.name not in ["MEMBER", "ADMINISTRATOR", "OWNER"]:
                missing.append(("📩 Request to Join", request_fsub_id))
        except Exception:
            missing.append(("📩 Request to Join", request_fsub_id))
            
    if not missing:
        return True
    
    buttons = []
    for text, c_id in missing:
        try:
            chat = await client.get_chat(c_id)
            url = chat.invite_link or f"https://t.me/{chat.username}"
            buttons.append([InlineKeyboardButton(text, url=url)])
        except Exception:
            pass
            
    buttons.append([InlineKeyboardButton("🔄 Verify Subscription", callback_data="check_sub")])
    
    await message.reply_text(
        "<b>⚠️ Access Denied!</b>\n\nYou must join our channels to use this bot.",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )
    return False

async def auto_delete(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

@app.on_callback_query(filters.regex("check_sub"))
async def check_sub_callback(client, callback_query):
    if await check_fsub(client, callback_query.message):
        try:
            await callback_query.message.delete()
        except:
            pass
        await callback_query.message.reply_text("✅ Thank you for joining! You can now use the bot.")
    else:
        await callback_query.answer("❌ You haven't joined yet!", show_alert=True)

@app.on_message(filters.text & filters.regex(INSTA_REGEX))
@app.on_edited_message(filters.text & filters.regex(INSTA_REGEX))
async def insta_link_handler(client, message: Message):
    if not await check_fsub(client, message):
        return
    match = re.search(INSTA_REGEX, message.text)
    if not match:
        return

    insta_url = match.group(0)
    status_msg = await message.reply_text("🔎 Processing your link...")

    video_url = await asyncio.to_thread(get_download_url, insta_url)

    if not video_url:
        await status_msg.edit_text("❌ Sorry, I couldn't download this reel. The API failed.")
        return

    file_path = f"downloads/{uuid.uuid4().hex}.mp4"
    try:
        await status_msg.edit_text("⏳ Downloading video...")
        video_urls_cache[str(message.id)] = video_url
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Download Audio 🎵", callback_data=f"audio_{message.id}")],
            [InlineKeyboardButton("· ᴜᴘᴅᴀᴛᴇ ·", url=support_ch), InlineKeyboardButton("· sᴜᴘᴘᴏʀᴛ ·", url=support_gc)]
        ])

        async with aiohttp.ClientSession() as session:
            async with session.get(video_url) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(file_path, "wb") as f:
                        f.write(content)
                else:
                    raise Exception("Failed to download video locally")

        await status_msg.edit_text("⏳ Uploading video...")
        sent_video = await client.send_video(
            chat_id=message.chat.id,
            video=file_path,
            reply_parameters=ReplyParameters(message_id=message.id),
            reply_markup=keyboard
        )
        asyncio.create_task(auto_delete(sent_video, auto_delete_time))
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        await status_msg.edit_text(f"❌ Error sending video: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@app.on_callback_query(filters.regex(r"^audio_(\d+)$"))
async def audio_callback_handler(client, callback_query):
    if not await check_fsub(client, callback_query.message):
        return
    message_id = callback_query.data.split("_")[1]
    video_url = video_urls_cache.get(message_id)

    if not video_url:
        await callback_query.answer("❌ Video URL expired or not found. Please resend the link.", show_alert=True)
        return

    await callback_query.answer("⏳ Processing Audio...")
    status_msg = await callback_query.message.reply_text("🎵 Extracting audio, please wait...")

    try:
        def download_audio():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(video_url, download=True)
                return ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3", info.get("title", "audio")

        audio_path, title = await asyncio.to_thread(download_audio)

        await status_msg.edit_text("📤 Sending audio...")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("· ᴜᴘᴅᴀᴛᴇ ·", url=support_ch),
            InlineKeyboardButton("· sᴜᴘᴘᴏʀᴛ ·", url=support_gc)
        ]])
        sent_audio = await client.send_audio(
            chat_id=callback_query.message.chat.id,
            audio=audio_path,
            title=title,
            performer="@InstaRdownloadbot",
            reply_markup=keyboard
        )
        asyncio.create_task(auto_delete(sent_audio, auto_delete_time))

        if os.path.exists(audio_path):
            os.remove(audio_path)
        
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Audio extraction failed: {e}")
        await status_msg.edit_text(f"❌ Failed to extract audio: {str(e)}")

@app.on_message(filters.command("stats") & filters.user(owner))
async def stats(client, message):
    users = len(await db.get_served_users())
    chats = len(await db.get_served_chats())
    await message.reply_text(f"Total Chats: {chats}\nTotal Users: {users}")

@app.on_message(filters.command(["gcast", "broadcast", "gcastpin", "broadcastpin"]) & filters.user(owner))
async def gcast_command(client, message):
    global is_broadcasting
    if is_broadcasting:
        return await message.reply_text("⚠️ A broadcast is already in progress.")

    is_broadcasting = True
    chats = await db.get_served_chats()
    users = await db.get_served_users()
    targets = list(set(chats + users))

    pin = message.command[0].endswith("pin")

    if message.reply_to_message:
        msg = message.reply_to_message
        msg_text = None
    elif len(message.command) > 1:
        msg_text = message.text.split(None, 1)[1]
        msg = None
    else:
        is_broadcasting = False
        return await message.reply_text("❌ Provide text or reply to a message to broadcast.")

    panel = await message.reply_text("📣 Broadcasting Message...")

    success = 0
    failed = 0

    for chat_id in targets:
        try:
            if msg:
                sent = await msg.copy(chat_id)
            else:
                sent = await client.send_message(chat_id, msg_text)
            if pin:
                try:
                    await sent.pin(disable_notification=False)
                except Exception:
                    pass
            success += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                if msg:
                    sent = await msg.copy(chat_id)
                else:
                    sent = await client.send_message(chat_id, msg_text)
                if pin:
                    await sent.pin(disable_notification=False)
                success += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, PeerIdInvalid, MessageNotModified):
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)

    await panel.edit(f"📢 Broadcast Complete\n✅ Success: {success}\n❌ Failed: {failed}")
    is_broadcasting = False

if __name__ == "__main__":
    logger.info("Bot starting...")
    app.run()
