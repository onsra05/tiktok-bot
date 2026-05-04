import os
import glob
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/downloads"  # Dùng /tmp thay vì thư mục local
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "tiktok.com" not in url and "vm.tiktok.com" not in url:
        await update.message.reply_text("❌ Vui lòng gửi link TikTok hợp lệ!")
        return

    msg = await update.message.reply_text("⏳ Đang tải video, chờ chút...")

    try:
        # Xóa file cũ trước
        for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
            os.remove(f)

        output_template = os.path.join(DOWNLOAD_DIR, "video.%(ext)s")

        cmd = [
            "yt-dlp",
            "--no-warnings",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--add-header", "User-Agent:TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet",
            "-o", output_template,
            url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        # Log để debug
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)

        # Tìm file mp4
        files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4"))
        
        if not files:
            # Thử tìm bất kỳ file nào
            files = glob.glob(os.path.join(DOWNLOAD_DIR, "*"))

        if not files:
            raise Exception(f"Không tìm thấy file. STDERR: {result.stderr[:200]}")

        video_file = files[0]
        print("Found file:", video_file)

        await msg.edit_text("📤 Đang gửi video...")
        with open(video_file, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption="✅ Video TikTok HD - Không logo",
                supports_streaming=True
            )

        await msg.delete()
        os.remove(video_file)

    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ Timeout! Link này mất quá lâu để tải.")
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
