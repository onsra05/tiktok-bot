import os
import subprocess
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "tiktok.com" not in url and "vm.tiktok.com" not in url:
        await update.message.reply_text("❌ Vui lòng gửi link TikTok hợp lệ!")
        return

    msg = await update.message.reply_text("⏳ Đang tải video, chờ chút...")

    try:
        output_template = os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "--no-warnings",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--add-header", "User-Agent:TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet",
            "-o", output_template,
            url
        ]

        subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        video_file = None
        for f in os.listdir(DOWNLOAD_DIR):
            full_path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(full_path):
                video_file = full_path
                break

        if not video_file:
            raise Exception("Không tìm thấy file sau khi tải")

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
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
