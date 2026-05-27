import os
import threading
import asyncio
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler,
    filters, ContextTypes,
    CallbackQueryHandler
)
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
SHOPEE_URL = os.environ.get("SHOPEE_URL", "https://shopee.vn")
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_data_store = {}

# ---------------- WEB SERVER ----------------
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(base_dir, "index.html")
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()  

            # Inject SHOPEE_URL vào meta tag
            content = content.replace(
                '</head>',
                f'<meta name="shopee-url" content="{SHOPEE_URL}">\n</head>'
            )

            encoded = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        except FileNotFoundError:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), WebHandler)
    server.serve_forever()

# ---------------- DOWNLOAD TIKTOK ----------------
def download_sync(url: str, output_path: str):
    import requests as req
    import subprocess

    # Thử API tikwm trước (ổn định nhất, không cần cookie)
    try:
        print("🔍 Thử API tikwm...")
        api_resp = req.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": 1},
            timeout=30
        )
        data = api_resp.json()
        video_url = (
            data.get("data", {}).get("hdplay") or
            data.get("data", {}).get("play")
        )
        if video_url:
            print("✅ tikwm OK, downloading...")
            r = req.get(video_url, stream=True, timeout=60, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.tiktok.com/"
            })
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("✅ Download xong từ tikwm")
            return
        else:
            print("⚠️ tikwm không có URL, thử yt-dlp...")
    except Exception as e:
        print(f"⚠️ tikwm lỗi: {e}, thử yt-dlp...")

    # Fallback: yt-dlp với user-agent giả mobile
    import yt_dlp as ytdl
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'quiet': True,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'com.zhiliaoapp.musically/2022600030 (Linux; U; Android 11; en_US; Pixel 4; Build/RQ3A.210805.001.A1; Cronet/58.0.2991.0)',
            'Referer': 'https://www.tiktok.com/',
        },
        'extractor_args': {
            'tiktok': {
                'webpage_download': True,
                'api_hostname': 'api22-normal-c-useast2a.tiktokv.com',
            }
        }
    }
    with ytdl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])




async def download_tiktok_video(url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "video.mp4")
    if os.path.exists(output_path):
        os.remove(output_path)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_sync, url, output_path)
    return output_path

# ---------------- ĐĂNG LÊN FACEBOOK ----------------
def post_to_facebook(video_path: str, caption: str, shopee_url: str) -> bool:
    full_caption = f"{caption}\n\n🛒 Mua ngay: {shopee_url}"
    upload_url = f"https://graph-video.facebook.com/v19.0/{FB_PAGE_ID}/videos"
    with open(video_path, "rb") as video_file:
        response = requests.post(
            upload_url,
            data={"description": full_caption, "access_token": FB_PAGE_TOKEN},
            files={"file": video_file},
            timeout=120
        )
    result = response.json()
    print("FB response:", result)
    if "id" in result:
        return True
    raise Exception(result.get("error", {}).get("message", "Lỗi không xác định"))

# ---------------- TELEGRAM HANDLERS ----------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting")

    if waiting == "caption":
        context.user_data["caption"] = text
        context.user_data["waiting"] = "shopee"
        await update.message.reply_text("🛒 Nhập link Shopee cho bài đăng FB:")

    elif waiting == "shopee":
        shopee_url = text
        caption = context.user_data.get("caption", "")
        video_path = user_data_store.get(user_id, {}).get("video_path")

        if not video_path or not os.path.exists(video_path):
            await update.message.reply_text("❌ Video không còn, gửi lại link TikTok!")
            context.user_data.clear()
            return

        msg = await update.message.reply_text("📤 Đang đăng lên Facebook...")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, post_to_facebook, video_path, caption, shopee_url
            )
            await msg.edit_text("✅ Đăng Facebook thành công!\n\nGửi link TikTok mới để tiếp tục.")
            os.remove(video_path)
            user_data_store.pop(user_id, None)
        except Exception as e:
            await msg.edit_text(f"❌ Lỗi đăng FB: {str(e)}")
        context.user_data.clear()

    elif "tiktok.com" in text:
        msg = await update.message.reply_text("⬇️ Đang tải video TikTok...")
        try:
            video_path = await download_tiktok_video(text)
            if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
                raise Exception("Tải video thất bại")

            user_data_store[user_id] = {"video_path": video_path}

            keyboard = [
                [InlineKeyboardButton("📥 Chỉ tải về Telegram", callback_data="only_download")],
                [InlineKeyboardButton("📤 Tải + Đăng lên Facebook", callback_data="post_facebook")]
            ]
            with open(video_path, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    caption="✅ Video TikTok - Không logo\n\nBạn muốn làm gì tiếp theo?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    supports_streaming=True
                )
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Lỗi: {str(e)}")
    else:
        await update.message.reply_text("👋 Gửi link TikTok để bắt đầu!")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "only_download":
        await query.edit_message_caption("✅ Xong! Gửi link TikTok khác nếu muốn tải thêm.")
    elif query.data == "post_facebook":
        await query.edit_message_caption("✏️ Nhập caption cho bài đăng Facebook:")
        context.user_data["waiting"] = "caption"

# ---------------- MAIN ----------------
def main():
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()
    print("✅ Web server started!")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
