import os
import re
import logging
import requests
import threading
import asyncio
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ CONFIG ============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
SHOPEE_AFL_ID = os.environ.get("SHOPEE_AFL_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
SHOPEE_URL = os.environ.get("SHOPEE_URL", "https://shopee.vn")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DOWNLOAD_DIR = "/tmp/downloads"
MAX_TG_SIZE = 50 * 1024 * 1024
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_data_store = {}

# ============ WEB SERVER ============
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(base_dir, "index.html")
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
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

# ============ DATABASE ============
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            username TEXT,
            last_command_time TIMESTAMP DEFAULT NOW(),
            command_count INT DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS followed_products (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            product_url TEXT,
            product_name TEXT,
            last_price FLOAT,
            last_notified TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Database initialized")

# ============ RATE LIMIT ============
def check_rate_limit(user_id: int, username: str) -> bool:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        now = datetime.now()

        if not user:
            cur.execute(
                "INSERT INTO users (id, username, last_command_time, command_count) VALUES (%s, %s, %s, 1)",
                (user_id, username, now)
            )
            conn.commit()
            cur.close()
            conn.close()
            return True

        if now - user['last_command_time'] > timedelta(minutes=1):
            cur.execute(
                "UPDATE users SET last_command_time=%s, command_count=1, username=%s WHERE id=%s",
                (now, username, user_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            return True

        if user['command_count'] >= 10:
            cur.close()
            conn.close()
            return False

        cur.execute(
            "UPDATE users SET command_count=command_count+1 WHERE id=%s",
            (user_id,)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Rate limit error: {e}")
        return True

# ============ SHOPEE FUNCTIONS ============
def make_affiliate_link(product_url: str) -> str:
    """Tạo link affiliate từ link sản phẩm Shopee"""
    try:
        # Lấy item_id và shop_id từ URL
        match = re.search(r'i\.(\d+)\.(\d+)', product_url)
        if match:
            shop_id = match.group(1)
            item_id = match.group(2)
            return f"https://s.shopee.vn/redirect?aff_id={SHOPEE_AFL_ID}&url=https://shopee.vn/product/{shop_id}/{item_id}"
        # Nếu không parse được → gắn trực tiếp
        sep = "&" if "?" in product_url else "?"
        return f"{product_url}{sep}aff_id={SHOPEE_AFL_ID}"
    except:
        return product_url

def search_shopee(keyword: str) -> list:
    """Tìm sản phẩm Shopee qua API không chính thức"""
    try:
        url = f"https://shopee.vn/api/v4/search/search_items"
        params = {
            "by": "relevancy",
            "keyword": keyword,
            "limit": 5,
            "newest": 0,
            "order": "desc",
            "page_type": "search",
            "scenario": "PAGE_GLOBAL_SEARCH",
            "version": 2
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://shopee.vn/",
            "X-API-SOURCE": "pc",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        items = data.get("items", [])
        results = []
        for item in items[:5]:
            info = item.get("item_basic", {})
            name = info.get("name", "")
            price = info.get("price", 0) / 100000
            original_price = info.get("price_before_discount", 0) / 100000
            shop_id = info.get("shopid", "")
            item_id = info.get("itemid", "")
            stock = info.get("stock", 0)
            sold = info.get("historical_sold", 0)
            rating = info.get("item_rating", {}).get("rating_star", 0)
            discount = info.get("discount", "")

            product_url = f"https://shopee.vn/product/{shop_id}/{item_id}"
            afl_link = make_affiliate_link(product_url)

            results.append({
                "name": name,
                "price": price,
                "original_price": original_price,
                "url": afl_link,
                "stock": stock,
                "sold": sold,
                "rating": round(rating, 1),
                "discount": discount
            })
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def get_top_deals() -> list:
    """Lấy top deals từ Shopee flash sale"""
    try:
        url = "https://shopee.vn/api/v2/flash_sale/get_all_sessions"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://shopee.vn/",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        sessions = resp.json().get("data", {}).get("sessions", [])
        if not sessions:
            return []

        session_id = sessions[0].get("promotionid")
        items_url = f"https://shopee.vn/api/v2/flash_sale/get_items_by_promotionid"
        params = {"promotionid": session_id, "limit": 5, "offset": 0}
        items_resp = requests.get(items_url, params=params, headers=headers, timeout=10)
        items = items_resp.json().get("data", {}).get("items", [])

        results = []
        for item in items[:5]:
            name = item.get("name", "")
            price = item.get("price", 0) / 100000
            original_price = item.get("price_before_discount", 0) / 100000
            shop_id = item.get("shopid", "")
            item_id = item.get("itemid", "")
            sold = item.get("flash_sale_stock", 0)
            discount = item.get("discount", "")

            product_url = f"https://shopee.vn/product/{shop_id}/{item_id}"
            afl_link = make_affiliate_link(product_url)

            results.append({
                "name": name,
                "price": price,
                "original_price": original_price,
                "url": afl_link,
                "sold": sold,
                "discount": discount
            })
        return results
    except Exception as e:
        logger.error(f"Deal error: {e}")
        return []

def get_product_price(product_url: str) -> float:
    """Lấy giá hiện tại của sản phẩm"""
    try:
        match = re.search(r'product/(\d+)/(\d+)', product_url)
        if not match:
            return 0
        shop_id = match.group(1)
        item_id = match.group(2)
        url = f"https://shopee.vn/api/v4/item/get?itemid={item_id}&shopid={shop_id}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://shopee.vn/"}
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        price = data.get("data", {}).get("price", 0) / 100000
        return price
    except Exception as e:
        logger.error(f"Price check error: {e}")
        return 0

def format_price(price: float) -> str:
    return f"₫{price:,.0f}".replace(",", ".")

def format_product(p: dict, index: int = None) -> str:
    idx = f"{index}. " if index else ""
    discount_text = f"🏷 Giảm {p['discount']}%" if p.get('discount') else ""
    original = f"~~{format_price(p['original_price'])}~~" if p.get('original_price') and p['original_price'] > p['price'] else ""
    rating = f"⭐ {p.get('rating', 0)}" if p.get('rating') else ""
    sold = f"🛍 {p.get('sold', 0):,} đã bán" if p.get('sold') else ""

    return (
        f"{idx}🔥 *{p['name'][:60]}*\n"
        f"💰 *{format_price(p['price'])}* {original}\n"
        f"{discount_text} {rating} {sold}\n"
    )

# ============ TIKTOK FUNCTIONS ============
def download_sync(url: str, output_path: str):
    import subprocess
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True)
    except:
        pass

    # Thử tikwm trước
    try:
        api_resp = requests.post(
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
            r = requests.get(video_url, stream=True, timeout=60, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.tiktok.com/"
            })
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return
    except Exception as e:
        logger.error(f"tikwm error: {e}")

    # Fallback yt-dlp
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'quiet': True,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'com.zhiliaoapp.musically/2022600030 (Linux; U; Android 11)',
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def download_tiktok_video(url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "video.mp4")
    if os.path.exists(output_path):
        os.remove(output_path)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_sync, url, output_path)
    return output_path

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
    if "id" in result:
        return True
    raise Exception(result.get("error", {}).get("message", "Lỗi không xác định"))

def format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024*1024):.1f} MB"

# ============ TELEGRAM HANDLERS ============

# --- ADMIN ONLY: TikTok ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting")

    # Chỉ admin mới dùng được TikTok
    if user_id != ADMIN_ID:
        if "tiktok.com" in text:
            await update.message.reply_text("❌ Tính năng này chỉ dành cho admin!")
            return
        await update.message.reply_text("👋 Dùng /search hoặc /deal để tìm sản phẩm Shopee!")
        return

    # Đang chờ caption FB
    if waiting == "caption":
        context.user_data["caption"] = text
        context.user_data["waiting"] = "shopee_link"
        await update.message.reply_text("🛒 Nhập link Shopee cho bài đăng FB:")
        return

    # Đang chờ link Shopee để đăng FB
    if waiting == "shopee_link":
        shopee_link = text
        caption = context.user_data.get("caption", "")
        video_path = user_data_store.get(user_id, {}).get("video_path")

        if not video_path or not os.path.exists(video_path):
            await update.message.reply_text("❌ Video không còn, gửi lại link TikTok!")
            context.user_data.clear()
            return

        msg = await update.message.reply_text("📤 Đang đăng lên Facebook...")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, post_to_facebook, video_path, caption, shopee_link)
            await msg.edit_text("✅ Đăng Facebook thành công!\n\nGửi link TikTok mới để tiếp tục.")
            if os.path.exists(video_path):
                os.remove(video_path)
            user_data_store.pop(user_id, None)
        except Exception as e:
            await msg.edit_text(f"❌ Lỗi đăng FB: {str(e)}")
        context.user_data.clear()
        return

    # Nhận link TikTok
    if "tiktok.com" in text:
        msg = await update.message.reply_text("⬇️ Đang tải video TikTok...")
        try:
            video_path = await download_tiktok_video(text)
            if not os.path.exists(video_path):
                raise Exception("Không tải được video!")
            file_size = os.path.getsize(video_path)
            if file_size < 1000:
                raise Exception("Video lỗi (file quá nhỏ)!")

            size_text = format_size(file_size)
            user_data_store[user_id] = {"video_path": video_path}

            if file_size > MAX_TG_SIZE:
                await msg.edit_text(
                    f"📦 Video {size_text} > 50MB\n"
                    f"📤 Sẽ đăng thẳng lên Facebook\n"
                    f"✏️ Nhập caption:"
                )
                context.user_data["waiting"] = "caption"
            else:
                await msg.delete()
                keyboard = [
                    [InlineKeyboardButton("📥 Tải về Telegram", callback_data="only_download")],
                    [InlineKeyboardButton("📤 Đăng lên Facebook", callback_data="post_facebook")]
                ]
                with open(video_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=f"✅ Video TikTok\n📦 {size_text}\n\nBạn muốn làm gì?",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        supports_streaming=True
                    )
        except Exception as e:
            await msg.edit_text(f"❌ Lỗi: {str(e)}")
            if os.path.exists(os.path.join(DOWNLOAD_DIR, "video.mp4")):
                os.remove(os.path.join(DOWNLOAD_DIR, "video.mp4"))
        return

    await update.message.reply_text("👋 Gửi /search <tên SP> hoặc /deal để xem deal!")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "only_download":
        await query.edit_message_caption(caption="✅ Xong! Gửi link TikTok khác nếu muốn.")
        video_path = user_data_store.get(user_id, {}).get("video_path")
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
        user_data_store.pop(user_id, None)

    elif query.data == "post_facebook":
        await query.edit_message_caption(caption="✏️ Nhập caption cho bài đăng Facebook:")
        context.user_data["waiting"] = "caption"

