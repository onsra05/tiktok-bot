import os
import glob
import subprocess
import threading
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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

async def get_tiktok_video_url(url: str) -> str:
    """Dùng API tikhub để lấy link video không watermark"""
    api_url = f"https://api.tiklydown.eu.org/api/download?url={url}"
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(api_url)
        data = response.json()
    
    print("API response:", data)
    
    # Lấy link video không watermark
    video_url = data.get("video", {}).get("noWatermark") or \
                data.get("video", {}).get("origin") or \
                data.get("video", {}).get("watermark")
    
    if not video_url:
        raise Exception("Không lấy được link video từ API")
    
    return video_url

async def download_video(video_url: str) -> str:
    """Download video từ direct link"""
    output_path = os.path.join(DOWNLOAD_DIR, "video.mp4")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)",
        "Referer": "https://www.tiktok.com/"
    }
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(video_url, headers=headers)
        with open(output_path, "wb") as f:
            f.write(response.content)
    
    return output_path

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "tiktok.com" not in url and "vm.tiktok.com" not in url:
        await update.message.reply_text("❌ Vui lòng gửi link TikTok hợp lệ!")
        return

    msg = await update.message.reply_text("⏳ Đang tải video, chờ chút...")

    try:
        await msg.edit_text("🔍 Đang lấy link video...")
        video_url = await get_tiktok_video_url(url)
        
        await msg.edit_text("⬇️ Đang tải video...")
        video_path = await download_video(video_url)
        
        file_size = os.path.getsize(video_path)
        print(f"File size: {file_size} bytes")
        
        if file_size < 1000:
            raise Exception("File tải về quá nhỏ, có thể bị lỗi")

        await msg.edit_text("📤 Đang gửi video...")
        with open(video_path, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption="✅ Video TikTok HD - Không logo",
                supports_streaming=True
            )

        await msg.delete()
        os.remove(video_path)

    except Exception as e:
        await msg.edit_text(f"❌ Lỗi: {str(e)}")

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
