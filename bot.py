import os
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

import yt_dlp
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------- WEB SERVER (Render keep alive) ----------------
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    server.serve_forever()

# ---------------- DOWNLOAD WITH YT-DLP ----------------
def download_sync(url: str, output_path: str):
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'mp4',
        'quiet': True,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0'
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def download_tiktok_video(url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "video.mp4")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_sync, url, output_path)

    return output_path

# ---------------- HANDLE MESSAGE ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "tiktok.com" not in url:
        await update.message.reply_text("❌ Link không hợp lệ!")
        return

    msg = await update.message.reply_text("⏳ Đang xử lý...")

    try:
        # Download video
        await msg.edit_text("⬇️ Đang tải video...")
        video_path = await download_tiktok_video(url)

        # Check file
        if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
            raise Exception("File lỗi hoặc không tải được")

        # Send video
        await msg.edit_text("📤 Đang gửi video...")
        with open(video_path, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption="✅ Video TikTok - Không logo",
                supports_streaming=True
            )

        await msg.delete()
        os.remove(video_path)

    except Exception as e:
        await msg.edit_text(f"❌ Lỗi: {str(e)}")

# ---------------- MAIN ----------------
def main():
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()
    print("✅ Web server started!")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