# --- SHOPEE COMMANDS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    is_admin = user_id == ADMIN_ID

    admin_text = "\n🎬 Gửi link TikTok để tải video\n📤 Tải xong chọn đăng FB hoặc lưu về" if is_admin else ""

    await update.message.reply_text(
        f"👋 Chào *{name}*! Mình là DealBot 🛒\n\n"
        f"*Lệnh có thể dùng:*\n"
        f"🔍 /search <tên sản phẩm> - Tìm sản phẩm\n"
        f"🔥 /deal - Top deal flash sale hôm nay\n"
        f"👁 /follow <link Shopee> - Theo dõi giá\n"
        f"📋 /following - Xem danh sách đang theo dõi\n"
        f"❌ /unfollow <số thứ tự> - Bỏ theo dõi\n"
        f"{admin_text}\n\n"
        f"⚡ Giới hạn: 10 lệnh/phút",
        parse_mode="Markdown"
    )

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""

    if not check_rate_limit(user_id, username):
        await update.message.reply_text("⚠️ Bạn gửi quá nhiều lệnh! Chờ 1 phút nhé.")
        return

    if not context.args:
        await update.message.reply_text("❌ Cú pháp: /search <tên sản phẩm>\nVD: /search tai nghe bluetooth")
        return

    keyword = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Đang tìm *{keyword}*...", parse_mode="Markdown")

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, search_shopee, keyword)

        if not results:
            await msg.edit_text("❌ Không tìm thấy sản phẩm nào!")
            return

        text = f"🛒 *Kết quả tìm kiếm: {keyword}*\n\n"
        keyboard = []

        for i, p in enumerate(results, 1):
            text += format_product(p, i)
            keyboard.append([InlineKeyboardButton(
                f"🛒 {i}. {p['name'][:30]}... - {format_price(p['price'])}",
                url=p['url']
            )])

        text += "\n💡 Click vào nút bên dưới để mua ngay!"
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Search error: {e}")
        await msg.edit_text("❌ Lỗi tìm kiếm, thử lại sau!")

