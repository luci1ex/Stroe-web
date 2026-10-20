"""
Lucifer Exchange - البوت الأساسي (نقاط، لوحات، بورصة، ترحيب، نشر منتجات)
---------------------------------------------------------------------------
تسجيل التسليم وفتح التكتات انتقلوا لبوت التكت المنفصل. هذا البوت يقرا
نفس Firestore: لما بوت التكت يضيف نقاط لزبون، لوحة النقاط هنا تتحدث
تلقائياً.
"""

import asyncio
import hashlib
import json
import os
import io
import logging
import re

import aiohttp
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore

import welcome as welcome_module
from offers import DETAILS_BUTTON_LABEL, OFFER_FIXED_TEXT, PAYMENT_TEXTS, PRODUCTS
from panel import CustomerLog, F, Panel, ReviewLog, fill, publish_guild_info
from welcome import render_welcome
try:  # اختياري: إذا boostcard.py مو موجود، البوت يشتغل عادي وبس أمر البوست يتعطل
    from boostcard import arabic_duration, boost_status, render_boost_card
except ImportError:
    arabic_duration = boost_status = render_boost_card = None

# ---------------------------------------------------------------------------
# الإعدادات (تُقرأ من ملف .env - شوف .env.example)
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
FIREBASE_CREDENTIALS_PATH = os.environ["FIREBASE_CREDENTIALS_PATH"]
GUILD_ID = int(os.environ["GUILD_ID"])
TEAM_ROLE_ID = int(os.environ["TEAM_ROLE_ID"])
# اختياري - يتفعّل بس لو انحط بملف .env. لو فاضي، البوت يشتغل عادي بدون لوحة نقاط.
_leaderboard_env = os.environ.get("LEADERBOARD_CHANNEL_ID", "").strip()
LEADERBOARD_CHANNEL_ID = int(_leaderboard_env) if _leaderboard_env else None

# اختياري - روم تقييمات الزبائن (لعد التقييمات باستثناء رسائل البوتات)
_reviews_env = os.environ.get("REVIEWS_CHANNEL_ID", "").strip()
REVIEWS_CHANNEL_ID = int(_reviews_env) if _reviews_env else None

# اختياري - روم "لوگ" الإحصائيات (عدد كل رتبة + عدد التقييمات) - يتحدث تلقائياً
_stats_log_env = os.environ.get("STATS_LOG_CHANNEL_ID", "").strip()
STATS_LOG_CHANNEL_ID = int(_stats_log_env) if _stats_log_env else None

# اختياري - روم منشن الأعضاء الجدد (تنبيه التوثيق) - الرسالة تنرسل وتنحذف فوراً
_join_mention_env = os.environ.get("JOIN_MENTION_CHANNEL_ID", "").strip()
JOIN_MENTION_CHANNEL_ID = int(_join_mention_env) if _join_mention_env else None
JOIN_MENTION_TEXT = "لا تنسى توثق نفسك يعسل"  # عدّل النص هنا إذا حبيت تغييره

# رسالة الترحيب الخاصة (DM) اللي توصل لأي عضو جديد ينضم للسيرفر
WELCOME_DM_TEMPLATE = """** مـنـور سـيـرفـر لــوسـيـفـر<:tdll:1393611890818814083>
اكـبـر سـيـرفـر  تـبـديـل عـراقـي<a:22_ayayaya:1548193736306204684>
ومـتـخـصـص بـتـ9فــير 90% من احتياجات كل عميل<:needmony:1392865439520002079>
نـمـتـلـك اكـثـر مـن 1200 عـمـيـل و اكـثـر مـن 3350 تـقـيـيـم <:perfcto:1394077729896267888>
وكـذالـك مـتـ9فـر توريـد اذا عـنـدك سـيـرفـر كـبـيـر <a:extra_13:1392865417781055512>
- لا تـنـسـى تـخـلـي ايـمـوجـي هـنـا  https://discord.com/channels/1392614769147646092/1392843040904843264
- وكذالك ادخل فرع باك اب شي في حال صار شي للسيرفر الاساسي

https://discord.gg/YUmCUdyta**"""

# رسالة الخاص للعميل لما ياخذ نقاط - {points} و {total} ينعوضون تلقائياً
POINTS_DM_TEMPLATE = """**مبروك ولكك<:WTF:1392865722446909494>
حصلت على {points} وصار عندك {total} <:lzez:1393611946011394138>

- اذا تسئل شنو فائده نقاط تقدر تبدلها ب منتجات كل ما زادت نقاطك <:ifff:1393611932149219381>


- كل ما تشتري اكثر تحصل نقاط اكثر <:emoji_130:1548180671481127023>


وديربالك تنسى تقييم ترى ادججك<:die:1393611941511172187>**"""

# ترتيب وتسميات رتب الإحصائية - من الأعلى للأدنى بالضبط زي ما تحب تظهر باللوگ
ROLE_TIERS = [
    {"id": 1392859293434380388, "label": "أعلى رتبة"},
    {"id": 1392859294319378492, "label": "ثالث رتبة"},
    {"id": 1392859297125371994, "label": "ثاني رتبة"},
    {"id": 1392859298484322344, "label": "أول رتبة"},
]
ROLE_TIER_IDS = {tier["id"] for tier in ROLE_TIERS}

# حدود النقاط للترقية التلقائية بالرتبة - من الأدنى للأعلى. "أول رتبة" ماله
# حد أدنى محدد (رتبة أساسية تنعطى بطريقة ثانية غير النقاط).
RANK_THRESHOLDS = [
    {"role_id": 1392859297125371994, "points": 25000},   # ثاني رتبة
    {"role_id": 1392859294319378492, "points": 50000},   # ثالث رتبة
    {"role_id": 1392859293434380388, "points": 100000},  # أعلى رتبة
]

# رسالة الخاص لما عضو يترقى رتبة تلقائياً
RANK_UP_DM_TEMPLATE = """** ولكك شهل مستوى<a:22_ayayaya:1548193736306204684>
مبروك عليك صعدت رتبة استمر بهذا مستوى ومناسبه صعودك رتبة افتح تكت واخذ هديه من يمنا مجانا <a:NONONONONO:1548193880891981844>
وكل ما تصعد رتبة تجيك هدية اقوى لحد ما توصل لاعلى رتبة استمر بمستواك ضلعي <:ok:1392865707368255641> **"""

# اختياري - رتبة نشر المنتجات (أمر -s) - لو فاضي محد يكدر يستخدم الأمر
_product_post_role_env = os.environ.get("PRODUCT_POST_ROLE_ID", "").strip()
PRODUCT_POST_ROLE_ID = int(_product_post_role_env) if _product_post_role_env else None

# ميزة نشر المنتجات: عضو عنده PRODUCT_POST_ROLE_ID يكتب "-s <تفاصيل>" بأي
# روم (مع صورة مرفقة اختياري) وينشر البوت فوراً بنفس الروم باسم وصورة
# العضو نفسه (Webhook)، رسالة عادية مو إمبيد، بدون حذف رسالته الأصلية.
PRODUCT_POST_PREFIX = "-s"
PRODUCT_POST_WEBHOOK_NAME = "Lucifer Poster"
PRODUCT_POST_FOOTER = """** للـطـلـب افــتــح <#1392845069278646283> وتــدلـل عـلـيـنـا<:tdll:1393611890818814083> <a:perfecto:1392865770207445148> **"""

