import os
import threading
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------- WEB SERVER (để giữ Render sống) ----------------
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

# ---------------- EXPAND LINK TIKTOK ----------------
async def expand_tiktok_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            res = await client.get(url)
            if "location" in res.headers:
                return res.headers["location"]
            return url
    except:
        return url

# ---------------- API CHÍNH ----------------
async def get_tiktok_video_url(url: str) -> str:
    api_url = f"https://api.tiklydown.eu.org/api/download?url={url}"
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(api_url)
        data = response.json()
    
    print("API 1 response:", data)

    video_url = data.get("video", {}).get("noWatermark") or \
                data.get("video", {}).get("origin") or \
                data.get("video", {}).get("watermark")

    if not video_url:
        raise Exception("API 1 không trả video")

    return video_url

# ---------------- API BACKUP ----------------
async def get_video_backup(url: str) -> str:
    api_url = f"https://tikwm.com/api/?url={url}"
    
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(api_url)
        data = res.json()

    print("API 2 response:", data)

    video_url = data.get("data", {}).get("play")

    if not video_url:
        raise Exception("API backup cũng lỗi")

    return video_url

# ---------------- DOWNLOAD VIDEO ----------------
async def download_video(video_url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "video.mp4")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.tiktok.com/"
    }

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(video_url, headers=headers)

        with open(output_path, "wb") as f:
            f.write(response.content)

    return output_path

# ---------------- HANDLE MESSAGE ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "tiktok.com" not in url:
        await update.message.reply_text("❌ Link không hợp lệ!")
        return

    msg = await update.message.reply_text("⏳ Đang xử lý...")

    try:
        # 1. Expand link
        await msg.edit_text("🔗 Đang xử lý link...")
        expanded_url = await expand_tiktok_url(url)
        print("Expanded:", expanded_url)

        # 2. Lấy video (có backup)
        await msg.edit_text("🔍 Đang lấy video...")

        try:
            video_url = await get_tiktok_video_url(expanded_url)
            print("✅ Dùng API 1")
        except Exception as e:
            print("❌ API 1 lỗi:", e)
            video_url = await get_video_backup(expanded_url)
            print("✅ Dùng API 2")

        # 3. Download
        await msg.edit_text("⬇️ Đang tải video...")
        video_path = await download_video(video_url)

        if os.path.getsize(video_path) < 1000:
            raise Exception("File lỗi hoặc quá nhỏ")

        # 4. Gửi video
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