async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""

    if not check_rate_limit(user_id, username):
        await update.message.reply_text("⚠️ Quá nhiều lệnh! Chờ 1 phút nhé.")
        return

    msg = await update.message.reply_text("🔥 Đang lấy top deal hôm nay...")

    try:
        loop = asyncio.get_event_loop()
        deals = await loop.run_in_executor(None, get_top_deals)

        if not deals:
            await msg.edit_text("❌ Không lấy được deal lúc này, thử lại sau!")
            return

        text = "⚡ *TOP FLASH SALE HÔM NAY* ⚡\n\n"
        keyboard = []

        for i, p in enumerate(deals, 1):
            text += format_product(p, i)
            keyboard.append([InlineKeyboardButton(
                f"⚡ {i}. Mua ngay - {format_price(p['price'])}",
                url=p['url']
            )])

        text += "\n🏃 Flash sale có hạn, mua nhanh kẻo hết!"
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Deal error: {e}")
        await msg.edit_text("❌ Lỗi lấy deal, thử lại sau!")

async def cmd_follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""

    if not check_rate_limit(user_id, username):
        await update.message.reply_text("⚠️ Quá nhiều lệnh! Chờ 1 phút nhé.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Cú pháp: /follow <link Shopee>\n"
            "VD: /follow https://shopee.vn/product/123/456"
        )
        return

    product_url = context.args[0]
    if "shopee.vn" not in product_url:
        await update.message.reply_text("❌ Link không hợp lệ! Cần link từ shopee.vn")
        return

    msg = await update.message.reply_text("🔍 Đang lấy thông tin sản phẩm...")

    try:
        loop = asyncio.get_event_loop()
        price = await loop.run_in_executor(None, get_product_price, product_url)

        if price <= 0:
            await msg.edit_text("❌ Không lấy được giá, thử link khác!")
            return

        conn = get_db()
        cur = conn.cursor()

        # Kiểm tra đã follow chưa
        cur.execute(
            "SELECT id FROM followed_products WHERE user_id=%s AND product_url=%s",
            (user_id, product_url)
        )
        if cur.fetchone():
            await msg.edit_text("⚠️ Bạn đã theo dõi sản phẩm này rồi!")
            cur.close()
            conn.close()
            return

        cur.execute(
            """INSERT INTO followed_products 
               (user_id, product_url, product_name, last_price, last_notified)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, product_url, "Sản phẩm Shopee", price, datetime.now())
        )
        conn.commit()
        cur.close()
        conn.close()

        await msg.edit_text(
            f"✅ *Đã theo dõi sản phẩm!*\n\n"
            f"💰 Giá hiện tại: *{format_price(price)}*\n"
            f"🔔 Bot sẽ thông báo khi giá giảm!\n"
            f"📋 Xem danh sách: /following",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Follow error: {e}")
        await msg.edit_text("❌ Lỗi theo dõi, thử lại sau!")

async def cmd_following(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM followed_products WHERE user_id=%s ORDER BY id",
            (user_id,)
        )
        products = cur.fetchall()
        cur.close()
        conn.close()

        if not products:
            await update.message.reply_text(
                "📋 Bạn chưa theo dõi sản phẩm nào!\n"
                "Dùng /follow <link Shopee> để theo dõi."
            )
            return

        text = "📋 *Danh sách đang theo dõi:*\n\n"
        for i, p in enumerate(products, 1):
            text += (
                f"{i}. {p['product_name'][:40]}\n"
                f"   💰 Giá hiện tại: {format_price(p['last_price'])}\n"
                f"   🔗 {p['product_url'][:40]}...\n\n"
            )
        text += "❌ Dùng /unfollow <số thứ tự> để bỏ theo dõi"

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Following error: {e}")
        await update.message.reply_text("❌ Lỗi, thử lại sau!")

async def cmd_unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Cú pháp: /unfollow <số thứ tự>\nXem danh sách: /following")
        return

    index = int(context.args[0])

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM followed_products WHERE user_id=%s ORDER BY id",
            (user_id,)
        )
        products = cur.fetchall()

        if index < 1 or index > len(products):
            await update.message.reply_text(f"❌ Số thứ tự không hợp lệ! (1-{len(products)})")
            cur.close()
            conn.close()
            return

        product_id = products[index-1]['id']
        cur.execute("DELETE FROM followed_products WHERE id=%s", (product_id,))
        conn.commit()
        cur.close()
        conn.close()

        await update.message.reply_text(f"✅ Đã bỏ theo dõi sản phẩm #{index}!")

    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        await update.message.reply_text("❌ Lỗi, thử lại sau!")

# ============ PRICE CHECK SCHEDULER ============
async def check_prices(app):
    """Kiểm tra giá định kỳ và thông báo khi giảm"""
    logger.info("🔍 Checking prices...")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM followed_products")
        products = cur.fetchall()
        cur.close()
        conn.close()

        for p in products:
            try:
                new_price = get_product_price(p['product_url'])
                if new_price <= 0:
                    continue

                # Giá giảm → thông báo
                if new_price < p['last_price']:
                    drop_pct = ((p['last_price'] - new_price) / p['last_price']) * 100
                    afl_link = make_affiliate_link(p['product_url'])

                    keyboard = [[InlineKeyboardButton("🛒 Mua ngay!", url=afl_link)]]
                    await app.bot.send_message(
                        chat_id=p['user_id'],
                        text=(
                            f"🔔 *GIÁ GIẢM!*\n\n"
                            f"📦 {p['product_name'][:50]}\n"
                            f"💰 Giá cũ: ~~{format_price(p['last_price'])}~~\n"
                            f"🔥 Giá mới: *{format_price(new_price)}*\n"
                            f"📉 Giảm: *{drop_pct:.1f}%*\n\n"
                            f"⏰ Mua nhanh kẻo hết!"
                        ),
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )

                    # Cập nhật giá mới
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE followed_products SET last_price=%s, last_notified=%s WHERE id=%s",
                        (new_price, datetime.now(), p['id'])
                    )
                    conn.commit()
                    cur.close()
                    conn.close()

            except Exception as e:
                logger.error(f"Price check error for {p['product_url']}: {e}")

    except Exception as e:
        logger.error(f"Scheduler error: {e}")

# ============ MAIN ============
def main():
    # Web server
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()
    print("✅ Web server started!")

    # Init DB
    if DATABASE_URL:
        init_db()

    # Bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("deal", cmd_deal))
    app.add_handler(CommandHandler("follow", cmd_follow))
    app.add_handler(CommandHandler("following", cmd_following))
    app.add_handler(CommandHandler("unfollow", cmd_unfollow))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Scheduler kiểm tra giá mỗi 15 phút
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_prices,
        'interval',
        minutes=15,
        args=[app]
    )
    scheduler.start()

    print("✅ Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