# أونرات أوامر السستم (خط، !adde، !adds، بان، طرد، -send، -say، -s) - آيديات
# مفصولة بفاصلة بملف .env. صاحب السيرفر يعتبر أونر دائماً.
OWNER_IDS = {
    int(x) for x in os.environ.get("OWNER_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

# أسماء الأوامر النصية - تتعدل من الموقع. المفتاح (يسار) اسم داخلي ثابت.
COMMAND_NAMES = {
    "line": ["خط"],
    "adde": ["!adde"],
    "adds": ["!adds"],
    "ban": ["بان", "ban"],
    "kick": ["طرد", "kick"],
    "send": ["-send"],
    "say": ["-say"],
    "s": ["-s"],
    "welcome": ["!welcome"],
}
# أمر صورة شارة البوست - لكل الأعضاء (مو بس الأونرات)
BOOST_COMMANDS: list[str] = []  # متوقف: بوت الشارات (Badges bot) هو اللي يرد على =boost
BOOST_NEXT_TEXT = "توصل للشارة الجاية بعد {time}"
BOOST_MAX_TEXT = "وصلت لأعلى شارة بوست"
BOOST_NONE_TEXT = "**ما عندك بوست بهذا السيرفر**"
BOOST_NONE_OTHER_TEXT = "**{user} ما عنده بوست بهذا السيرفر**"
MOD_ROLE_IDS: set[int] = set()  # رتب تكدر تستخدم بان/طرد غير الأونرات (فارغ = الأونرات بس)
BAN_REPLY_TEMPLATE = "✅ تم تبنيد {user} — السبب: {reason}"
KICK_REPLY_TEMPLATE = "✅ تم طرد {user} — السبب: {reason}"
NO_REASON_TEXT = "بدون سبب"

# سجل الزبائن (يطلع بالموقع): أي عضو عنده وحدة من هذي الرتب ينحفظ آيديه ويوزره.
# الترتيب من الأعلى للأدنى.
CUSTOMER_ROLE_IDS = [
    1392859293434380388,  # شيخ معاميل
    1392859294319378492,  # معميل ثكيل
    1392859297125371994,  # معميل معدل
    1392859298484322344,  # معميل
]
RESTORE_ROLES_ON_REJOIN = True  # إذا زبون طلع ورجع دخل ترجعله رتبه
SERVER_KEY = "lucifer"  # اسم السيرفر بالموقع - لا تغيره

LINE_FILENAME = "line.png"  # الخط الفاصل - بنفس مجلد main.py
OFFER_IMAGES_DIR = "images"  # صور رسائل العروض

# روم التقييمات: البوت يتفاعل بهذي الإيموجيات على كل تقييم جديد، ويحط خط بعده
REVIEW_REACTIONS = ["<:tdll:1393611890818814083>", "<:emoji_175:1555175342975619082>"]
REVIEW_AUTO_LINE = True

# روم صورة الترحيب (منشن العضو + صورته بالدائرة واسمه بالمستطيل)
WELCOME_CHANNEL_ID = int(os.environ.get("WELCOME_CHANNEL_ID", "1521890012587753664"))

# رتبة الطاقم (نفس رتبة بوت التكت) - هي بس تكدر تستخدم كلمات الدفع (راجحي، برق...)
STAFF_ROLE_ID = int(os.environ.get("STAFF_ROLE_ID", "1392859276913152040"))

# رومات يكدر بيها أي عضو يطلع طرق الدفع (مو بس الطاقم)
PUBLIC_PAYMENT_CHANNEL_IDS: list[int] = [1399842796512346112]

# بورصة تبديل العملات - عدّل الإيموجيات هنا إذا تغيرت بسيرفرك
CURRENCIES = {
    "asia": {
        "label": "اسيا",
        "emoji": "<:Asiacell:1394078738077057044><:zain:1394078810718343329>",
        "icon": "<:Asiacell:1394078738077057044>",
    },
    "mc": {
        "label": "ماستر كارد",
        "emoji": "<:MC:1394078773032517633>",
        "icon": "<:MC:1394078773032517633>",
    },
    "riyal": {
        "label": "ريال سعودي",
        "emoji": "<:ryal:1549670006885715968>",
        "icon": "<:ryal:1549670006885715968>",
    },
    "egp": {
        "label": "جنيه مصري",
        "emoji": "<:vd:1393611853858476114>",
        "icon": "<:vd:1393611853858476114>",
    },
    "usd": {
        "label": "دولار",
        "emoji": "<:usdt:1392865383723171921>",
        "icon": "<:usdt:1392865383723171921>",
    },
}
USDT_EMOJI = "<:usdt:1392865383723171921>"
CURRENCY_CHOICES = [
    app_commands.Choice(name=info["label"], value=key) for key, info in CURRENCIES.items()
]
# الدولار مقياس ثابت (1 = 1) وما يحتاج تحديث يدوي - مستثنى من أمر التحديث
UPDATABLE_CURRENCY_CHOICES = [
    app_commands.Choice(name=info["label"], value=key)
    for key, info in CURRENCIES.items()
    if key != "usd"
]

# اختياري - روم البورصة المقفل (لو محدد، /exchange و/exchange_rates يشتغلون بس فيه)
_exchange_channel_env = os.environ.get("EXCHANGE_CHANNEL_ID", "").strip()
EXCHANGE_CHANNEL_ID = int(_exchange_channel_env) if _exchange_channel_env else None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lucifer-bot")

# ---------------------------------------------------------------------------
# تهيئة Firebase - نفس مشروع lucifer-server-log اللي يستخدمه موقع الدفتر
# ---------------------------------------------------------------------------
cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------------------------------------------------------------------------
# إعداد البوت
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True  # لازم عشان نتحقق من رتبة العضو - فعّلها بالـ Developer Portal بعد
intents.message_content = True  # لازم عشان نقرأ نص أمر "-s" - فعّلها بالـ Developer Portal بعد

bot = commands.Bot(command_prefix="!", intents=intents)

BRAND_COLOR = discord.Color(0xE6D724)  # نفس الأصفر الذهبي المستخدم ببانر Lucifer Exchange
BANNER_FILENAME = "banner.jpg"  # لازم يكون هذا الملف بنفس مجلد main.py بالاستضافة


def has_team_role(member: discord.Member) -> bool:
    """يتحقق أن العضو يملك رتبة التيم أو صلاحية أدمن."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    return any(role.id == TEAM_ROLE_ID for role in member.roles)


async def check_rank_promotion(member: discord.Member, total_points: int):
    """يترقي العضو تلقائياً لأعلى رتبة تستحقها نقاطه الحالية (رتبة وحدة بس
    بأي وقت - يشيل أي رتبة ثانية من رتب النظام لما يترقى)، ويرسله رسالة
    تهنئة بالخاص. يحتاج صلاحية Manage Roles، ورتبة البوت لازم تكون فوق
    هذي الرتب الأربعة بترتيب رتب السيرفر."""
    target = None
    for tier in RANK_THRESHOLDS:
        if total_points >= tier["points"]:
            target = tier

    if target is None:
        return
    if any(r.id == target["role_id"] for r in member.roles):
        return  # عنده الرتبة الهدف أصلاً

    target_role = member.guild.get_role(target["role_id"])
    if target_role is None:
        return

    roles_to_remove = [
        r for r in member.roles if r.id in ROLE_TIER_IDS and r.id != target["role_id"]
    ]

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="ترقية رتبة تلقائية حسب النقاط")
        await member.add_roles(target_role, reason="ترقية رتبة تلقائية حسب النقاط")
    except discord.Forbidden:
        log.warning(
            f"ماكدرت أعدل رتب العضو {member.id} - تحقق من صلاحية Manage Roles "
            "وإن رتبة البوت أعلى من رتب التسلسل بترتيب رتب السيرفر."
        )
        return

    try:
        await member.send(RANK_UP_DM_TEMPLATE)
    except discord.Forbidden:
        pass


async def get_or_create_post_webhook(channel):
    """يرجع Webhook موجود سويناه احنا بنفس الروم (أو ينشئ وحد جديد لو
    مالكو)، نستخدمه عشان ننشر باسم وصورة العضو نفسه برسالة عادية (مو
    إمبيد). يحتاج صلاحية Manage Webhooks لرتبة البوت بهذا الروم."""
    if not hasattr(channel, "webhooks"):
        return None  # ثريدات وبعض أنواع الرومات ما تدعم Webhooks مباشرة
    try:
        webhooks = await channel.webhooks()
    except (discord.Forbidden, discord.HTTPException):
        return None
    for wh in webhooks:
        if wh.name == PRODUCT_POST_WEBHOOK_NAME:
            return wh
    try:
        return await channel.create_webhook(name=PRODUCT_POST_WEBHOOK_NAME)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def handle_product_post(message: discord.Message):
    """ميزة نشر المنتجات: أمر "-s <تفاصيل>" (مع صورة مرفقة اختياري) من
    عضو عنده رتبة PRODUCT_POST_ROLE_ID - ينشر فوراً بنفس الروم باسم
    وصورة العضو نفسه، بدون مراجعة وبدون حذف رسالته الأصلية."""
    member = message.author
    parts = message.content.strip().split(maxsplit=1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if not body and not message.attachments:
        return  # ماكو نص ولا صورة - تجاهل

    content = f"{body}\n\n{PRODUCT_POST_FOOTER}" if body else PRODUCT_POST_FOOTER

    files = []
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                data = await attachment.read()
                files.append(discord.File(io.BytesIO(data), filename=attachment.filename))
            except discord.HTTPException:
                continue

    webhook = await get_or_create_post_webhook(message.channel)

    try:
        if webhook is not None:
            await webhook.send(
                content=content,
                username=member.display_name,
                avatar_url=member.display_avatar.url,
                files=files if files else discord.utils.MISSING,
            )
        else:
            # ما اكدرنا نسوي Webhook (صلاحيات ناقصة أو نوع روم ما يدعمه) -
            # نرجع لرسالة عادية من البوت مع منشن العضو بدل اسمه وصورته.
            await message.channel.send(
                content=f"**{member.mention}**\n\n{content}",
                files=files if files else discord.utils.MISSING,
            )
    except discord.Forbidden:
        log.warning(
            f"ماكدرت أنشر منشور المنتج بالروم {message.channel.id} - تحقق من صلاحيات "
            "البوت (Manage Webhooks / Send Messages / Attach Files) بهذا الروم."
        )
    except discord.HTTPException as e:
        log.warning(f"فشل نشر منشور المنتج: {e}")


# ---------------------------------------------------------------------------
# أدوات عامة: الخط الفاصل وجلب الرومات
# ---------------------------------------------------------------------------
def line_file() -> discord.File | None:
    if not os.path.isfile(LINE_FILENAME):
        log.warning(f"ملف الخط {LINE_FILENAME} مو موجود بمجلد البوت.")
        return None
    return discord.File(LINE_FILENAME, filename=LINE_FILENAME)


async def send_line(channel):
    file = line_file()
    if file is None:
        return None
    try:
        return await channel.send(file=file)
    except discord.HTTPException as e:
        log.warning(f"ماكدرت أرسل الخط بالروم {getattr(channel, 'id', '?')}: {e}")
        return None


async def get_channel_safe(channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel


# ---------------------------------------------------------------------------
# صورة الترحيب
# ---------------------------------------------------------------------------
async def send_welcome_card(member: discord.Member, channel=None):
    channel = channel or await get_channel_safe(WELCOME_CHANNEL_ID)
    if channel is None:
        log.warning("ماكدرت ألقى روم الترحيب - تحقق من WELCOME_CHANNEL_ID.")
        return
    try:
        avatar = await member.display_avatar.replace(size=512, format="png").read()
    except (discord.HTTPException, ValueError):
        avatar = None
    try:
        png = await asyncio.to_thread(render_welcome, member.display_name, avatar, member.name)
    except Exception as e:
        log.warning(f"فشل رسم صورة الترحيب للعضو {member.id}: {e}")
        return
    try:
        await channel.send(
            content=member.mention,
            file=discord.File(io.BytesIO(png), filename="welcome.png"),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except discord.HTTPException as e:
        log.warning(f"ماكدرت أرسل صورة الترحيب: {e}")


# ---------------------------------------------------------------------------
# رسائل العروض (بيانات المنتجات بملف offers.py)
# ---------------------------------------------------------------------------
OFFERS_META_DOC = db.collection("meta").document("offers")
PRODUCTS_BY_KEY = {p["key"]: p for p in PRODUCTS}
_offer_views_registered = False


def offer_image_path(product: dict) -> str:
    return os.path.join(OFFER_IMAGES_DIR, product["image"])


def offer_embed(product: dict) -> discord.Embed:
    embed = discord.Embed(
        description=f"### {product['title']}\n\n{OFFER_FIXED_TEXT}",
        color=BRAND_COLOR,
    )
    if os.path.isfile(offer_image_path(product)):
        embed.set_image(url=f"attachment://{product['image']}")
    return embed


def offer_files(product: dict) -> list[discord.File]:
    files = []
    line = line_file()
    if line is not None:
        files.append(line)  # الخط يطلع فوق الـ Embed
    path = offer_image_path(product)
    if os.path.isfile(path):
        files.append(discord.File(path, filename=product["image"]))
    else:
        log.warning(f"صورة المنتج {path} مو موجودة.")
    return files


def offer_signature(product: dict) -> str:
    """بصمة لشكل الرسالة (العنوان، النص الثابت، الأزرار، الصور). إذا ما
    تغيرت ما نعدل الرسالة بالـ Restart. نص التفاصيل مو داخلها لأنه يطلع
    بس لما ينضغط الزر."""
    h = hashlib.sha256()
    for part in (product["title"], OFFER_FIXED_TEXT, DETAILS_BUTTON_LABEL):
        h.update(part.encode("utf-8"))
    for label, _ in product.get("extra_buttons", []):
        h.update(label.encode("utf-8"))
    for path in (LINE_FILENAME, offer_image_path(product)):
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:16]


class OfferButton(discord.ui.Button):
    def __init__(self, product_key: str, index: int, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"offer:{product_key}:{index}")
        self.product_key = product_key
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        product = PRODUCTS_BY_KEY.get(self.product_key)
        extras = product.get("extra_buttons", []) if product else []
        if product is None or self.index > len(extras):
            await interaction.response.send_message("❌ هذا العرض انشال أو تغير.", ephemeral=True)
            return
        text = product["details"] if self.index == 0 else extras[self.index - 1][1]
        embed = discord.Embed(description=text, color=BRAND_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class OfferView(discord.ui.View):
    def __init__(self, product: dict):
        super().__init__(timeout=None)
        self.add_item(OfferButton(product["key"], 0, DETAILS_BUTTON_LABEL, discord.ButtonStyle.primary))
        for i, (label, _) in enumerate(product.get("extra_buttons", []), start=1):
            self.add_item(OfferButton(product["key"], i, label, discord.ButtonStyle.secondary))


def register_offer_views():
    """ينادى بالبداية وبعد أي تعديل للمنتجات من الموقع (حتى الأزرار الجديدة تشتغل)."""
    global _offer_views_registered
    for product in PRODUCTS:
        bot.add_view(OfferView(product))
    _offer_views_registered = True


async def delete_offer_messages(entry: dict):
    """يحذف رسالة عرض قديمة (وخطها) - لما منتج ينحذف أو يتغير رومه من الموقع."""
    try:
        channel = await get_channel_safe(int(entry.get("channel_id") or 0))
    except (TypeError, ValueError):
        channel = None
    if channel is None:
        return
    for field in ("message_id", "line_message_id"):
        if not entry.get(field):
            continue
        try:
            message = await channel.fetch_message(int(entry[field]))
            await message.delete()
        except (discord.HTTPException, ValueError):
            pass


_offers_lock = asyncio.Lock()


async def ensure_offers():
    async with _offers_lock:  # حتى ما يشتغل مرتين سوا (Restart + تعديل من الموقع)
        await _ensure_offers()


async def _ensure_offers():
    """يرسل رسالة كل عرض مرة وحدة (الخط + الـ Embed + الأزرار، وبعدها خط).
    بعد كل Restart أو تعديل من الموقع يعدل نفس الرسالة بس إذا تغير شكلها،
    بدون ما يكررها. المنتج المحذوف تنحذف رسالته."""
    meta = OFFERS_META_DOC.get()
    state = meta.to_dict() if meta.exists else {}
    changed = False
    problems = []

    for key in [k for k in state if k not in PRODUCTS_BY_KEY]:
        await delete_offer_messages(state.pop(key) or {})
        changed = True

    for product in PRODUCTS:
        key = product["key"]
        channel = await get_channel_safe(product["channel"])
        if channel is None:
            log.warning(f"ماكدرت ألقى روم عرض {key} ({product['channel']}).")
            problems.append(f"ما لگيت روم المنتج «{key}»")
            continue

        signature = offer_signature(product)
        entry = state.get(key) or {}
        message = None
        if entry.get("message_id") and entry.get("channel_id") != str(channel.id):
            await delete_offer_messages(entry)  # الروم تغير - نحذف القديمة ونرسل بالجديد
            entry = {}
        if entry.get("channel_id") == str(channel.id) and entry.get("message_id"):
            try:
                message = await channel.fetch_message(int(entry["message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                message = None

        try:
            if message is not None:
                if entry.get("signature") != signature:
                    await message.edit(
                        embed=offer_embed(product), attachments=offer_files(product), view=OfferView(product)
                    )
                    entry["signature"] = signature
                    state[key] = entry
                    changed = True
                continue

            files = offer_files(product)
            kwargs = {"embed": offer_embed(product), "view": OfferView(product)}
            if files:
                kwargs["files"] = files
            message = await channel.send(**kwargs)
            line_message = await send_line(channel)
            state[key] = {
                "channel_id": str(channel.id),
                "message_id": str(message.id),
                "line_message_id": str(line_message.id) if line_message else None,
                "signature": signature,
            }
            changed = True
        except discord.Forbidden:
            log.warning(f"ماكدرت أرسل/أعدل عرض {key} - البوت يحتاج Send Messages + Attach Files + Embed Links بالروم.")
            problems.append(f"ماكو صلاحية أرسل بروم المنتج «{key}»")
        except discord.HTTPException as e:
            log.warning(f"فشل إرسال عرض {key}: {e}")
            problems.append(f"المنتج «{key}»: {e}")

    if changed:
        OFFERS_META_DOC.set(state)
    PANEL.note("offers", "، ".join(problems) if problems else None)


# ---------------------------------------------------------------------------
# روم التقييمات: تفاعل تلقائي + خط بعد كل تقييم
# ---------------------------------------------------------------------------
async def handle_review(message: discord.Message):
    for emoji in REVIEW_REACTIONS:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass
    if REVIEW_AUTO_LINE:
        await send_line(message.channel)
    REVIEWS_META_DOC.set({"count": firestore.Increment(1)}, merge=True)
    await REVIEWS.record(message)  # لإحصائيات الموقع
    await update_stats_log()


# ---------------------------------------------------------------------------
# أوامر الأونرات (OWNER_IDS + صاحب السيرفر)
# ---------------------------------------------------------------------------
COMMAND_LOOKUP: dict[str, str] = {}  # الكلمة المكتوبة -> الاسم الداخلي (ينبني من COMMAND_NAMES)
CUSTOM_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")
TARGET_RE = re.compile(r"^(?:<@!?(\d+)>|(\d{17,20}))$")


PAYMENT_LOOKUP: dict[str, str] = {}


def rebuild_lookups():
    """يعيد بناء القواميس المشتقة - ينادى بالبداية وبعد أي تعديل من الموقع."""
    global PRODUCTS_BY_KEY, ROLE_TIER_IDS
    PAYMENT_LOOKUP.clear()
    PAYMENT_LOOKUP.update({key.strip().lower(): text for key, text in PAYMENT_TEXTS.items() if key.strip()})
    COMMAND_LOOKUP.clear()
    for canon, names in COMMAND_NAMES.items():
        for name in names:
            if name.strip():
                COMMAND_LOOKUP[name.strip().lower()] = canon
    PRODUCTS_BY_KEY = {p["key"]: p for p in PRODUCTS}
    ROLE_TIER_IDS = {tier["id"] for tier in ROLE_TIERS}


def can_use_command(member, canon: str) -> bool:
    if is_owner(member):
        return True
    if canon in ("ban", "kick") and isinstance(member, discord.Member):
        return any(r.id in MOD_ROLE_IDS for r in member.roles)
    return False


def can_send_payment(member, channel=None) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if channel is not None and channel.id in PUBLIC_PAYMENT_CHANNEL_IDS:
        return True
    return any(r.id == STAFF_ROLE_ID for r in member.roles)


def is_owner(member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return member.id in OWNER_IDS or member.id == member.guild.owner_id


def clean_name(raw: str, max_len: int, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw or "").strip("_")[:max_len]
    return name if len(name) >= 2 else fallback


async def parse_target(guild: discord.Guild, arg: str):
    """أول كلمة لازم تكون منشن أو آيدي. يرجع (العضو أو المستخدم، باقي النص)."""
    parts = arg.split(maxsplit=1)
    if not parts:
        return None, ""
    match = TARGET_RE.match(parts[0])
    if not match:
        return None, arg
    user_id = int(match.group(1) or match.group(2))
    rest = parts[1].strip() if len(parts) > 1 else ""
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    if member is not None:
        return member, rest
    try:
        return await bot.fetch_user(user_id), rest
    except discord.HTTPException:
        return discord.Object(id=user_id), rest


async def read_attachments(message: discord.Message) -> list[tuple[str, bytes]]:
    out = []
    for att in message.attachments:
        try:
            out.append((att.filename, await att.read()))
        except discord.HTTPException:
            pass
    return out


def as_files(raw: list[tuple[str, bytes]]) -> list[discord.File]:
    return [discord.File(io.BytesIO(data), filename=name) for name, data in raw]


async def handle_boost(message: discord.Message):
    """صورة بمستوى شارة البوست وكم باقي للجاية (من شوكت يبوست هالسيرفر)."""
    target = next((m for m in message.mentions if isinstance(m, discord.Member)), None) or message.author
    if not isinstance(target, discord.Member):
        return
    if boost_status is None:
        await message.reply("أمر البوست متوقف (ملف boostcard.py مو موجود).", allowed_mentions=discord.AllowedMentions.none())
        return
    if target.premium_since is None:
        text = BOOST_NONE_TEXT if target.id == message.author.id else fill(BOOST_NONE_OTHER_TEXT, user=target.display_name)
        await message.reply(text, allowed_mentions=discord.AllowedMentions.none())
        return
    status = boost_status(target.premium_since)
    line = fill(BOOST_NEXT_TEXT, time=arabic_duration(status["remaining"])) if status["remaining"] else BOOST_MAX_TEXT
    try:
        png = await asyncio.to_thread(render_boost_card, BASE_DIR, status["image"], line)
    except OSError as e:
        log.warning(f"ماكدرت أسوي صورة البوست (تأكد من مجلد boost): {e}")
        await message.reply("ماكدرت أسوي الصورة هسه.", allowed_mentions=discord.AllowedMentions.none())
        return
    await message.reply(file=discord.File(io.BytesIO(png), filename="boost.png"),
                        allowed_mentions=discord.AllowedMentions.none())


async def safe_delete(message: discord.Message):
    try:
        await message.delete()
    except discord.HTTPException:
        pass


async def handle_owner_command(message: discord.Message, cmd: str, arg: str):
    guild = message.guild
    channel = message.channel
    author = message.author

    if cmd == "welcome":
        # تجربة صورة الترحيب بنفس الروم (لك أو لعضو تمنشنه)
        target = message.mentions[0] if message.mentions else author
        if isinstance(target, discord.Member):
            await send_welcome_card(target, channel)
        return

    if cmd == "line":
        if arg:
            return  # "خط" لازم تكون لحالها
        await safe_delete(message)
        await send_line(channel)
        return

    if cmd == "adde":
        found = CUSTOM_EMOJI_RE.findall(arg)
        sources = []  # (name, bytes)
        for animated, name, emoji_id in found:
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{'gif' if animated else 'png'}"
            try:
                sources.append((name, await bot.http.get_from_cdn(url)))
            except discord.HTTPException:
                pass
        if not found and message.attachments:
            att = message.attachments[0]
            stem = os.path.splitext(att.filename)[0]
            sources.append((arg.split()[0] if arg else stem, await att.read()))
        if not sources:
            await message.reply("دز الإيموجي بعد الأمر: `!adde 😀` (إيموجي مخصص)، أو ارفق صورة: `!adde اسم`")
            return
        added, failed = [], []
        for name, data in sources:
            try:
                emoji = await guild.create_custom_emoji(
                    name=clean_name(name, 32, "emoji"), image=data, reason=f"!adde بواسطة {author}"
                )
                added.append(str(emoji))
            except discord.Forbidden:
                failed.append(f"{name} (البوت يحتاج صلاحية Manage Expressions)")
            except discord.HTTPException:
                failed.append(f"{name} (ماكو مكان للإيموجيات، أو الصورة أكبر من 256KB)")
        lines = []
        if added:
            lines.append("✅ انضاف: " + " ".join(added))
        if failed:
            lines.append("❌ ما انضاف: " + "، ".join(failed))
        await message.reply("\n".join(lines))
        return

    if cmd == "adds":
        items = list(message.stickers)
        if not items and message.reference is not None:
            ref = message.reference.resolved
            if not isinstance(ref, discord.Message):
                try:
                    ref = await channel.fetch_message(message.reference.message_id)
                except discord.HTTPException:
                    ref = None
            if ref is not None:
                items = list(ref.stickers)
        try:
            if items:
                item = items[0]
                if item.format == discord.StickerFormatType.lottie:
                    await message.reply("❌ هذا ملصق رسمي من ديسكورد (متحرك بصيغة خاصة) وما ينضاف.")
                    return
                data = await item.read()
                emoji_tag = "⭐"
                try:
                    full = await item.fetch()
                    emoji_tag = getattr(full, "emoji", None) or emoji_tag
                except discord.HTTPException:
                    pass
                ext = "gif" if item.format == discord.StickerFormatType.gif else "png"
                name = clean_name(arg or item.name, 30, "sticker")
            elif message.attachments:
                att = message.attachments[0]
                data = await att.read()
                emoji_tag = "⭐"
                ext = "gif" if att.filename.lower().endswith(".gif") else "png"
                name = clean_name(arg or os.path.splitext(att.filename)[0], 30, "sticker")
            else:
                await message.reply("دز الملصق ويا الأمر `!adds`، أو سوي رد على رسالة بيها ملصق، أو ارفق صورة PNG/GIF.")
                return
            sticker = await guild.create_sticker(
                name=name,
                description="",
                emoji=emoji_tag,
                file=discord.File(io.BytesIO(data), filename=f"{name}.{ext}"),
                reason=f"!adds بواسطة {author}",
            )
            await message.reply(f"✅ انضاف الملصق: **{sticker.name}**")
        except discord.Forbidden:
            await message.reply("❌ البوت يحتاج صلاحية Manage Expressions.")
        except discord.HTTPException:
            await message.reply("❌ ما انضاف (ماكو مكان للملصقات، أو الملف أكبر من 512KB).")
        return

    if cmd in ("ban", "kick"):
        target, reason = await parse_target(guild, arg)
        if target is None:
            return  # بدون منشن/آيدي نعتبرها كلام عادي
        if target.id in OWNER_IDS or target.id in (guild.owner_id, author.id, bot.user.id):
            await message.reply("❌ ما ينفع على هذا الشخص.")
            return
        shown_reason = reason or NO_REASON_TEXT
        full_reason = f"{shown_reason} | بواسطة {author}"
        try:
            if cmd == "ban":
                await guild.ban(target, reason=full_reason, delete_message_seconds=0)
                await message.reply(fill(BAN_REPLY_TEMPLATE, user=f"<@{target.id}>", reason=shown_reason),
                                    allowed_mentions=discord.AllowedMentions.none())
            else:
                if not isinstance(target, discord.Member):
                    await message.reply("❌ هذا الشخص مو موجود بالسيرفر.")
                    return
                await guild.kick(target, reason=full_reason)
                await message.reply(fill(KICK_REPLY_TEMPLATE, user=f"<@{target.id}>", reason=shown_reason),
                                    allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await message.reply("❌ ماكدرت - رتبة البوت لازم تكون فوق رتبته، والبوت يحتاج صلاحية Ban/Kick Members.")
        except discord.HTTPException as e:
            await message.reply(f"❌ صار خطأ: {e}")
        return

    if cmd == "send":
        target, text = await parse_target(guild, arg)
        if target is None or isinstance(target, discord.Object):
            await message.reply("الاستخدام: `-send @عضو الرسالة`")
            return
        files = await read_attachments(message)
        if not text and not files:
            await message.reply("اكتب الرسالة بعد المنشن.")
            return
        try:
            kwargs = {"files": as_files(files)} if files else {}
            await target.send(text or None, **kwargs)
            await message.add_reaction("✅")
        except discord.HTTPException:
            await message.reply("❌ ماكدرت أرسله - خاصه مقفل.")
        return

    if cmd in ("say", "s"):
        files = await read_attachments(message)
        if not arg and not files:
            return
        await safe_delete(message)
        # للأونرات بس، فنسمح بمنشن الكل والرتب (@everyone / @here / @رتبة)
        mentions = discord.AllowedMentions(users=True, roles=True, everyone=True)
        kwargs = {"files": as_files(files)} if files else {}
        try:
            if cmd == "s":
                webhook = await get_or_create_post_webhook(channel)
                if webhook is not None:
                    await webhook.send(
                        content=arg or None,
                        username=author.display_name,
                        avatar_url=author.display_avatar.url,
                        allowed_mentions=mentions,
                        **kwargs,
                    )
                    return
            await channel.send(arg or None, allowed_mentions=mentions, **kwargs)
        except discord.HTTPException as e:
            log.warning(f"فشل {cmd}: {e}")
        return


LEADERBOARD_META_DOC = db.collection("meta").document("leaderboard")
REVIEWS_META_DOC = db.collection("meta").document("reviews_count")

CURRENCY_RATES_DOC = db.collection("meta").document("currency_rates")
CURRENCY_RATES_CACHE: dict[str, dict] = {}


async def load_currency_rates():
    doc = CURRENCY_RATES_DOC.get()
    CURRENCY_RATES_CACHE.clear()
    if doc.exists:
        CURRENCY_RATES_CACHE.update(doc.to_dict())
    CURRENCY_RATES_CACHE["usd"] = {"buy_rate": 1.0, "sell_rate": 1.0}  # ثابت دايماً


@bot.event
async def on_message(message: discord.Message):
    # البوت ممكن يكون داخل سيرفرات ثانية (مثلاً علمود الإيموجيات) - يشتغل بس بسيرفره
    if message.author.bot or message.guild is None or message.guild.id != GUILD_ID:
        return

    content = message.content.strip()
    first_word = content.split(maxsplit=1)[0] if content else ""
    cmd = first_word.lower()

    # أوامر الأونرات - لها الأولوية (الأسماء تتعدل من الموقع)
    canon = COMMAND_LOOKUP.get(cmd)
    if canon is not None and can_use_command(message.author, canon):
        await handle_owner_command(message, canon, content[len(first_word):].strip())
        return

    # صورة شارة البوست - لكل الأعضاء
    if cmd in {c.strip().lower() for c in BOOST_COMMANDS if c.strip()}:
        await handle_boost(message)
        return

    # كلمات الدفع (راجحي، برق، Usdt، Bin...) - لرتبة الطاقم، أو لأي عضو برومات الدفع العامة. الإنجليزي ما يفرق
    # كبير وصغير (Usdt = usdt = USDT)
    payment_text = PAYMENT_LOOKUP.get(content.lower())
    if payment_text is not None and can_send_payment(message.author, message.channel):
        await safe_delete(message)
        try:
            await message.channel.send(payment_text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning(f"ماكدرت أرسل نص الدفع {content}: {e}")
        return

    # روم التقييمات: عد + تفاعل + خط (يستثني البوتات)
    if REVIEWS_CHANNEL_ID is not None and message.channel.id == REVIEWS_CHANNEL_ID:
        await handle_review(message)
        return

    # نشر المنتجات القديم - "-s" لأصحاب رتبة PRODUCT_POST_ROLE_ID (مو أونرات)
    if (
        canon == "s"
        and PRODUCT_POST_ROLE_ID is not None
        and isinstance(message.author, discord.Member)
        and any(r.id == PRODUCT_POST_ROLE_ID for r in message.author.roles)
    ):
        await handle_product_post(message)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
        return
    # يحدث لوگ الإحصائيات أول ما عضو ياخذ/تنسحب منه وحدة من رتب التسلسل
    before_ids = {r.id for r in before.roles}
    after_ids = {r.id for r in after.roles}
    if before_ids != after_ids and (before_ids ^ after_ids) & ROLE_TIER_IDS:
        await update_stats_log()
    # سجل الزبائن بالموقع: أي تغيير برتب الزبائن أو بالاسم ينحفظ
    try:
        await CUSTOMERS.member_updated(after)
    except Exception as e:
        log.warning(f"ماكدرت أحدث سجل الزبون {after.id}: {e}")


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    try:
        await CUSTOMERS.member_left(member)  # يبقى محفوظ بالسجل ويتعلم "طالع"
    except Exception as e:
        log.warning(f"ماكدرت أحدث سجل الزبون {member.id}: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    """يرسل رسالة ترحيب خاصة (DM) للعضو الجديد، وبعدين منشن تنبيهي بروم
    محدد يحذفه فوراً - الحذف ما يسحب التنبيه (notification) اللي وصل
    للعضو، بس ينظف الروم من الرسالة."""
    if member.guild.id != GUILD_ID:
        return  # عضو دخل سيرفر ثاني البوت موجود بيه - مو شغلنا
    try:
        await CUSTOMERS.member_joined(member)  # زبون قديم رجع؟ ترجعله رتبه
    except Exception as e:
        log.warning(f"ماكدرت أتفقد سجل الزبون {member.id}: {e}")
    if WELCOME_DM_TEMPLATE.strip():
        try:
            await member.send(WELCOME_DM_TEMPLATE)
        except discord.HTTPException:
            log.info(f"ماكدرت أرسل رسالة الترحيب الخاصة للعضو {member.id} - عنده الخاص مقفل.")

    await send_welcome_card(member)

    if JOIN_MENTION_CHANNEL_ID is None:
        return

    channel = bot.get_channel(JOIN_MENTION_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(JOIN_MENTION_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            log.warning("ماكدرت ألقى روم منشن الأعضاء الجدد - تحقق من JOIN_MENTION_CHANNEL_ID وصلاحيات البوت.")
            return

    try:
        msg = await channel.send(f"{member.mention} {JOIN_MENTION_TEXT}")
        await msg.delete()
    except discord.HTTPException:
        log.warning("ماكدرت أرسل/أحذف رسالة منشن العضو الجديد - تحقق من صلاحيات البوت بهذا الروم.")


async def update_leaderboard():
    """يبني/يحدّث رسالة لوحة نقاط العملاء بروم اللوحة (لو محدد بـ .env).
    ملاحظة: هذي ميزة خاصة بالبوت بس (نقاط ولاء عبر الديسكورد) - مو جزء
    من مخطط بيانات موقع Store-defter الأصلي."""
    if LEADERBOARD_CHANNEL_ID is None:
        return

    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LEADERBOARD_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            log.warning("ماكدرت ألقى روم لوحة النقاط - تحقق من LEADERBOARD_CHANNEL_ID وصلاحيات البوت.")
            return

    docs = list(
        db.collection("customers")
        .order_by("points", direction=firestore.Query.DESCENDING)
        .stream()
    )

    rank_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    total_len = 0
    shown = 0
    for i, doc in enumerate(docs, start=1):
        data = doc.to_dict()
        discord_id = data.get("discord_id", doc.id)
        points = data.get("points", 0)
        prefix = rank_emojis.get(i, f"`#{i}`")
        line = f"{prefix} <@{discord_id}> — **{points}** نقطة"
        if total_len + len(line) + 1 > 3900:
            break
        lines.append(line)
        total_len += len(line) + 1
        shown += 1

    if shown < len(docs):
        lines.append(f"...و {len(docs) - shown} عميل آخر")

    description = "\n".join(lines) if lines else "ماكو عملاء مسجلين بعد."

    embed = discord.Embed(
        title="🏆 لوحة نقاط العملاء",
        description=description,
        color=BRAND_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Lucifer Exchange • تتحدث تلقائياً بعد كل تسليم")

    meta = LEADERBOARD_META_DOC.get()
    message_id = meta.to_dict().get("message_id") if meta.exists else None

    message = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            message = None

    if message is not None:
        await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        new_message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        LEADERBOARD_META_DOC.set({
            "channel_id": str(LEADERBOARD_CHANNEL_ID),
            "message_id": str(new_message.id),
        })


STATS_LOG_META_DOC = db.collection("meta").document("stats_log")


async def update_stats_log():
    """يبني/يحدّث رسالة لوگ الإحصائيات (عدد كل رتبة من التسلسل + عدد
    تقييمات الزبائن) بروم اللوگ المحدد بـ STATS_LOG_CHANNEL_ID."""
    if STATS_LOG_CHANNEL_ID is None:
        return

    channel = bot.get_channel(STATS_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(STATS_LOG_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            log.warning("ماكدرت ألقى روم لوگ الإحصائيات - تحقق من STATS_LOG_CHANNEL_ID وصلاحيات البوت.")
            return

    guild = bot.get_guild(GUILD_ID)
    role_lines = []
    for tier in ROLE_TIERS:
        role = guild.get_role(tier["id"]) if guild else None
        count = len(role.members) if role else 0
        role_lines.append(f"🎖️ **{tier['label']}** (<@&{tier['id']}>): {count} عضو")

    review_doc = REVIEWS_META_DOC.get()
    review_count = review_doc.to_dict().get("count", 0) if review_doc.exists else 0

    embed = discord.Embed(
        title="📊 إحصائيات السيرفر",
        color=BRAND_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="الرتب", value="\n".join(role_lines), inline=False)
    embed.add_field(name="⭐ تقييمات الزبائن", value=f"**{review_count}**", inline=False)
    embed.set_footer(text="Lucifer Exchange • يتحدث تلقائياً")

    meta = STATS_LOG_META_DOC.get()
    message_id = meta.to_dict().get("message_id") if meta.exists else None

    message = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            message = None

    if message is not None:
        await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        new_message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        STATS_LOG_META_DOC.set({"message_id": str(new_message.id)})


# ---------------------------------------------------------------------------
# تحديث لوحة النقاط تلقائياً لما بوت التكت (أو أي شي ثاني) يغير النقاط
# ---------------------------------------------------------------------------
_customers_watch = None
_leaderboard_refresh_pending = False


async def _debounced_leaderboard_refresh():
    """ينتظر 5 ثواني ويحدث اللوحة مرة وحدة، حتى لو تغيرت نقاط أكثر من زبون
    بنفس اللحظة (يقلل الطلبات على ديسكورد و Firestore)."""
    global _leaderboard_refresh_pending
    if _leaderboard_refresh_pending:
        return
    _leaderboard_refresh_pending = True
    try:
        await asyncio.sleep(5)
    finally:
        _leaderboard_refresh_pending = False
    try:
        await update_leaderboard()
    except Exception as e:  # ما نخلي خطأ باللوحة يوكع المراقبة
        log.warning(f"فشل تحديث لوحة النقاط التلقائي: {e}")


def _on_customers_change(_docs, changes, _read_time):
    # هذا ينادى من thread ثاني (مال Firestore) - نحوله لـ loop البوت
    if not changes:
        return
    asyncio.run_coroutine_threadsafe(_debounced_leaderboard_refresh(), bot.loop)


def start_customers_watch():
    global _customers_watch
    if _customers_watch is not None:
        return
    _customers_watch = db.collection("customers").on_snapshot(_on_customers_change)
    log.info("مراقبة نقاط العملاء شغالة - لوحة النقاط تتحدث تلقائياً.")


@bot.event
async def on_ready():
    bot.add_view(ExchangeStartView())  # نفس الشي لزر تحويل العملة
    start_site_link()  # الربط بموقع لوحة التحكم (مرة وحدة)
    guild = discord.Object(id=GUILD_ID)
    synced = await bot.tree.sync(guild=guild)
    log.info(f"سجّل الدخول كـ {bot.user} — {len(synced)} أوامر مفعّلة بالسيرفر.")
    await update_leaderboard()
    await update_stats_log()
    await load_currency_rates()
    await seed_exchange_rows()
    await ensure_exchange_panel()
    start_customers_watch()
    register_offer_views()
    await ensure_offers()


# ---------------------------------------------------------------------------
# /view-bots و /view-commands
# ---------------------------------------------------------------------------
def chunk_lines(lines: list[str], limit: int = 3900) -> list[str]:
    """يقسم الأسطر لأجزاء كل وحدة أقل من حد وصف الـ Embed."""
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    return chunks


async def send_embed_pages(interaction: discord.Interaction, title: str, lines: list[str]):
    pages = chunk_lines(lines) or ["—"]
    embeds = []
    for i, page in enumerate(pages):
        embed = discord.Embed(description=page, color=BRAND_COLOR)
        if i == 0:
            embed.title = title
        if len(pages) > 1:
            embed.set_footer(text=f"صفحة {i + 1} من {len(pages)}")
        embeds.append(embed)
    # ديسكورد يسمح بـ 10 Embeds ومجموع 6000 حرف بالرسالة الوحدة - نرسل كل صفحة برسالة
    if interaction.response.is_done():
        await interaction.followup.send(embed=embeds[0], ephemeral=True)
    else:
        await interaction.response.send_message(embed=embeds[0], ephemeral=True)
    for embed in embeds[1:]:
        await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="view-bots",
    description="عرض كل البوتات الموجودة بالسيرفر",
    guild=discord.Object(id=GUILD_ID),
)
async def view_bots(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    if guild.chunked is False:
        try:
            await guild.chunk()  # نتأكد إن قائمة الأعضاء كاملة
        except discord.HTTPException:
            pass
    bots = sorted((m for m in guild.members if m.bot), key=lambda m: m.joined_at or datetime.min.replace(tzinfo=timezone.utc))
    lines = []
    for i, member in enumerate(bots, start=1):
        verified = " ✅" if member.public_flags.verified_bot else ""
        joined = f"<t:{int(member.joined_at.timestamp())}:d>" if member.joined_at else "—"
        lines.append(f"**{i}.** {member.mention}{verified} — `{member}` — `{member.id}` — دخل {joined}")
    await send_embed_pages(interaction, f"🤖 البوتات بالسيرفر ({len(bots)})", lines)


HELP_EXTRA = ""  # نص إضافي يطلع آخر قائمة الأوامر (يتعدل من الموقع)


def _cmd(key: str) -> str:
    """أسماء الأمر الحالية (تتغير إذا عدلتها من الموقع)."""
    return " / ".join(f"`{name}`" for name in COMMAND_NAMES.get(key) or [key])


def help_sections() -> list[dict]:
    """كل أوامر بوتات لوسيفر مقسمة. أي أمر جديد ينضاف هنا."""
    payments = " · ".join(f"`{k}`" for k in PAYMENT_TEXTS) or "—"
    return [
        {"key": "main", "emoji": "🟡", "title": "البوت الأساسي", "color": 0xE6D724,
         "about": "النقاط، البورصة، الترحيب، المنتجات، وأوامر الإدارة.",
         "groups": [
             ("⚡ أوامر للكل", [
                 "`/points` — نقاطك وشكد باقي للرتبة الجاية (التيم يشوف نقاط غيره)",
                 "`/exchange_rates` — أسعار العملات بالبورصة",
                 "`/view-bots` — كل البوتات الموجودة بالسيرفر",
                 "`/view-commands` — هذي القائمة",
             ] + ([" / ".join(f"`{c}`" for c in BOOST_COMMANDS) + " (أو ويا `@شخص`) — صورة شارة البوست (بهذا السيرفر)"]
                  if BOOST_COMMANDS else [])),
             ("🛠️ أوامر السلاش (للتيم)", [
                 "`/add-points` — إعطاء نقاط لعميل",
                 "`/points_reset` — تصفير نقاط عميل",
                 "`/points_rescale` — ⚠️ ضرب نقاط الكل ×1000 (مرة وحدة بس)",
                 "`/refresh_leaderboard` — تحديث لوحة النقاط",
                 "`/reviews_recount` — إعادة عد التقييمات",
                 "`/exchange_rate_update` — تحديث سعر عملة بالبورصة (يتحدث بالموقع هم)",
                 "`/role-icon` — أيقونة لرتبة: إيموجي أو صورة، أو مسحها (للأونرات)",
             ]),
             ("👑 أوامر الأونرات", [
                 f"{_cmd('line')} — يحذف رسالتك ويحط الخط الفاصل",
                 f"{_cmd('adde')} + إيموجي — يضيف الإيموجي للسيرفر (أو ارفق صورة ويا اسم)",
                 f"{_cmd('adds')} — يضيف ملصق (دزه ويا الأمر، أو رد على ملصق، أو ارفق صورة)",
                 f"{_cmd('ban')} `@عضو السبب` — تبنيد (يشتغل بالآيدي هم)",
                 f"{_cmd('kick')} `@عضو السبب` — طرد",
                 f"{_cmd('send')} `@عضو الرسالة` — رسالة خاص للعضو من البوت",
                 f"{_cmd('say')} `النص` — يحذف رسالتك والبوت يكتبها",
                 f"{_cmd('s')} `النص` — يحذف رسالتك وتطلع باسمك وصورتك",
                 f"{_cmd('welcome')} أو ويا `@عضو` — تجربة صورة الترحيب",
             ]),
             ("💳 طرق الدفع (اكتب الكلمة لحالها — الطاقم بأي روم، والكل بروم الدفع)", [payments]),
             ("🛍️ رتبة نشر المنتجات", [
                 f"{_cmd('s')} `تفاصيل المنتج` — ينشر المنتج باسمك وصورتك ويا نص الطلب (رسالتك تبقى)",
             ]),
             ("🔄 يشتغل لحاله", [
                 "صورة ترحيب لكل عضو جديد",
                 "نقاط تلقائية لكل تقييم بروم التقييمات",
                 "لوحة النقاط والبورصة ورسائل المنتجات تتحدث لحالها",
                 "لوق اشتراكات نتفلكس وكرانشي رول: يعد الأيام وينبهك لما يخلص الاشتراك",
                 "سجل الزبائن: يحفظهم وترجع رتبهم إذا طلعوا ورجعوا",
             ]),
         ]},
        {"key": "tickets", "emoji": "🎫", "title": "بوت التكت", "color": 0x5865F2,
         "about": "فتح التذاكر وإدارتها وتسجيل التسليم.",
         "groups": [
             ("📋 لوحة التذاكر", ["الزبون يختار القسم من القائمة وتنفتح تذكرته"]),
             ("🎛️ قائمة التحكم داخل التذكرة (الطاقم)", [
                 "**إغلاق** — يغلق ويرسل النسخة للوگ وخاص الزبون",
                 "**تنبيه صاحب التذكرة** — منشن بالتذكرة ويا رسالة خاص",
                 "**إضافة شخص** للتذكرة",
                 "**تسجيل التسليم** — يسجل الطلب ويعطي النقاط",
             ]),
             ("⌨️ أوامر الطاقم داخل التذكرة", [
                 "`r اسم` — تغيير اسم التذكرة",
                 "`c @شخص` — استدعاء (رسالة خاص بيها رابط الروم)، يشتغل بأي روم",
                 "`bot @شخص` — تختار قسم وتوصله رسالة القسم بالخاص (إذا مقفول تنكتب بالروم)، يشتغل بأي روم",
                 "`close` — إغلاق ويا النسخة للوگ وخاص الزبون",
                 "`delete` — حذف الروم ويا النسخة (إذا مغلقة من قبل ما يرسل للزبون مرة ثانية)",
                 "`add @شخص` — إضافة للتذكرة",
                 "`remove @شخص` — إزالة من التذكرة",
                 "`dn` — الاسم يصير dn، إغلاق، ورسالة \"تم إكمال طلبك\" للزبون ويا النسخة",
             ]),
             ("🧹 للأدمن (بأي روم)", [
                 "`clearclosed` / `حذف-المغلقة` — حذف كل التذاكر المغلقة مرة وحدة (ويا زر تأكيد)",
             ]),
         ]},
        {"key": "broadcast", "emoji": "📣", "title": "بوت الإعلانات", "color": 0xF59E0B,
         "about": "إعلانات بالخاص لأعضاء السيرفر (للأونرات بس). كلشي يتسجل بالموقع.",
         "groups": [
             ("📤 الإرسال", [
                 "`-bc الكل النص` — إعلان لكل الأعضاء (تطلع معاينة وتضغط إرسال)",
                 "`-bc @رتبة النص` — إعلان لأصحاب رتبة معينة",
                 "ارفق صورة ويا الأمر حتى تطلع بالإعلان",
             ]),
             ("⏱️ المتابعة", [
                 "`-bc status` — وين وصل الإرسال",
                 "`-bc stop` — إيقاف الإرسال",
                 "`-bc bots` — حالة بوتات الإرسال",
             ]),
             ("🎨 الاسم والصورة لكل البوتات", [
                 "`-bc name الاسم` — اسم موحد",
                 "`-bc avatar` ويا صورة — صورة موحدة",
                 "`-bc profile الاسم` ويا صورة — الاثنين سوا",
             ]),
             ("ℹ️ ملاحظات", [
                 "كل إعلان بيه زر \"إيقاف الإعلانات\" للعضو",
                 "تكدر ترسل وتوقف وتشوف السجل والإحصائيات من الموقع هم",
             ]),
         ]},
        {"key": "badges", "emoji": "🏅", "title": "بوت الشارات", "color": 0xEC4899,
         "about": "صور شارات النيترو والبوست وكم باقي للشارة الجاية (لكل الأعضاء).",
         "groups": [
             ("🏅 الأوامر", [
                 "`-نيترو` / `-nitro` — صورة شارة النيترو وكم باقي للجاية",
                 "`=boost` / `-بوست` — صورة شارة البوست وكم باقي للجاية",
                 "ويا `@شخص` أو آيديه حتى تشوف شارته",
             ]),
         ]},
        {"key": "transfers", "emoji": "💳", "title": "بوت التحويلات", "color": 0x10B981,
         "about": "تأكيد التحويلات بالوصل، ومحفظة لكل عملة، والسحب بأي عملة.",
         "groups": [
             ("👤 للزبون (بخاص البوت)", [
                 "`طرق الدفع` — أزرار طرق الدفع (نفس نصوص الرد التلقائي) حتى يعرف وين يحول",
                 "**صورة الوصل** — يختار العملة، والبوت يقرا الوصل ويرسله للمراجعة",
                 "`رصيدي` — رصيده بكل عملة",
                 "`سحب` — يسحب رصيده ويستلم بأي عملة",
                 "`تحويل` — يحول من رصيده لشخص ثاني بالآيدي (يوصل للاثنين رسالة)",
                 "`تبديل` — يبدل رصيده من عملة لعملة ثانية بسعر البورصة (لحاله)",
                 "`/wallet` و `/withdraw` و `/transfer` و `/swap` — نفسها بالسيرفر",
             ]),
             ("🛠️ للطاقم", [
                 "زر **قبول** (تكدر تعدل المبلغ) وزر **رفض** على كل وصل بروم عملته",
                 "زر **تم التحويل** و **رفض وإرجاع الرصيد** على كل طلب سحب",
                 "`/wallet @عضو` — محفظة أي عضو",
                 "`/wallet-adjust` — إضافة أو خصم رصيد يدوي (توصل للزبون رسالة)",
                 "روم **لوحة الأرصدة** — أرصدة كل الزبائن تتحدث لحالها",
             ]),
             ("🛡️ يشتغل لحاله", [
                 "يرفض الوصل المكرر (نفس رقم العملية أو نفس الصورة) وينبه على الصور المتشابهة",
             ]),
         ]},
        {"key": "site", "emoji": "🌐", "title": "موقع لوحة التحكم", "color": 0x22C55E,
         "about": "تسجيل الدخول بحساب Google مالك بس.",
         "groups": [
             ("📄 الصفحات", [
                 "**الزبائن** — سجل الزبائن ورتبهم",
                 "**البوت الأساسي** — تعديل كل النصوص والمنتجات والأوامر والاشتراكات والبورصة",
                 "**بوت التكت** — الأقسام والصور والنصوص",
                 "**بوت الإعلانات** — إرسال، سجل، إحصائيات، البوتات وإعداداتها",
                 "**بوت التحويلات** — الوصولات والسحوبات والمحافظ والعملات والأسعار",
                 "**التذاكر / التقييمات** — الإحصائيات",
             ]),
         ]},
    ]


def _help_fields(lines: list[str]) -> list[str]:
    """يقسم الأسطر لقطع كل وحدة أقل من حد خانة الامبيد (1024)."""
    parts, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 1000:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current:
        parts.append(current)
    return parts


def help_overview_embed() -> discord.Embed:
    sections = help_sections()
    embed = discord.Embed(title="📜 كل الأوامر", color=BRAND_COLOR,
                          description="اختار البوت من القائمة تحت حتى تشوف أوامره.")
    for sec in sections:
        count = sum(len(lines) for _, lines in sec["groups"])
        embed.add_field(name=f"{sec['emoji']} {sec['title']}", value=f"{sec['about']}\n-# {count} سطر", inline=False)
    if HELP_EXTRA.strip():
        embed.add_field(name="📝 ملاحظات", value=HELP_EXTRA.strip()[:1024], inline=False)
    return embed


def help_section_embed(key: str) -> discord.Embed:
    sec = next((x for x in help_sections() if x["key"] == key), None)
    if sec is None:
        return help_overview_embed()
    embed = discord.Embed(title=f"{sec['emoji']} {sec['title']}", description=sec["about"], color=sec["color"])
    for name, lines in sec["groups"]:
        for i, part in enumerate(_help_fields(lines)):
            embed.add_field(name=name if i == 0 else f"{name} (تكملة)", value=part, inline=False)
    embed.set_footer(text="اختار قسم ثاني من القائمة")
    return embed


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        options = [discord.SelectOption(label="نظرة عامة", value="overview", emoji="📜")]
        options += [discord.SelectOption(label=sec["title"], value=sec["key"], emoji=sec["emoji"], description=sec["about"][:100])
                    for sec in help_sections()]
        select = discord.ui.Select(placeholder="اختار البوت", options=options)

        async def chosen(interaction: discord.Interaction):
            value = select.values[0]
            embed = help_overview_embed() if value == "overview" else help_section_embed(value)
            await interaction.response.edit_message(embed=embed, view=self)

        select.callback = chosen
        self.add_item(select)


@bot.tree.command(
    name="view-commands",
    description="عرض كل أوامر البوتات",
    guild=discord.Object(id=GUILD_ID),
)
async def view_commands(interaction: discord.Interaction):
    await interaction.response.send_message(embed=help_overview_embed(), view=HelpView(), ephemeral=True)


# ---------------------------------------------------------------------------
# أيقونة الرتبة (/role-icon) - للأونرات
# ---------------------------------------------------------------------------
@bot.tree.command(
    name="role-icon",
    description="إضافة أو تغيير أيقونة رتبة (إيموجي أو صورة)",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(
    role="الرتبة",
    emoji="إيموجي (عادي أو من إيموجيات السيرفر)",
    image="أو ارفع صورة (PNG/JPG، أقل من 256KB)",
    remove="مسح الأيقونة الحالية",
)
async def role_icon(interaction: discord.Interaction, role: discord.Role,
                    emoji: str | None = None, image: discord.Attachment | None = None,
                    remove: bool = False):
    if not is_owner(interaction.user):
        await interaction.response.send_message("❌ هذا الأمر للأونرات بس.", ephemeral=True)
        return
    guild = interaction.guild
    if "ROLE_ICONS" not in guild.features:
        await interaction.response.send_message(
            "❌ السيرفر لازم يكون بوست لفل 2 حتى تنحط أيقونات للرتب.", ephemeral=True)
        return
    if role >= guild.me.top_role or role.managed:
        await interaction.response.send_message(
            "❌ ما أكدر أعدل هالرتبة — رتبة البوت لازم تكون فوقها (ورتب البوتات ما تتعدل).", ephemeral=True)
        return
    if not remove and not emoji and image is None:
        await interaction.response.send_message("❌ اختار إيموجي أو ارفع صورة (أو فعّل remove).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if remove:
            icon, shown = None, "مسحت الأيقونة"
        elif image is not None:
            if not (image.content_type or "").startswith("image/"):
                await interaction.followup.send("❌ الملف لازم يكون صورة.", ephemeral=True)
                return
            if image.size > 256 * 1024:
                await interaction.followup.send("❌ الصورة أكبر من 256KB، صغّرها وارجع جرب.", ephemeral=True)
                return
            icon, shown = await image.read(), "حطيت الصورة"
        else:
            emoji = emoji.strip()
            match = CUSTOM_EMOJI_RE.search(emoji)
            if match:
                ext = "gif" if match.group(1) else "png"
                url = f"https://cdn.discordapp.com/emojis/{match.group(3)}.{ext}"
                known = bot.get_emoji(int(match.group(3)))
                if known is not None:
                    icon = await known.read()
                else:
                    async with aiohttp.ClientSession() as session, session.get(url) as resp:
                        if resp.status != 200:
                            await interaction.followup.send("❌ ماكدرت أنزل صورة الإيموجي.", ephemeral=True)
                            return
                        icon = await resp.read()
            else:
                icon = emoji
            shown = f"حطيت {emoji}"
        await role.edit(display_icon=icon, reason=f"/role-icon بواسطة {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("❌ البوت ما عنده صلاحية Manage Roles.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ ديسكورد رفض: {e.text or e}", ephemeral=True)
        return
    await interaction.followup.send(f"✅ {shown} لرتبة {role.mention}.", ephemeral=True)


@bot.tree.command(
    name="refresh_leaderboard",
    description="تحديث لوحة نقاط العملاء يدوياً",
    guild=discord.Object(id=GUILD_ID),
)
async def refresh_leaderboard(interaction: discord.Interaction):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return
    if LEADERBOARD_CHANNEL_ID is None:
        await interaction.response.send_message(
            "❌ ماكو روم محدد للوحة النقاط - ضيف LEADERBOARD_CHANNEL_ID بملف .env أول.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    await update_leaderboard()
    await interaction.followup.send("✅ تم تحديث لوحة النقاط.", ephemeral=True)


# ---------------------------------------------------------------------------
# عدد تقييمات الزبائن
# ---------------------------------------------------------------------------
@bot.tree.command(
    name="reviews_recount",
    description="إعادة احتساب عدد التقييمات من كل تاريخ الروم (للتيم فقط)",
    guild=discord.Object(id=GUILD_ID),
)
async def reviews_recount(interaction: discord.Interaction):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return
    if REVIEWS_CHANNEL_ID is None:
        await interaction.response.send_message(
            "❌ ماكو روم تقييمات محدد - ضيف REVIEWS_CHANNEL_ID بملف .env أول.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(REVIEWS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(REVIEWS_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            await interaction.response.send_message(
                "❌ ماكدرت ألقى روم التقييمات - تحقق من الآيدي وصلاحيات البوت.",
                ephemeral=True,
            )
            return

    await interaction.response.defer(ephemeral=True)

    count = 0
    async for message in channel.history(limit=None):
        if not message.author.bot:
            count += 1

    REVIEWS_META_DOC.set({"count": count}, merge=True)
    await update_stats_log()
    await interaction.followup.send(
        f"✅ تم احتساب **{count}** تقييم من كل تاريخ الروم.", ephemeral=True
    )


# ---------------------------------------------------------------------------
# إدارة نقاط العملاء مباشرة (بدون تسجيل طلب)
# ---------------------------------------------------------------------------
@bot.tree.command(
    name="add-points",
    description="إعطاء نقاط لعميل مباشرة بدون تسجيل طلب (للتيم فقط)",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(buyer="العميل", points="عدد النقاط (تنضرب ×1000 تلقائياً)")
async def add_points(interaction: discord.Interaction, buyer: discord.Member, points: int):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return
    if points <= 0:
        await interaction.response.send_message("❌ عدد النقاط لازم يكون رقم موجب.", ephemeral=True)
        return

    scaled_points = points * 1000
    customer_ref = db.collection("customers").document(str(buyer.id))
    customer_ref.set(
        {
            "discord_id": str(buyer.id),
            "display_name": buyer.display_name,
            "points": firestore.Increment(scaled_points),
            "last_order_at": datetime.now(timezone.utc),
        },
        merge=True,
    )
    new_total_points = customer_ref.get().to_dict().get("points", scaled_points)
    await update_leaderboard()
    await check_rank_promotion(buyer, new_total_points)

    try:
        await buyer.send(fill(POINTS_DM_TEMPLATE, points=scaled_points, total=new_total_points))
    except discord.Forbidden:
        pass

    await interaction.response.send_message(
        f"✅ تم إعطاء **{scaled_points}** نقطة لـ {buyer.mention}.", ephemeral=True
    )


@bot.tree.command(
    name="points",
    description="عرض نقاطك (أو نقاط عضو ثاني للتيم) وشكد باقي للرتبة الجاية",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(member="اختياري - تفقد نقاط عضو ثاني (للتيم بس)")
async def points_check(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    if member is not None and member.id != interaction.user.id and not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ بس أعضاء التيم يقدرون يتفقدون نقاط عضو ثاني.", ephemeral=True
        )
        return

    doc = db.collection("customers").document(str(target.id)).get()
    total = doc.to_dict().get("points", 0) if doc.exists else 0

    next_tier = None
    for tier in RANK_THRESHOLDS:
        if total < tier["points"]:
            next_tier = tier
            break

    embed = discord.Embed(title=f"⭐ نقاط {target.display_name}", color=BRAND_COLOR)
    embed.add_field(name="الرصيد الحالي", value=f"**{total}** نقطة", inline=False)
    if next_tier:
        remaining = next_tier["points"] - total
        role = interaction.guild.get_role(next_tier["role_id"])
        role_name = role.name if role else "؟"
        embed.add_field(
            name="للرتبة الجاية", value=f"باقيلك **{remaining}** نقطة توصل لـ {role_name}", inline=False
        )
    else:
        embed.add_field(name="🏆", value="وصلت لأعلى رتبة!", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="points_reset",
    description="تصفير نقاط عميل معين (للتيم فقط)",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(buyer="العميل")
async def points_reset(interaction: discord.Interaction, buyer: discord.Member):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return

    customer_ref = db.collection("customers").document(str(buyer.id))
    customer_ref.set(
        {
            "discord_id": str(buyer.id),
            "display_name": buyer.display_name,
            "points": 0,
        },
        merge=True,
    )
    await update_leaderboard()

    await interaction.response.send_message(f"🔄 تم تصفير نقاط {buyer.mention}.", ephemeral=True)


@bot.tree.command(
    name="points_rescale",
    description="⚠️ تحويل نقاط كل العملاء الحاليين ×1000 - مرة وحدة بس (للتيم فقط)",
    guild=discord.Object(id=GUILD_ID),
)
async def points_rescale(interaction: discord.Interaction):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    docs = list(db.collection("customers").stream())
    batch = db.batch()
    updated = 0
    for doc in docs:
        current_points = doc.to_dict().get("points", 0) or 0
        batch.update(doc.reference, {"points": current_points * 1000})
        updated += 1
        if updated % 400 == 0:
            batch.commit()
            batch = db.batch()
    if updated % 400 != 0:
        batch.commit()

    await update_leaderboard()
    await interaction.followup.send(
        f"✅ تم ضرب نقاط **{updated}** عميل ×1000.\n"
        "⚠️ لا تشغل هذا الأمر مرة ثانية إلا إذا متأكد - يضرب النقاط الحالية "
        "×1000 كل مرة (يعني تشغيله مرتين يفسد الأرقام).",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# بورصة تبديل العملات
# ---------------------------------------------------------------------------
@bot.tree.command(
    name="exchange_rate_update",
    description="تحديث سعر شراء وبيع عملة (للتيم فقط)",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(
    currency="العملة",
    buy_price_usd="سعر الشراء بالدولار (لما الزبون يشتريها منا)",
    sell_price_usd="سعر البيع بالدولار (لما الزبون يبيعها الينا)",
    buy_batch_size="كم وحدة عملة تقابل سعر الشراء (افتراضي 1 - اسيا/ماستركارد عادة 10)",
    sell_batch_size="كم وحدة عملة تقابل سعر البيع (افتراضي 1 - اسيا/ماستركارد عادة 10)",
)
@app_commands.choices(currency=UPDATABLE_CURRENCY_CHOICES)
async def exchange_rate_update(
    interaction: discord.Interaction,
    currency: app_commands.Choice[str],
    buy_price_usd: float,
    sell_price_usd: float,
    buy_batch_size: float = 1.0,
    sell_batch_size: float = 1.0,
):
    if not has_team_role(interaction.user):
        await interaction.response.send_message(
            "❌ هذا الأمر مخصص لأعضاء التيم بس.", ephemeral=True
        )
        return
    if buy_price_usd <= 0 or sell_price_usd <= 0 or buy_batch_size <= 0 or sell_batch_size <= 0:
        await interaction.response.send_message("❌ الأرقام لازم تكون موجبة.", ephemeral=True)
        return

    buy_rate = buy_batch_size / buy_price_usd
    sell_rate = sell_batch_size / sell_price_usd

    CURRENCY_RATES_CACHE[currency.value] = {"buy_rate": buy_rate, "sell_rate": sell_rate}
    CURRENCY_RATES_DOC.set({currency.value: {"buy_rate": buy_rate, "sell_rate": sell_rate}}, merge=True)
    # نفس السعر يطلع بالموقع
    rows = [dict(r) for r in EXCHANGE_RATES_ROWS if r.get("key") != currency.value]
    rows.append({"key": currency.value, "label": currency.name, "buy_price": buy_price_usd, "buy_batch": buy_batch_size,
                 "sell_price": sell_price_usd, "sell_batch": sell_batch_size})
    order = list(CURRENCIES)
    rows.sort(key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
    _G["EXCHANGE_RATES_ROWS"] = rows
    await asyncio.to_thread(PANEL.write_value, "EXCHANGE_RATES", rows)

    await interaction.response.send_message(
        f"✅ تحديث **{currency.name}**:\n"
        f"شراء: {buy_batch_size:g} لكل {buy_price_usd:g}$ (= {buy_rate:.4f} وحدة/$)\n"
        f"بيع: {sell_batch_size:g} لكل {sell_price_usd:g}$ (= {sell_rate:.4f} وحدة/$)",
        ephemeral=True,
    )


@bot.tree.command(
    name="exchange_rates",
    description="عرض أسعار كل العملات الحالية",
    guild=discord.Object(id=GUILD_ID),
)
async def exchange_rates_display(interaction: discord.Interaction):
    if EXCHANGE_CHANNEL_ID is not None and interaction.channel_id != EXCHANGE_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ هذا الأمر يشتغل بس بروم <#{EXCHANGE_CHANNEL_ID}>.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="💱 أسعار العملات — Lucifer Exchange",
        color=BRAND_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    keys = list(CURRENCIES.keys())
    for i, key in enumerate(keys):
        info = CURRENCIES[key]
        rate = CURRENCY_RATES_CACHE.get(key)
        if rate is None:
            value = "غير محدد بعد"
        else:
            value = f"🟢 شراء `{rate['buy_rate']:.4f}`\n🔴 بيع `{rate['sell_rate']:.4f}`"
        embed.add_field(name=f"{info['emoji']} {info['label']}", value=value, inline=True)
        if i % 2 == 1 and i != len(keys) - 1:
            embed.add_field(name="\u200b", value="\u200b", inline=True)  # سطر جديد كل عملتين

    embed.set_footer(text=f"الأسعار مقابل وحدة {USDT_EMOJI} • تتحدث يدوياً")
    await interaction.response.send_message(embed=embed)


def compute_exchange(from_key: str, to_key: str, amount: float):
    from_rate = CURRENCY_RATES_CACHE.get(from_key)
    to_rate = CURRENCY_RATES_CACHE.get(to_key)
    if from_rate is None or to_rate is None:
        return None
    usd_value = amount / from_rate["sell_rate"]
    result = usd_value * to_rate["buy_rate"]
    return usd_value, result


def _parse_exchange_amount(text: str):
    """يقبل 20 أو 20,000 أو أرقام عربية."""
    text = (text or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    text = text.replace(",", "").replace("،", "").replace(" ", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _fmt_ex(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class ExchangeAmountModal(discord.ui.Modal):
    """mode = have: الزبون يكتب شكد عنده ويطلعله شكد يستلم.
    mode = want: الزبون يكتب شكد يريد ويطلعله شكد لازم يدفع."""

    def __init__(self, from_key: str, to_key: str, mode: str = "have"):
        super().__init__(title="تحويل عملة")
        self.from_key = from_key
        self.to_key = to_key
        self.mode = mode
        info = CURRENCIES[from_key if mode == "have" else to_key]
        label = f"الكمية اللي عندك ({info['label']})" if mode == "have" else f"الكمية اللي تريدها ({info['label']})"
        self.amount_input = discord.ui.TextInput(label=label[:45], placeholder="مثلاً: 20", required=True, max_length=20)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        amount = _parse_exchange_amount(self.amount_input.value)
        if amount is None:
            await interaction.response.send_message("❌ اكتب رقم صحيح موجب.", ephemeral=True)
            return

        if self.mode == "want":
            # عكس الحساب: شكد لازم يدفع من عملته حتى يستلم الكمية اللي يريدها
            back = compute_exchange(self.from_key, self.to_key, 1)
            if back is None or not back[1]:
                await interaction.response.send_message("❌ ماكو سعر محدد لوحدة من العملتين بعد.", ephemeral=True)
                return
            pay = amount / back[1]
            usd_value = pay * back[0]
            have, get = pay, amount
        else:
            computed = compute_exchange(self.from_key, self.to_key, amount)
            if computed is None:
                await interaction.response.send_message("❌ ماكو سعر محدد لوحدة من العملتين بعد.", ephemeral=True)
                return
            usd_value, get = computed
            have = amount

        from_info = CURRENCIES[self.from_key]
        to_info = CURRENCIES[self.to_key]
        embed = discord.Embed(
            title="✅ نتيجة التحويل",
            color=BRAND_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="تدفع" if self.mode == "want" else "عندك",
            value=f"{_fmt_ex(have)} {from_info['emoji']}\n**{from_info['label']}**",
            inline=True,
        )
        embed.add_field(name="\u200b", value="↔️", inline=True)
        embed.add_field(
            name="تريد" if self.mode == "want" else "تحصل",
            value=f"{_fmt_ex(get)} {to_info['emoji']}\n**{to_info['label']}**",
            inline=True,
        )
        embed.set_footer(text=f"القيمة الوسيطة: {usd_value:.2f}$ • Lucifer Exchange")

        await interaction.response.send_message(embed=embed, ephemeral=True)


class ExchangeModeView(discord.ui.View):
    """بعد ما يختار العملتين: يحدد شكد عنده، لو شكد يريد."""

    def __init__(self, from_key: str, to_key: str):
        super().__init__(timeout=120)
        self.from_key = from_key
        self.to_key = to_key

    @discord.ui.button(label="أحدد شكد عندي", emoji="💰", style=discord.ButtonStyle.primary)
    async def have(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ExchangeAmountModal(self.from_key, self.to_key, "have"))

    @discord.ui.button(label="أحدد شكد أريد", emoji="🎯", style=discord.ButtonStyle.success)
    async def want(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ExchangeAmountModal(self.from_key, self.to_key, "want"))


class ExchangeToSelect(discord.ui.Select):
    def __init__(self, from_key: str):
        self.from_key = from_key
        options = [
            discord.SelectOption(label=info["label"], value=key, emoji=info["icon"])
            for key, info in CURRENCIES.items()
            if key != from_key
        ]
        super().__init__(placeholder="العملة اللي تريدها", options=options)

    async def callback(self, interaction: discord.Interaction):
        frm, to = CURRENCIES[self.from_key], CURRENCIES[self.values[0]]
        await interaction.response.edit_message(
            content=f"{frm['icon']} **{frm['label']}** ← {to['icon']} **{to['label']}**\n"
                    "تريد تكتب شكد عندك، لو شكد تريد تستلم؟",
            view=ExchangeModeView(self.from_key, self.values[0]),
        )


class ExchangeToView(discord.ui.View):
    def __init__(self, from_key: str):
        super().__init__(timeout=120)
        self.add_item(ExchangeToSelect(from_key))


class ExchangeFromSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=info["label"], value=key, emoji=info["icon"])
            for key, info in CURRENCIES.items()
        ]
        super().__init__(placeholder="العملة اللي عندك", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="اختر العملة اللي تريدها:", view=ExchangeToView(from_key=self.values[0])
        )


class ExchangeFromView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ExchangeFromSelect())


EXCHANGE_START_CUSTOM_ID = "lucifer:exchange_start"


class ExchangeStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="💱 تحويل عملة", style=discord.ButtonStyle.success, custom_id=EXCHANGE_START_CUSTOM_ID
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "اختر العملة اللي عندك:", view=ExchangeFromView(), ephemeral=True
        )


EXCHANGE_PANEL_META_DOC = db.collection("meta").document("exchange_panel")


async def ensure_exchange_panel():
    """يتأكد إن لوحة زر البورصة موجودة بروم البورصة وبأحدث شكل - يسوي
    تعديل بالرسالة الموجودة (لو أكو) بدل ما يرسل وحدة جديدة، عشان أي
    تحسين بالشكل يوصل حتى لو اللوحة كانت مرسلة من قبل."""
    if EXCHANGE_CHANNEL_ID is None:
        return

    channel = bot.get_channel(EXCHANGE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(EXCHANGE_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            log.warning("ماكدرت ألقى روم البورصة - تحقق من EXCHANGE_CHANNEL_ID وصلاحيات البوت.")
            return

    embed = discord.Embed(
        title="💱 بورصة Lucifer Exchange",
        description=(
            "حولّ بين عملاتك بضغطة زر - اختر العملة اللي عندك، والعملة اللي "
            "تريدها، واكتب الكمية.\n\nالنتيجة تطلعلك أنت بس. 🔒"
        ),
        color=BRAND_COLOR,
    )
    embed.set_footer(text="Lucifer Exchange")

    file = None
    if os.path.isfile(BANNER_FILENAME):
        file = discord.File(BANNER_FILENAME, filename=BANNER_FILENAME)
        embed.set_image(url=f"attachment://{BANNER_FILENAME}")

    meta = EXCHANGE_PANEL_META_DOC.get()
    message_id = meta.to_dict().get("message_id") if meta.exists else None

    existing_message = None
    if message_id:
        try:
            existing_message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            existing_message = None

    try:
        if existing_message is not None:
            if file:
                await existing_message.edit(embed=embed, view=ExchangeStartView(), attachments=[file])
            else:
                await existing_message.edit(embed=embed, view=ExchangeStartView())
            return

        if file:
            new_message = await channel.send(embed=embed, view=ExchangeStartView(), file=file)
        else:
            new_message = await channel.send(embed=embed, view=ExchangeStartView())
        EXCHANGE_PANEL_META_DOC.set({"message_id": str(new_message.id)})
    except discord.Forbidden:
        log.warning("ماكدرت أرسل لوحة البورصة - تحقق من صلاحية Send Messages للبوت بهذا الروم.")

# ---------------------------------------------------------------------------
# الربط بموقع لوحة التحكم
# كل إعداد مربوط هنا يطلع بالموقع ويتعدل من هناك بدون Restart. القيم المكتوبة
# بالكود فوق تبقى هي "الأصلية" اللي يرجعلها زر "رجوع للأصلي" بالموقع.
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL = Panel(db, SERVER_KEY, "main", "البوت الأساسي", BASE_DIR)
CUSTOMERS = CustomerLog(db, SERVER_KEY, lambda: CUSTOMER_ROLE_IDS, lambda: RESTORE_ROLES_ON_REJOIN)
REVIEWS = ReviewLog(db, SERVER_KEY)
_G = globals()


def _products_dump(products):
    return [
        {
            "key": p["key"], "title": p["title"], "channel": p["channel"], "image": p["image"],
            "details": p["details"],
            "buttons": [{"label": label, "text": text} for label, text in p.get("extra_buttons", [])],
        }
        for p in products
    ]


def _products_load(rows):
    out, seen = [], set()
    for row in rows:
        key = (row.get("key") or "").strip()
        title = row.get("title") or ""
        if not key or key in seen:
            raise ValueError("كل منتج لازم إله مفتاح خاص بيه")
        if not row.get("channel"):
            raise ValueError(f"المنتج «{title or key}» بدون روم")
        if not row.get("image"):
            raise ValueError(f"المنتج «{title or key}» بدون صورة")
        buttons = [(b["label"].strip(), b["text"]) for b in row.get("buttons") or [] if b.get("label", "").strip()]
        if len(buttons) > 20 or any(len(label) > 80 for label, _ in buttons):
            raise ValueError(f"أزرار المنتج «{title or key}»: الحد 20 زر، واسم الزر 80 حرف")
        if len(row.get("details") or "") > 4000:
            raise ValueError(f"تفاصيل المنتج «{title or key}» أطول من 4000 حرف")
        seen.add(key)
        product = {"key": key, "title": title, "image": row["image"], "channel": row["channel"],
                   "details": row.get("details") or ""}
        if buttons:
            product["extra_buttons"] = buttons
        out.append(product)
    return out


def _command_binding(canon: str, label: str, help: str = ""):
    PANEL.bind(
        f"cmd_{canon}", "words", label, help=help,
        get=lambda: COMMAND_NAMES[canon], put=lambda v: COMMAND_NAMES.__setitem__(canon, v),
    )


def _welcome_number(key: str, label: str, getter, setter, help: str = ""):
    PANEL.bind(key, "number", label, get=getter, put=lambda v: setter(int(v)), help=help)


def _setup_panel():
    W = welcome_module

    PANEL.group("shortcuts", "اختصارات النصوص",
                "الطاقم يكتب الكلمة لحالها بأي روم، والبوت يحذفها ويرسل النص مكانها.")
    PANEL.bind(
        "PAYMENT_TEXTS", "list", "الاختصارات", item_label="اختصار", title_field="word",
        fields=[F("word", "text", "الكلمة"), F("text", "longtext", "النص اللي يطلع")],
        get=lambda: PAYMENT_TEXTS, put=lambda v: _G.__setitem__("PAYMENT_TEXTS", v),
        dump=lambda d: [{"word": k, "text": t} for k, t in d.items()],
        load=lambda rows: {r["word"].strip(): r["text"] for r in rows if r["word"].strip()},
    )
    PANEL.bind_var(_G, "STAFF_ROLE_ID", "id", "الرتبة اللي تكدر تستخدم الاختصارات", kind="role", required=True)
    PANEL.bind_var(_G, "PUBLIC_PAYMENT_CHANNEL_IDS", "ids", "رومات يكدر بيها أي عضو يطلع طرق الدفع", kind="channel")

    PANEL.group("offers", "رسائل المنتجات",
                "كل منتج إله رسالة بروم خاص. أي تعديل هنا يتعدل بنفس الرسالة بدون ما تتكرر.")
    PANEL.bind_var(_G, "OFFER_FIXED_TEXT", "longtext", "النص الثابت تحت عنوان كل منتج")
    PANEL.bind_var(_G, "DETAILS_BUTTON_LABEL", "text", "اسم زر التفاصيل", required=True)
    PANEL.bind(
        "PRODUCTS", "list", "المنتجات", item_label="منتج", title_field="title",
        fields=[
            F("key", "key", "المفتاح"),
            F("title", "text", "العنوان"),
            F("channel", "id", "الروم", kind="channel"),
            F("image", "image", "الصورة", dir=OFFER_IMAGES_DIR),
            F("details", "longtext", "التفاصيل (تطلع لما ينضغط الزر)"),
            F("buttons", "list", "أزرار إضافية", item_label="زر", title_field="label",
              fields=[F("label", "text", "اسم الزر"), F("text", "longtext", "النص اللي يطلع")]),
        ],
        get=lambda: PRODUCTS, put=lambda v: _G.__setitem__("PRODUCTS", v),
        dump=_products_dump, load=_products_load,
    )
    PANEL.bind_var(_G, "LINE_FILENAME", "image", "صورة الخط الفاصل", dir="", required=True)

    PANEL.group("commands", "الأوامر",
                "أسماء أوامر الأونرات ورسائلها. كل سطر اسم للأمر، وإذا فرغت الخانة يتعطل الأمر.")
    PANEL.bind_var(_G, "OWNER_IDS", "ids", "الأونرات (غير صاحب السيرفر)", kind="user",
                   help="آيديات الأشخاص اللي يكدرون يستخدمون كل الأوامر تحت.")
    PANEL.bind_var(_G, "MOD_ROLE_IDS", "ids", "رتب تكدر تستخدم البان والطرد", kind="role",
                   help="فارغ = الأونرات بس.")
    _command_binding("ban", "أمر البان")
    PANEL.bind_var(_G, "BAN_REPLY_TEMPLATE", "text", "رد البوت بعد البان",
                   help="{user} = العضو، {reason} = السبب")
    _command_binding("kick", "أمر الطرد")
    PANEL.bind_var(_G, "KICK_REPLY_TEMPLATE", "text", "رد البوت بعد الطرد",
                   help="{user} = العضو، {reason} = السبب")
    PANEL.bind_var(_G, "NO_REASON_TEXT", "text", "النص إذا ما انكتب سبب")
    _command_binding("line", "أمر الخط الفاصل")
    _command_binding("say", "أمر الكتابة باسم البوت")
    _command_binding("s", "أمر النشر باسمك وصورتك")
    _command_binding("send", "أمر رسالة الخاص")
    _command_binding("adde", "أمر إضافة إيموجي")
    _command_binding("adds", "أمر إضافة ملصق")
    _command_binding("welcome", "أمر تجربة صورة الترحيب")

    PANEL.group("boost", "شارة البوست",
                "أمر لكل الأعضاء يطلع صورة بمستوى شارة البوست وكم باقي للجاية (يحسب البوست بهذا السيرفر).")
    PANEL.bind_var(_G, "BOOST_COMMANDS", "words", "أسماء الأمر", help="كل سطر اسم. فارغ = الأمر متوقف.")
    PANEL.bind_var(_G, "BOOST_NEXT_TEXT", "text", "النص اللي ينكتب على الصورة", help="{time} = الوقت الباقي")
    PANEL.bind_var(_G, "BOOST_MAX_TEXT", "text", "النص إذا وصل لأعلى شارة")
    PANEL.bind_var(_G, "BOOST_NONE_TEXT", "text", "الرد إذا ما عنده بوست")
    PANEL.bind_var(_G, "BOOST_NONE_OTHER_TEXT", "text", "الرد إذا الشخص المنشن ما عنده بوست", help="{user} = اسمه")

    PANEL.group("reviews", "التقييمات")
    PANEL.bind_var(_G, "REVIEWS_CHANNEL_ID", "id", "روم التقييمات", kind="channel")
    PANEL.bind_var(_G, "REVIEW_REACTIONS", "words", "إيموجيات التفاعل على كل تقييم",
                   help="كل سطر إيموجي بصيغة <:الاسم:الآيدي>")
    PANEL.bind_var(_G, "REVIEW_AUTO_LINE", "bool", "خط فاصل بعد كل تقييم")

    PANEL.group("welcome", "الترحيب")
    PANEL.bind_var(_G, "WELCOME_CHANNEL_ID", "id", "روم صورة الترحيب", kind="channel", required=True)
    PANEL.bind_var(_G, "WELCOME_DM_TEMPLATE", "longtext", "رسالة الخاص للعضو الجديد",
                   help="خليها فارغة حتى توقف رسالة الخاص.")
    PANEL.bind_var(_G, "JOIN_MENTION_CHANNEL_ID", "id", "روم منشن التوثيق", kind="channel",
                   help="البوت يمنشن العضو الجديد هنا ويحذف المنشن فوراً. فارغ = ملغي.")
    PANEL.bind_var(_G, "JOIN_MENTION_TEXT", "text", "نص منشن التوثيق")

    PANEL.group("customers", "سجل الزبائن")
    PANEL.bind_var(_G, "CUSTOMER_ROLE_IDS", "ids", "رتب الزبائن (من الأعلى للأدنى)", kind="role",
                   help="أي عضو عنده وحدة منها ينحفظ بالسجل.")
    PANEL.bind_var(_G, "RESTORE_ROLES_ON_REJOIN", "bool", "ترجع الرتب للزبون إذا طلع ورجع دخل")

    PANEL.group("points", "النقاط والرتب")
    PANEL.bind_var(_G, "TEAM_ROLE_ID", "id", "رتبة التيم (أوامر النقاط)", kind="role", required=True)
    PANEL.bind_var(_G, "POINTS_DM_TEMPLATE", "longtext", "رسالة الخاص لما الزبون ياخذ نقاط",
                   help="{points} = النقاط اللي أخذها، {total} = مجموعه")
    PANEL.bind_var(_G, "RANK_UP_DM_TEMPLATE", "longtext", "رسالة الخاص لما يترقى رتبة")
    PANEL.bind(
        "RANK_THRESHOLDS", "list", "حدود الترقية (من الأقل للأكثر)", item_label="حد", title_field="points",
        fields=[F("role_id", "id", "الرتبة", kind="role"), F("points", "number", "النقاط المطلوبة")],
        get=lambda: RANK_THRESHOLDS,
        put=lambda v: _G.__setitem__("RANK_THRESHOLDS", sorted((r for r in v if r["role_id"]), key=lambda r: r["points"])),
    )
    PANEL.bind(
        "ROLE_TIERS", "list", "الرتب اللي تطلع بلوگ الإحصائيات", item_label="رتبة", title_field="label",
        fields=[F("id", "id", "الرتبة", kind="role"), F("label", "text", "الاسم باللوگ")],
        get=lambda: ROLE_TIERS, put=lambda v: _G.__setitem__("ROLE_TIERS", [r for r in v if r["id"]]),
    )

    PANEL.group("exchange", "أسعار البورصة",
                "لكل عملة: كم وحدة تقابل كم دولار، بالشراء (الزبون يشتري منك) وبالبيع (الزبون يبيع إلك). "
                "مثلاً اسيا: 10 وحدات بـ 7.5 دولار. نفس أمر /exchange_rate_update. الدولار ثابت وما ينكتب.")
    PANEL.bind(
        "EXCHANGE_RATES", "list", "العملات", item_label="عملة", title_field="label",
        fields=[
            F("key", "key", "المفتاح"),
            F("label", "text", "العملة"),
            F("buy_batch", "number", "الشراء: عدد الوحدات"),
            F("buy_price", "number", "الشراء: سعرها بالدولار"),
            F("sell_batch", "number", "البيع: عدد الوحدات"),
            F("sell_price", "number", "البيع: سعرها بالدولار"),
        ],
        get=lambda: EXCHANGE_RATES_ROWS, put=_exchange_put,
    )

    PANEL.group("rooms", "رومات اللوحات", "خلي الحقل فارغ حتى تلغي اللوحة.")
    PANEL.bind_var(_G, "LEADERBOARD_CHANNEL_ID", "id", "روم لوحة النقاط", kind="channel")
    PANEL.bind_var(_G, "STATS_LOG_CHANNEL_ID", "id", "روم لوگ الإحصائيات", kind="channel")
    PANEL.bind_var(_G, "EXCHANGE_CHANNEL_ID", "id", "روم البورصة", kind="channel")

    PANEL.group("post", "نشر المنتجات", "أمر النشر لأصحاب رتبة معينة: رسالتهم تبقى، وينزل منشور باسمهم وتحته نص ثابت.")
    PANEL.bind_var(_G, "PRODUCT_POST_ROLE_ID", "id", "الرتبة اللي تكدر تنشر", kind="role")
    PANEL.bind_var(_G, "PRODUCT_POST_FOOTER", "longtext", "النص اللي ينضاف بنهاية كل منشور")

    PANEL.group("welcome_card", "قالب صورة الترحيب",
                "إذا بدلت القالب بمقاس ثاني عدل الأرقام حتى صورة العضو واسمه يجون بمكانهم. الأرقام بالبكسل.")
    PANEL.bind_var(W, "TEMPLATE_PATH", "image", "صورة القالب", dir="", key="WELCOME_TEMPLATE", required=True)
    _welcome_number("WELCOME_CX", "مركز الدائرة - أفقي", lambda: W.CIRCLE_CENTER[0],
                    lambda v: setattr(W, "CIRCLE_CENTER", (v, W.CIRCLE_CENTER[1])))
    _welcome_number("WELCOME_CY", "مركز الدائرة - عمودي", lambda: W.CIRCLE_CENTER[1],
                    lambda v: setattr(W, "CIRCLE_CENTER", (W.CIRCLE_CENTER[0], v)))
    _welcome_number("WELCOME_R", "نصف قطر الدائرة", lambda: W.CIRCLE_RADIUS,
                    lambda v: setattr(W, "CIRCLE_RADIUS", max(8, v)))
    for index, label in enumerate(("مستطيل الاسم - يسار", "مستطيل الاسم - فوق", "مستطيل الاسم - يمين", "مستطيل الاسم - تحت")):
        def _put(v, index=index):
            box = list(W.NAME_BOX)
            box[index] = v
            W.NAME_BOX = tuple(box)
        _welcome_number(f"WELCOME_BOX{index}", label, lambda index=index: W.NAME_BOX[index], _put)
    _welcome_number("WELCOME_MAX", "أكبر حجم خط للاسم", lambda: W.NAME_MAX_SIZE,
                    lambda v: setattr(W, "NAME_MAX_SIZE", max(W.NAME_MIN_SIZE, v)))
    _welcome_number("WELCOME_MIN", "أصغر حجم خط للاسم", lambda: W.NAME_MIN_SIZE,
                    lambda v: setattr(W, "NAME_MIN_SIZE", max(8, min(W.NAME_MAX_SIZE, v))))

    PANEL.group("netflix", "اشتراكات نتفلكس",
                "كل حساب إله رسالة بروم اللوق وجواه لحد 5 مشتركين. اكتب مدة الاشتراك بالأيام وهي تنقص كل يوم لحالها، "
                "ومن تخلص توصلك رسالة بالخاص وتوصل للزبون إذا آيديه مكتوب. حتى تجدد اشتراك اكتب عدد الأيام الجديد.")
    PANEL.bind_var(_G, "NETFLIX_CHANNEL_ID", "id", "روم اللوق", kind="channel")
    PANEL.bind(
        "NETFLIX_ACCOUNTS", "list", "الحسابات", item_label="حساب", title_field="email",
        fields=[
            F("key", "key", "المفتاح"),
            F("email", "text", "إيميل الحساب"),
            F("password", "text", "الباسورد"),
            F("subscribers", "list", "المشتركين (لحد 5)", item_label="مشترك", title_field="name",
              fields=[
                  F("key", "key", "المفتاح"),
                  F("name", "text", "اسم المشترك"),
                  F("days", "number", "مدة الاشتراك (أيام باقية)"),
                  F("username", "text", "يوزر الشخص (اختياري)"),
                  F("user_id", "id", "آيدي الشخص (اختياري)", kind="user"),
                  F("link", "text", "رابط صفحة الشخص (إذا موجود)"),
                  F("profile_name", "text", "اسم البروفايل"),
                  F("profile_user", "text", "يوزر البروفايل"),
              ]),
        ],
        get=lambda: NETFLIX_ACCOUNTS, put=lambda v: _G.__setitem__("NETFLIX_ACCOUNTS", v), load=_netflix_load,
    )
    PANEL.bind_var(_G, "NETFLIX_NOTIFY_IDS", "ids", "منو توصله رسالة انتهاء الاشتراك", kind="user",
                   help="فارغ = صاحب السيرفر. نفس الأشخاص لنتفلكس وكرانشي رول.")
    PANEL.bind_var(_G, "NETFLIX_OWNER_DM", "longtext", "الرسالة اللي توصلك من يخلص اشتراك",
                   help="{name} = اسم المشترك، {email} = إيميل الحساب، {user} = الزبون، {profile} = البروفايل")
    PANEL.bind_var(_G, "NETFLIX_CUSTOMER_DM", "longtext", "الرسالة اللي توصل للزبون من يخلص اشتراكه",
                   help="{name} = اسمه، {profile} = البروفايل. خليها فارغة حتى ما توصله رسالة.")

    PANEL.group("crunchy", "اشتراكات كرانشي رول",
                "كل حساب إله رسالة بروم اللوق وجواه لحد 5 مشتركين. اكتب مدة الاشتراك بالأيام وهي تنقص كل يوم لحالها، "
                "ومن تخلص توصلك رسالة بالخاص وتوصل للزبون إذا آيديه مكتوب. حتى تجدد اشتراك اكتب عدد الأيام الجديد.")
    PANEL.bind_var(_G, "CRUNCHY_CHANNEL_ID", "id", "روم اللوق", kind="channel")
    PANEL.bind(
        "CRUNCHY_ACCOUNTS", "list", "الحسابات", item_label="حساب", title_field="email",
        fields=[
            F("key", "key", "المفتاح"),
            F("email", "text", "إيميل الحساب"),
            F("password", "text", "الباسورد"),
            F("subscribers", "list", "المشتركين (لحد 5)", item_label="مشترك", title_field="name",
              fields=[
                  F("key", "key", "المفتاح"),
                  F("name", "text", "اسم المشترك"),
                  F("days", "number", "مدة الاشتراك (أيام باقية)"),
                  F("username", "text", "يوزر الشخص (اختياري)"),
                  F("user_id", "id", "آيدي الشخص (اختياري)", kind="user"),
                  F("link", "text", "رابط صفحة الشخص (إذا موجود)"),
                  F("profile_name", "text", "اسم البروفايل"),
                  F("profile_user", "text", "يوزر البروفايل"),
              ]),
        ],
        get=lambda: CRUNCHY_ACCOUNTS, put=lambda v: _G.__setitem__("CRUNCHY_ACCOUNTS", v), load=_netflix_load,
    )
    PANEL.bind_var(_G, "CRUNCHY_OWNER_DM", "longtext", "الرسالة اللي توصلك من يخلص اشتراك",
                   help="{name} = اسم المشترك، {email} = إيميل الحساب، {user} = الزبون، {profile} = البروفايل")
    PANEL.bind_var(_G, "CRUNCHY_CUSTOMER_DM", "longtext", "الرسالة اللي توصل للزبون من يخلص اشتراكه",
                   help="{name} = اسمه، {profile} = البروفايل. خليها فارغة حتى ما توصله رسالة.")

    PANEL.group("help", "قائمة الأوامر", "أمر /view-commands يطلع كل أوامر البوتات لحاله ومرتبة. هنا تكدر تضيف ملاحظة تطلع بأوله.")
    PANEL.bind_var(_G, "HELP_EXTRA", "longtext", "ملاحظة إضافية بالقائمة", help="فارغ = ما تطلع.")

    PANEL.refresh = rebuild_lookups


# ---------------------------------------------------------------------------
# أسعار البورصة من الموقع
# ---------------------------------------------------------------------------
EXCHANGE_RATES_ROWS: list[dict] = []


def _exchange_put(rows):
    """يطبق أسعار الموقع على البورصة ويحفظها بنفس مكان أمر /exchange_rate_update."""
    update = {}
    for row in rows:
        key = row.get("key")
        nums = [row.get(k) or 0 for k in ("buy_batch", "buy_price", "sell_batch", "sell_price")]
        if key not in CURRENCIES or key == "usd":
            continue
        if any(n < 0 for n in nums):
            raise ValueError(f"أرقام «{row.get('label') or key}» لازم تكون موجبة")
        if not all(nums):
            continue  # عملة سعرها بعده ما مكتوب كامل - تبقى على سعرها الحالي
        update[key] = {"buy_rate": nums[0] / nums[1], "sell_rate": nums[2] / nums[3]}
    _G["EXCHANGE_RATES_ROWS"] = rows
    if update:
        CURRENCY_RATES_CACHE.update(update)
        CURRENCY_RATES_DOC.set(update, merge=True)


async def seed_exchange_rows():
    """أول مرة: يعبي خانات الموقع بالأسعار الحالية حتى ما تطلع فارغة."""
    have = {r.get("key") for r in EXCHANGE_RATES_ROWS}
    rows = [dict(r) for r in EXCHANGE_RATES_ROWS]
    for key, info in CURRENCIES.items():
        if key == "usd" or key in have:
            continue
        rate = CURRENCY_RATES_CACHE.get(key)
        batch = 10 if key in ("asia", "mc") else 1
        rows.append({
            "key": key, "label": info["label"],
            "buy_batch": batch if rate else 0, "buy_price": round(batch / rate["buy_rate"], 4) if rate else 0,
            "sell_batch": batch if rate else 0, "sell_price": round(batch / rate["sell_rate"], 4) if rate else 0,
        })
    if len(rows) != len(EXCHANGE_RATES_ROWS):
        _G["EXCHANGE_RATES_ROWS"] = rows
        await asyncio.to_thread(PANEL.write_value, "EXCHANGE_RATES", rows)


# ---------------------------------------------------------------------------
# لوق اشتراكات نتفلكس (البيانات تنكتب من الموقع)
# ---------------------------------------------------------------------------
NETFLIX_CHANNEL_ID = 1556090809584062526
NETFLIX_ACCOUNTS: list[dict] = []
NETFLIX_NOTIFY_IDS: set[int] = {975130270611165234}  # منو توصله رسالة انتهاء الاشتراك
NETFLIX_OWNER_DM = """**خلص اشتراك نتفلكس**
المشترك: {name}
الزبون: {user}
الحساب: `{email}`
البروفايل: {profile}"""
NETFLIX_CUSTOMER_DM = "** هلا {name}، خلص اشتراكك بنتفلكس (بروفايل {profile}). إذا تريد تجدد افتح تذكرة <:tdll:1393611890818814083> **"
NETFLIX_MAX_SUBSCRIBERS = 5

# لوق كرانشي رول - نفس نظام نتفلكس بالضبط، بس بروم وحسابات ثانية
CRUNCHY_CHANNEL_ID = None  # يتحدد من الموقع
CRUNCHY_ACCOUNTS: list[dict] = []
CRUNCHY_OWNER_DM = """**خلص اشتراك كرانشي رول**
المشترك: {name}
الزبون: {user}
الحساب: `{email}`
البروفايل: {profile}"""
CRUNCHY_CUSTOMER_DM = "** هلا {name}، خلص اشتراكك بكرانشي رول (بروفايل {profile}). إذا تريد تجدد افتح تذكرة <:tdll:1393611890818814083> **"

# كل خدمة: (المفتاح، الاسم، بادئة متغيراتها)
SUB_SERVICES = {
    "netflix": {"title": "نتفلكس", "prefix": "NETFLIX"},
    "crunchy": {"title": "كرانشي رول", "prefix": "CRUNCHY"},
}
_netflix_lock = asyncio.Lock()


def _svc(service: str, name: str):
    return globals()[f"{SUB_SERVICES[service]['prefix']}_{name}"]


def _netflix_load(rows):
    seen = set()
    for account in rows:
        if not account.get("key") or account["key"] in seen:
            raise ValueError("كل حساب لازم إله مفتاح خاص بيه")
        seen.add(account["key"])
        subs = account.get("subscribers") or []
        if len(subs) > NETFLIX_MAX_SUBSCRIBERS:
            raise ValueError(f"الحساب «{account.get('email') or '؟'}» بيه أكثر من {NETFLIX_MAX_SUBSCRIBERS} مشتركين")
        for sub in subs:
            if not sub.get("key") or sub["key"] in seen:
                raise ValueError("كل مشترك لازم إله مفتاح خاص بيه")
            seen.add(sub["key"])
            days = sub.get("days") or 0
            if days < 0 or days > 3650:
                raise ValueError(f"مدة اشتراك «{sub.get('name') or '؟'}» لازم بين 0 و 3650 يوم")
            sub["days"] = int(days)
    return rows


SUB_COLORS = {"netflix": 0xE50914, "crunchy": 0xF47521}


def netflix_embed(account: dict, state: dict, title: str = "نتفلكس", service: str = "netflix") -> discord.Embed:
    """رسالة الحساب بلوق الاشتراكات: امبيد مرتب، كل مشترك بخانة."""
    subs = account.get("subscribers") or []
    active = sum(1 for s in subs if s.get("days", 0) > 0)
    embed = discord.Embed(
        title=f"اشتراك {title}",
        description=(f"📧 **الإيميل:** `{account.get('email') or '—'}`\n"
                     f"🔑 **الباسورد:** `{account.get('password') or '—'}`"),
        color=SUB_COLORS.get(service, 0x2B2D31),
    )
    if not subs:
        embed.add_field(name="المشتركين", value="ماكو مشتركين بعد.", inline=False)
    for i, sub in enumerate(subs, start=1):
        entry = state.get(sub["key"]) or {}
        end_at = entry.get("end_at")
        if sub["days"] <= 0:
            head = f"🔴 {i}. {sub.get('name') or 'بدون اسم'}"
            lines = ["**خلص الاشتراك**"]
        else:
            head = f"🟢 {i}. {sub.get('name') or 'بدون اسم'}"
            until = f" · ينتهي <t:{int(end_at.timestamp())}:D>" if isinstance(end_at, datetime) else ""
            lines = [f"⏳ باقي **{sub['days']}** يوم{until}"]
        if sub.get("username") or sub.get("user_id"):
            who = []
            if sub.get("username"):
                who.append(f"`{sub['username']}`")
            if sub.get("user_id"):
                who.append(f"<@{sub['user_id']}>")
            lines.append("👤 " + " · ".join(who))
        if sub.get("user_id"):
            lines.append(f"🆔 `{sub['user_id']}`")
        profile = " / ".join(x for x in (sub.get("profile_name"), sub.get("profile_user")) if x)
        if profile:
            lines.append(f"🎭 البروفايل: **{profile}**")
        if sub.get("link"):
            lines.append(f"🔗 [صفحة الزبون]({sub['link']})")
        embed.add_field(name=head[:256], value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text=f"المشتركين: {len(subs)}/{NETFLIX_MAX_SUBSCRIBERS} · الفعالين: {active}")
    return embed


async def netflix_notify(guild, account: dict, sub: dict, service: str = "netflix"):
    owner_dm, customer_dm = _svc(service, "OWNER_DM"), _svc(service, "CUSTOMER_DM")
    profile = " / ".join(x for x in (sub.get("profile_name"), sub.get("profile_user")) if x) or "—"
    name = sub.get("name") or "بدون اسم"
    user = f"<@{sub['user_id']}> (`{sub['user_id']}`)" if sub.get("user_id") else (sub.get("username") or "ما مسجل")
    customer_note = ""
    if sub.get("user_id") and customer_dm.strip():
        try:
            target = guild.get_member(sub["user_id"]) or await bot.fetch_user(sub["user_id"])
            await target.send(fill(customer_dm, name=name, profile=profile))
            customer_note = "\n(وصلت رسالة للزبون بالخاص)"
        except discord.HTTPException:
            customer_note = "\n(ما وصلت رسالة للزبون: خاصه مقفل أو مو موجود)"
    for owner_id in (NETFLIX_NOTIFY_IDS or {guild.owner_id}):
        try:
            owner = guild.get_member(owner_id) or await bot.fetch_user(owner_id)
            await owner.send(fill(owner_dm, name=name, email=account.get("email") or "—", user=user, profile=profile) + customer_note,
                             allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning(f"ماكدرت أرسل تنبيه انتهاء الاشتراك للآيدي {owner_id} (خاصه مقفل؟).")


async def netflix_refresh(service: str = "netflix"):
    """ينقص الأيام، يرسل تنبيه اللي خلص اشتراكه، ويحدث رسائل اللوق. ينادى كل ساعة
    وبعد أي تعديل من الموقع. نفس الدالة لكل الخدمات (نتفلكس، كرانشي رول)."""
    async with _netflix_lock:
        guild = bot.get_guild(GUILD_ID)
        if guild is None:
            return
        title, prefix = SUB_SERVICES[service]["title"], SUB_SERVICES[service]["prefix"]
        NETFLIX_ACCOUNTS, NETFLIX_CHANNEL_ID = _svc(service, "ACCOUNTS"), _svc(service, "CHANNEL_ID")
        NETFLIX_STATE_DOC = db.collection("dash").document(SERVER_KEY).collection("state").document(service)
        snap = await asyncio.to_thread(NETFLIX_STATE_DOC.get)
        saved = (snap.to_dict() or {}) if snap.exists else {}
        state, messages = dict(saved.get("subs") or {}), dict(saved.get("messages") or {})
        now = datetime.now(timezone.utc)
        accounts = [dict(a, subscribers=[dict(s) for s in a.get("subscribers") or []]) for a in NETFLIX_ACCOUNTS]
        days_changed, expired = False, []
        alive = set()
        for account in accounts:
            for sub in account["subscribers"]:
                alive.add(sub["key"])
                entry = state.get(sub["key"])
                if entry is None or entry.get("last_days") != sub["days"]:
                    # مشترك جديد أو إنت غيرت المدة من الموقع - العد يبدي من هسه
                    entry = {"end_at": now + timedelta(days=sub["days"]), "last_days": sub["days"],
                             "notified": sub["days"] <= 0 and entry is None}
                    if sub["days"] > 0:
                        entry["notified"] = False
                else:
                    left = max(0, -(-(entry["end_at"] - now).total_seconds() // 86400)) if isinstance(entry.get("end_at"), datetime) else sub["days"]
                    left = int(left)
                    if left != sub["days"]:
                        sub["days"], entry["last_days"] = left, left
                        days_changed = True
                if sub["days"] <= 0 and not entry.get("notified"):
                    entry["notified"] = True
                    expired.append((account, sub))
                state[sub["key"]] = entry
        state = {k: v for k, v in state.items() if k in alive}

        if days_changed:
            _G[f"{prefix}_ACCOUNTS"] = accounts
            await asyncio.to_thread(PANEL.write_value, f"{prefix}_ACCOUNTS", accounts)

        problem = None
        channel = await get_channel_safe(NETFLIX_CHANNEL_ID) if NETFLIX_CHANNEL_ID else None
        if channel is None and accounts:
            problem = f"روم لوق {title} ما محدد أو ما لگيته"
        keys = {a["key"] for a in accounts}
        for key in [k for k in messages if k not in keys]:
            await delete_offer_messages(messages.pop(key) or {})  # حساب انحذف - تنحذف رسالته
        if channel is not None:
            for account in accounts:
                embed = netflix_embed(account, state, title, service)
                digest = hashlib.sha1(json.dumps(embed.to_dict(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                entry = messages.get(account["key"]) or {}
                if entry.get("message_id") and entry.get("channel_id") != str(channel.id):
                    await delete_offer_messages(entry)
                    entry = {}
                try:
                    message = None
                    if entry.get("message_id"):
                        try:
                            message = await channel.fetch_message(int(entry["message_id"]))
                        except (discord.NotFound, ValueError):
                            message = None
                    if message is None:
                        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    elif message.content or entry.get("hash") != digest:
                        # رسالة قديمة (نص) تتحول لامبيد، أو تغيرت المعلومات
                        await message.edit(content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    messages[account["key"]] = {"channel_id": str(channel.id), "message_id": str(message.id),
                                                "hash": digest}
                except discord.HTTPException as e:
                    problem = f"ماكدرت أكتب بروم لوق {title}: {e}"
        PANEL.note(service, problem)
        await asyncio.to_thread(NETFLIX_STATE_DOC.set, {"subs": state, "messages": messages, "updated_at": now})
        for account, sub in expired:
            await netflix_notify(guild, account, sub, service)


async def netflix_loop():
    while True:
        for service in SUB_SERVICES:
            try:
                await netflix_refresh(service)
            except Exception as e:
                log.warning(f"فشل تحديث لوق {SUB_SERVICES[service]['title']}: {e}")
        await asyncio.sleep(3600)


NETFLIX_SETTING_KEYS = {"NETFLIX_ACCOUNTS", "NETFLIX_CHANNEL_ID"}
CRUNCHY_SETTING_KEYS = {"CRUNCHY_ACCOUNTS", "CRUNCHY_CHANNEL_ID"}
OFFER_SETTING_KEYS = {"PRODUCTS", "OFFER_FIXED_TEXT", "DETAILS_BUTTON_LABEL", "LINE_FILENAME"}


async def on_panel_change(changed: set):
    """ينادى بعد ما تعديل من الموقع ينطبق - يحدث الرسائل المتأثرة."""
    guild = bot.get_guild(GUILD_ID)
    if changed & OFFER_SETTING_KEYS:
        register_offer_views()
        await ensure_offers()
    if "CUSTOMER_ROLE_IDS" in changed and guild is not None:
        await CUSTOMERS.scan(guild)
    if changed & NETFLIX_SETTING_KEYS:
        await netflix_refresh("netflix")
    if changed & CRUNCHY_SETTING_KEYS:
        await netflix_refresh("crunchy")
    if "LEADERBOARD_CHANNEL_ID" in changed:
        await update_leaderboard()
    if changed & {"STATS_LOG_CHANNEL_ID", "ROLE_TIERS"}:
        await update_stats_log()
    if "EXCHANGE_CHANNEL_ID" in changed:
        await ensure_exchange_panel()


# ---------------------------------------------------------------------------
# شات البوت من الموقع: تشوف الرومات وتقرا وتكتب وترسل صور باسم البوت
# الموقع يكتب طلباته بـ actions، والبوت يكتب الرسائل بـ console/{آيدي الروم}
# ---------------------------------------------------------------------------
CONSOLE_COL = db.collection("dash").document(SERVER_KEY).collection("console")
CONSOLE_FILES = db.collection("dash").document(SERVER_KEY).collection("console_files")
CONSOLE_WATCH_SECONDS = 150  # الموقع يأكد كل دقيقة إنه بعده فاتح الروم
CONSOLE_DOC_BYTES = 850_000  # حد حجم وثيقة Firestore (نخلي مجال)
CONSOLE_MENTIONS = discord.AllowedMentions(everyone=True, users=True, roles=True, replied_user=True)
_console_watch: dict[int, dict] = {}
_console_pending: dict[int, asyncio.Task] = {}
_console_send_lock = asyncio.Lock()  # الرسائل توصل بنفس ترتيب ما انكتبت


def _iso_size(data) -> int:
    return len(json.dumps(data, default=str, ensure_ascii=False).encode())


def _console_member_color(user) -> int:
    try:
        return user.color.value if isinstance(user, discord.Member) else 0
    except Exception:  # noqa: BLE001
        return 0


def _console_channels_data(guild: discord.Guild) -> dict:
    me = guild.me
    rows = []
    for ch in guild.channels:
        if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
            continue
        perms = ch.permissions_for(me)
        if not perms.view_channel:
            continue
        cat = ch.category
        rows.append({
            "id": str(ch.id), "name": ch.name, "type": ch.type.name,
            "cat": str(cat.id) if cat else "", "cat_name": cat.name if cat else "",
            "cat_pos": cat.position if cat else -1, "pos": ch.position,
            "voice": isinstance(ch, (discord.VoiceChannel, discord.StageChannel)),
            "send": perms.send_messages, "files": perms.attach_files, "manage": perms.manage_messages,
            "history": perms.read_message_history, "topic": (getattr(ch, "topic", None) or "")[:300],
        })
    for th in guild.threads:
        parent = th.parent
        if parent is None or th.archived:
            continue
        perms = th.permissions_for(me)
        if not perms.view_channel:
            continue
        cat = parent.category
        rows.append({
            "id": str(th.id), "name": th.name, "type": "thread", "thread_of": str(parent.id),
            "cat": str(cat.id) if cat else "", "cat_name": cat.name if cat else "",
            "cat_pos": cat.position if cat else -1, "pos": parent.position, "voice": False,
            "send": perms.send_messages_in_threads, "files": perms.attach_files, "manage": perms.manage_messages,
            "history": perms.read_message_history, "topic": "",
        })
    rows.sort(key=lambda r: (r["cat_pos"], r["voice"], r["pos"], r["type"] == "thread", r["id"]))
    emojis = [{"name": e.name, "id": str(e.id), "a": e.animated} for e in bot.emojis if e.available][:400]
    return {
        "channels": rows[:700], "emojis": emojis,
        "bot": {"id": str(bot.user.id), "name": me.display_name, "avatar": me.display_avatar.replace(size=64).url},
        "upload_limit": guild.filesize_limit, "updated_at": datetime.now(timezone.utc),
    }


def _console_embed(e: discord.Embed) -> dict:
    def url(proxy):
        return getattr(proxy, "url", None) or None
    return {
        "type": e.type or "rich", "title": (e.title or "")[:256], "url": e.url or None,
        "desc": (e.description or "")[:3000], "color": e.color.value if e.color else 0,
        "author": (e.author.name or "")[:256] if e.author else "", "author_icon": getattr(e.author, "icon_url", None) if e.author else None,
        "fields": [{"n": (f.name or "")[:256], "v": (f.value or "")[:1024], "i": bool(f.inline)} for f in e.fields[:25]],
        "image": url(e.image), "iw": getattr(e.image, "width", None), "ih": getattr(e.image, "height", None),
        "thumb": url(e.thumbnail), "video": url(e.video),
        "footer": (e.footer.text or "")[:2048] if e.footer else "",
    }


def _console_components(m: discord.Message) -> list:
    rows = []
    for row in m.components[:5]:
        items = []
        for c in getattr(row, "children", [])[:5]:
            if isinstance(c, discord.Button):
                items.append({"k": "b", "label": c.label or "", "emoji": str(c.emoji) if c.emoji else "",
                              "style": int(c.style.value), "url": c.url or None, "off": bool(c.disabled)})
            elif isinstance(c, discord.SelectMenu):
                items.append({"k": "s", "label": c.placeholder or "اختار..."})
        if items:
            rows.append({"items": items})
    return rows


def _console_msg(m: discord.Message, names: dict) -> dict:
    a = m.author
    for u in m.mentions:
        names[str(u.id)] = u.display_name
    text = m.content or ""
    system = ""
    if m.is_system() and m.type not in (discord.MessageType.default, discord.MessageType.reply):
        try:
            system = m.system_content or ""
        except Exception:  # noqa: BLE001
            system = m.type.name
    reply = None
    ref = m.reference
    if ref is not None and m.type == discord.MessageType.reply:
        r = ref.resolved
        if isinstance(r, discord.Message):
            names[str(r.author.id)] = r.author.display_name
            reply = {"id": str(r.id), "name": r.author.display_name, "color": _console_member_color(r.author),
                     "text": (r.content or ("[صورة/ملف]" if r.attachments else "[رسالة]"))[:160]}
        else:
            reply = {"id": str(ref.message_id or ""), "name": "", "text": "الرسالة الأصلية انحذفت"}
    return {
        "id": str(m.id), "uid": str(a.id), "name": a.display_name, "avatar": a.display_avatar.replace(size=64).url,
        "bot": a.bot, "color": _console_member_color(a), "mine": a.id == bot.user.id,
        "text": text[:4000], "system": system[:500], "at": m.created_at, "edited": m.edited_at is not None,
        "pinned": m.pinned, "reply": reply,
        "files": [{"url": f.url, "name": f.filename, "type": f.content_type or "", "size": f.size,
                   "w": f.width, "h": f.height, "spoiler": f.is_spoiler()} for f in m.attachments[:10]],
        "embeds": [_console_embed(e) for e in m.embeds[:5]],
        "stickers": [{"name": s.name, "url": s.url} for s in m.stickers[:3]],
        "buttons": _console_components(m),
        "reactions": [{"e": str(r.emoji), "n": r.count, "me": r.me} for r in m.reactions[:20]],
    }


def _console_watched(channel_id: int) -> bool:
    w = _console_watch.get(channel_id)
    return bool(w) and w["until"] > asyncio.get_running_loop().time()


def _console_set_watch(channel_id: int, limit: int | None = None):
    w = _console_watch.setdefault(channel_id, {"limit": 50, "until": 0})
    if limit:
        w["limit"] = max(20, min(int(limit), 300))
    w["until"] = asyncio.get_running_loop().time() + CONSOLE_WATCH_SECONDS
    # رومات ما احد فاتحها من مدة تنشال
    now = asyncio.get_running_loop().time()
    for cid in [c for c, v in _console_watch.items() if v["until"] < now - 600]:
        _console_watch.pop(cid, None)


async def _console_write(channel_id: int, data: dict):
    await asyncio.to_thread(CONSOLE_COL.document(str(channel_id)).set, data, merge=True)


async def console_refresh(channel_id: int):
    """يقرا آخر رسائل الروم ويكتبها للموقع."""
    limit = (_console_watch.get(channel_id) or {}).get("limit", 50)
    ch = await get_channel_safe(channel_id)
    base = {"channel_id": str(channel_id), "updated_at": datetime.now(timezone.utc), "limit": limit}
    if ch is None or not hasattr(ch, "history"):
        await _console_write(channel_id, {**base, "error": "البوت ما يشوف هذا الروم.", "messages": []})
        return
    try:
        msgs = [m async for m in ch.history(limit=limit)]
    except discord.Forbidden:
        await _console_write(channel_id, {**base, "error": "البوت ما عنده صلاحية يقرا رسائل هذا الروم.", "messages": []})
        return
    except discord.HTTPException as e:
        await _console_write(channel_id, {**base, "error": f"ديسكورد رفض: {e}"})
        return
    msgs.reverse()
    names: dict = {}
    rows = [_console_msg(m, names) for m in msgs]
    data = {**base, "error": "", "name": getattr(ch, "name", ""), "has_more": len(msgs) >= limit,
            "messages": rows, "names": names}
    while rows and _iso_size(data) > CONSOLE_DOC_BYTES:
        rows.pop(0)
        data["has_more"] = True
    await _console_write(channel_id, data)


def console_touch(channel_id: int):
    """صار تغيير بروم الموقع فاتحه - يحدث بعد لحظة (يجمع التغييرات السريعة)."""
    try:
        if not _console_watched(channel_id):
            return
    except RuntimeError:
        return
    task = _console_pending.get(channel_id)
    if task and not task.done():
        return

    async def later():
        await asyncio.sleep(0.7)
        _console_pending.pop(channel_id, None)
        try:
            await console_refresh(channel_id)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[شات الموقع] ماكدرت أحدث الروم {channel_id}: {e}")

    _console_pending[channel_id] = asyncio.create_task(later())


async def _console_on_message(message: discord.Message):
    console_touch(message.channel.id)


async def _console_on_raw(payload):
    console_touch(payload.channel_id)


for _event in ("on_raw_message_edit", "on_raw_message_delete", "on_raw_bulk_message_delete",
               "on_raw_reaction_add", "on_raw_reaction_remove", "on_raw_reaction_clear"):
    bot.add_listener(_console_on_raw, _event)
bot.add_listener(_console_on_message, "on_message")

_EMOJI_WORD = re.compile(r"(?<![<\w]):([A-Za-z0-9_]{2,32}):(?!\d)")


def console_emojis(text: str) -> str:
    """:اسم_الإيموجي: يتحول لإيموجي السيرفر (مثل ما يسوي ديسكورد بحسابك)."""
    if ":" not in text:
        return text
    lookup = {}
    for e in bot.emojis:
        if e.available:
            lookup.setdefault(e.name, e)
            lookup.setdefault(e.name.lower(), e)
    parts = re.split(r"(```[\s\S]*?```|`[^`\n]*`)", text)
    for i in range(0, len(parts), 2):
        parts[i] = _EMOJI_WORD.sub(lambda m: str(lookup.get(m.group(1)) or lookup.get(m.group(1).lower()) or m.group(0)), parts[i])
    return "".join(parts)


def _split_text(text: str, size: int = 2000) -> list[str]:
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def _console_files(specs: list) -> list[discord.File]:
    """الملفات اللي رفعها الموقع (مقسمة قطع بـ console_files)."""
    def read(spec):
        parts = []
        for i in range(int(spec.get("parts") or 0)):
            ref = CONSOLE_FILES.document(f"{spec['id']}-{i}")
            snap = ref.get()
            if not snap.exists:
                raise ValueError(f"الملف {spec.get('name')} ما وصل كامل، أعد رفعه")
            parts.append(bytes((snap.to_dict() or {}).get("data") or b""))
        return b"".join(parts)

    files = []
    for spec in specs[:10]:
        raw = await asyncio.to_thread(read, spec)
        name = re.sub(r"[\\/:*?\"<>|]", "_", str(spec.get("name") or "file"))[:100] or "file"
        if spec.get("spoiler") and not name.startswith("SPOILER_"):
            name = "SPOILER_" + name
        files.append(discord.File(io.BytesIO(raw), filename=name))
    return files


def _console_drop_files(specs: list):
    for spec in specs or []:
        for i in range(int(spec.get("parts") or 0)):
            try:
                CONSOLE_FILES.document(f"{spec['id']}-{i}").delete()
            except Exception:  # noqa: BLE001
                pass


def _console_clean_files():
    """قطع ملفات قديمة ما انرسلت (مثلاً البوت جان طافي) تنمسح."""
    try:
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        for snap in CONSOLE_FILES.stream():
            at = (snap.to_dict() or {}).get("at")
            if not isinstance(at, datetime) or at < old:
                snap.reference.delete()
    except Exception as e:  # noqa: BLE001
        log.warning(f"[شات الموقع] ماكدرت أنظف الملفات القديمة: {e}")


async def _console_message(ch, message_id) -> discord.Message:
    return await ch.fetch_message(int(message_id))


async def console_action(name: str, data: dict):
    """طلبات شات الموقع."""
    guild = bot.get_guild(GUILD_ID)
    if name == "console_channels":
        if guild is not None:
            await asyncio.to_thread(CONSOLE_COL.document("_channels").set, _console_channels_data(guild))
        await asyncio.to_thread(_console_clean_files)
        return
    channel_id = int(data.get("channel_id") or 0)
    if not channel_id:
        return
    if name in ("console_open", "console_ping"):
        fresh = not _console_watched(channel_id)
        _console_set_watch(channel_id, data.get("limit"))
        if name == "console_open" or fresh:
            await console_refresh(channel_id)
        return

    async with _console_send_lock:
        result = await _console_do(name, data, channel_id)
    if result is None:
        return
    await _console_write(channel_id, {"last_send": result})
    if result["ok"]:
        _console_set_watch(channel_id)
        console_touch(channel_id)


async def _console_do(name: str, data: dict, channel_id: int):
    nonce = str(data.get("nonce") or "")
    result = {"nonce": nonce, "ok": True, "error": "", "at": datetime.now(timezone.utc)}
    try:
        ch = await get_channel_safe(channel_id)
        if ch is None or not hasattr(ch, "send"):
            raise ValueError("البوت ما يشوف هذا الروم")
        if name == "console_send":
            text = console_emojis(str(data.get("text") or "").strip())
            specs = list(data.get("files") or [])
            try:
                files = await _console_files(specs)
            finally:
                await asyncio.to_thread(_console_drop_files, specs)
            if not text and not files:
                raise ValueError("الرسالة فارغة")
            chunks = _split_text(text) or [""]
            ref = None
            if data.get("reply_to"):
                ref = discord.MessageReference(message_id=int(data["reply_to"]), channel_id=channel_id,
                                               fail_if_not_exists=False)
            mentions = CONSOLE_MENTIONS if data.get("ping_reply", True) else discord.AllowedMentions(
                everyone=True, users=True, roles=True, replied_user=False)
            for i, chunk in enumerate(chunks):
                last = i == len(chunks) - 1
                await ch.send(content=chunk or None, files=files if last and files else discord.utils.MISSING,
                              reference=ref if i == 0 and ref else discord.utils.MISSING,
                              allowed_mentions=mentions, silent=bool(data.get("silent")))
        elif name == "console_edit":
            msg = await _console_message(ch, data["message_id"])
            if msg.author.id != bot.user.id:
                raise ValueError("تكدر تعدل بس رسائل البوت")
            text = console_emojis(str(data.get("text") or "").strip())
            if not text and not msg.attachments and not msg.embeds:
                raise ValueError("الرسالة فارغة")
            await msg.edit(content=text[:2000] or None)
        elif name == "console_delete":
            msg = await _console_message(ch, data["message_id"])
            await msg.delete()
        elif name == "console_react":
            msg = await _console_message(ch, data["message_id"])
            emoji = str(data.get("emoji") or "").strip()
            mine = any(str(r.emoji) == emoji and r.me for r in msg.reactions)
            if mine:
                await msg.remove_reaction(emoji, bot.user)
            else:
                await msg.add_reaction(emoji)
        elif name == "console_pin":
            msg = await _console_message(ch, data["message_id"])
            await (msg.unpin() if msg.pinned else msg.pin())
        else:
            return None
    except discord.Forbidden:
        result.update(ok=False, error="البوت ما عنده صلاحية لهذا الشي بهذا الروم")
    except discord.NotFound:
        result.update(ok=False, error="الرسالة ما موجودة (يمكن انحذفت)")
    except discord.HTTPException as e:
        too_big = getattr(e, "status", 0) == 413 or getattr(e, "code", 0) == 40005
        result.update(ok=False, error="الملف أكبر من المسموح بهذا السيرفر" if too_big else f"ديسكورد رفض: {e.text or e}")
    except Exception as e:  # noqa: BLE001
        result.update(ok=False, error=str(e) or "صار خطأ")
    return result


async def on_panel_action(name: str, _data: dict):
    """أزرار الموقع: إعادة فحص الزبائن / إعادة تحديث رسائل العروض / شات البوت."""
    guild = bot.get_guild(GUILD_ID)
    if name.startswith("console_"):
        await console_action(name, _data or {})
    elif name == "rescan_customers" and guild is not None:
        await asyncio.to_thread(publish_guild_info, db, SERVER_KEY, guild)
        await CUSTOMERS.scan(guild)
    elif name == "refresh_offers":
        register_offer_views()
        await ensure_offers()


SITE_SYNC_HOURS = 3
_site_link_started = False


async def site_sync_loop():
    """كل كم ساعة: ينشر أسماء الرومات والرتب للموقع ويفحص الزبائن كاملين."""
    first = True
    while True:
        guild = bot.get_guild(GUILD_ID)
        if guild is not None:
            try:
                await asyncio.to_thread(publish_guild_info, db, SERVER_KEY, guild)
                await CUSTOMERS.scan(guild)
                if first and REVIEWS_CHANNEL_ID is not None:
                    channel = await get_channel_safe(REVIEWS_CHANNEL_ID)
                    if channel is not None:
                        await REVIEWS.backfill(channel)
                first = False
            except Exception as e:
                log.warning(f"فشل تحديث بيانات الموقع: {e}")
        await asyncio.sleep(SITE_SYNC_HOURS * 3600 if guild is not None else 60)


def start_site_link():
    global _site_link_started
    if _site_link_started:
        return
    _site_link_started = True
    PANEL.on_change = on_panel_change
    PANEL.on_action = on_panel_action
    PANEL.start(bot.loop)
    bot.loop.create_task(site_sync_loop())
    bot.loop.create_task(netflix_loop())


_setup_panel()
rebuild_lookups()
PANEL.load_now()  # يطبق التعديلات المحفوظة من الموقع قبل ما البوت يبدي


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
