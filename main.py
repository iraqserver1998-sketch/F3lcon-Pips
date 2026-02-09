import logging
import asyncio
import cloudscraper
import pytz
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp

# ================= إعدادات البوت =================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
CHANNEL_ID = "@falcon_pips"

# توقيت بغداد
BAGHDAD_TZ = pytz.timezone('Asia/Baghdad')

# ================= اللوج =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= المتغيرات العامة =================
NOTIFIED_NEWS = set()
PRE_ALERT_NEWS = set()  # لتخزين الأخبار المنبهة عنها قبل 30 دقيقة

# ================= دوال التحليل والترجمة =================

def clean_number(text):
    """تحويل الأرقام من نص"""
    if not text: 
        return None
    text = text.replace(',', '').replace('%', '').strip()
    multiplier = 1
    
    if 'K' in text:
        multiplier = 1000
        text = text.replace('K', '')
    elif 'M' in text:
        multiplier = 1000000
        text = text.replace('M', '')
    elif 'B' in text:
        multiplier = 1000000000
        text = text.replace('B', '')
    
    try:
        return float(text) * multiplier
    except ValueError:
        return None

def analyze_impact(event_name, actual, forecast, impact_str):
    """
    تحليل تأثير الخبر على الذهب
    القاعدة: إيجابي للدولار = سلبي للذهب
    """
    if actual is None or forecast is None:
        return "⚪️ النتيجة متعادلة أو غير واضحة."

    # الأخبار ذات العلاقة العكسية بالدولار
    reverse_logic = any(x in event_name.lower() for x in 
                       ['unemployment', 'jobless', 'budget deficit', 'trade deficit'])
    
    diff = actual - forecast
    
    if diff == 0:
        return "⚪️ النتيجة طابقت التوقعات (تأثير محايد)."

    usd_positive = (diff > 0) if not reverse_logic else (diff < 0)
    
    if usd_positive:
        return f"🇺🇸 **إيجابي للدولار** (أفضل من المتوقع)\n📉 **سلبي للذهب - هبوط محتمل ⬇️**"
    else:
        return f"🇺🇸 **سلبي للدولار** (أسو�� من المتوقع)\n📈 **إيجابي للذهب - صعود محتمل ⬆️**"

def get_impact_emoji(impact_level):
    """إرجاع الإيموجي حسب قوة التأثير"""
    if impact_level == "High":
        return "🔴"  # أحمر - تأثير عالي
    elif impact_level == "Medium":
        return "🟠"  # برتقالي - تأثير متوسط
    else:
        return "🟡"  # أصفر - تأثير منخفض

# ================= دوال السكرابينج =================

def get_forex_news():
    """سحب أخبار Forex Factory لليوم الحالي"""
    scraper = cloudscraper.create_scraper()
    url = "https://www.forexfactory.com/calendar?day=today"
    
    try:
        response = scraper.get(url, timeout=10)
        if response.status_code != 200:
            logger.error("فشل الاتصال بموقع Forex Factory")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', class_='calendar__table')
        
        if not table:
            return []

        news_list = []
        rows = table.find_all('tr', class_='calendar__row')

        for row in rows:
            try:
                # استخراج العملة
                currency_cell = row.find('td', class_='calendar__currency')
                currency = currency_cell.text.strip() if currency_cell else ""
                
                # نركز على USD فقط
                if currency != 'USD':
                    continue

                # استخراج قوة الخبر
                impact_cell = row.find('td', class_='calendar__impact')
                impact_span = impact_cell.find('span') if impact_cell else None
                impact_class = impact_span.get('class', []) if impact_span else []
                
                impact_level = "Low"
                if any('high' in str(c).lower() for c in impact_class):
                    impact_level = "High"
                elif any('medium' in str(c).lower() for c in impact_class):
                    impact_level = "Medium"
                else:
                    continue

                # استخراج الوقت
                time_cell = row.find('td', class_='calendar__time')
                time_str = time_cell.text.strip() if time_cell else ""
                
                # استخراج اسم الخبر
                event_cell = row.find('td', class_='calendar__event')
                event_name = event_cell.text.strip() if event_cell else "Economic News"

                # استخراج الأرقام
                actual_cell = row.find('td', class_='calendar__actual')
                forecast_cell = row.find('td', class_='calendar__forecast')
                previous_cell = row.find('td', class_='calendar__previous')
                
                actual_val = clean_number(actual_cell.text) if actual_cell else None
                forecast_val = clean_number(forecast_cell.text) if forecast_cell else None
                actual_txt = actual_cell.text.strip() if actual_cell else "-"
                forecast_txt = forecast_cell.text.strip() if forecast_cell else "-"
                previous_txt = previous_cell.text.strip() if previous_cell else "-"

                news_item = {
                    'id': row.get('data-eventid', str(hash(event_name))),
                    'time': time_str,
                    'currency': currency,
                    'event': event_name,
                    'impact': impact_level,
                    'actual': actual_val,
                    'forecast': forecast_val,
                    'actual_txt': actual_txt,
                    'forecast_txt': forecast_txt,
                    'previous_txt': previous_txt
                }
                news_list.append(news_item)

            except Exception as e:
                logger.debug(f"خطأ في معالجة صف: {e}")
                continue
        
        return news_list

    except Exception as e:
        logger.error(f"خطأ في السكرابينج: {e}")
        return []

# ================= وظائف البوت المجدولة =================

async def send_msg(text, parse_mode=ParseMode.MARKDOWN):
    """إرسال رسالة للتليجرام"""
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHANNEL_ID, 
            text=text, 
            parse_mode=parse_mode
        )
        logger.info("رسالة أرسلت بنجاح")
    except Exception as e:
        logger.error(f"خطأ في إرسال الرسالة: {e}")

async def send_session_alert():
    """تنبيهات افتتاح الجلسات"""
    now = datetime.now(BAGHDAD_TZ)
    current_time = now.strftime("%H:%M")
    
    sessions = {
        "09:00": {
            "name": "جلسة آسيا (Tokyo/Sydney) 🇯🇵🇦🇺",
            "emoji": "🌅",
            "description": "بداية اليوم التجاري، تحضر للحركة"
        },
        "13:00": {
            "name": "جلسة أوروبا (London) 🇬🇧",
            "emoji": "🌍",
            "description": "جلسة قوية - السيولة تبدأ بالارتفاع"
        },
        "20:00": {
            "name": "جلسة نيويورك (New York) 🇺🇸",
            "emoji": "🔥",
            "description": "أقوى الجلسات - السيولة في الذروة!"
        }
    }
    
    if current_time in sessions:
        session = sessions[current_time]
        msg = f"""
{session['emoji']} **تنبيه الجلسات**

🚀 تم افتتاح **{session['name']}**

💬 {session['description']}

⚠️ انتبه لتحركات الذهب والدولار!

━━━━━━━━━━━━━━━━━━━━━━
@falcon_pips 📊
"""
        await send_msg(msg)

async def pre_alert_news(news_item, minutes_before=30):
    """
    تنبيه قبل صدور الخبر بـ 30 دقيقة
    ملاحظة: يتطلب معالجة وقت دقيقة
    """
    try:
        # هذا الجزء يحتاج معايرة دقيقة للوقت
        # سنستخدم معرف فريد للخبر لتجنب التكرار
        alert_id = f"{news_item['id']}_pre_alert"
        
        if alert_id in PRE_ALERT_NEWS:
            return
        
        msg = f"""
⏰ **تنبيه مسبق - خبر متوقع الصدور خلال 30 دقيقة**

📰 **الخبر:** {news_item['event']}
🕐 **الموعد المتوقع:** {news_item['time']} (توقيت بغداد)
🇺🇸 **التأثير:** {get_impact_emoji(news_item['impact'])} {news_item['impact']} Impact

🔮 **التوقع:** `{news_item['forecast_txt']}`
📊 **السابق:** `{news_item['previous_txt']}`

⚠️ **استعد للتحرك - قد تحدث فجوة سعرية!**

━━━━━━━━━━━━━━━━━━━━━━
@falcon_pips 📊
"""
        await send_msg(msg)
        PRE_ALERT_NEWS.add(alert_id)
        
    except Exception as e:
        logger.error(f"خطأ في التنبيه المسبق: {e}")

async def send_news_alert(news_item):
    """إرسال تنبيه عند صدور الخبر"""
    try:
        if news_item['id'] in NOTIFIED_NEWS:
            return
        
        if not news_item['actual_txt'] or news_item['actual_txt'] == '-':
            return

        analysis = analyze_impact(
            news_item['event'], 
            news_item['actual'], 
            news_item['forecast'],
            news_item['impact']
        )
        
        icon = get_impact_emoji(news_item['impact'])
        
        msg = f"""
{icon} **عاجل: صدور نتائج اقتصادية**

📰 **الخبر:** {news_item['event']}
🇺🇸 **العملة:** {news_item['currency']}
📊 **التأثير:** {news_item['impact']} Impact

━━━━━━━━━━━━━━━━━━━━━━

📈 **الحالي:** `{news_item['actual_txt']}`
🔮 **المتوقع:** `{news_item['forecast_txt']}`
📊 **السابق:** `{news_item['previous_txt']}`

━━━━━━━━━━━━━━━━━━━━━━

💡 **التحليل الفوري:**
{analysis}

🎯 **التوصية:** تابع حركة الذهب بحذر!

━━━━━━━━━━━━━━━━━━━━━━
@falcon_pips 📊
"""
        await send_msg(msg)
        NOTIFIED_NEWS.add(news_item['id'])
        logger.info(f"تنبيه مرسول للخبر: {news_item['event']}")

    except Exception as e:
        logger.error(f"خطأ في إرسال التنبيه: {e}")

async def market_watch_job():
    """الوظيفة الرئيسية: مراقبة السوق"""
    try:
        logger.info("🔍 جاري فحص الأخبار والأسواق...")
        news_data = await asyncio.to_thread(get_forex_news)
        
        for item in news_data:
            # إرسال التنبيهات
            await send_news_alert(item)
            # يمكن إضافة التنبيهات المسبقة هنا مع معايرة الوقت
            
    except Exception as e:
        logger.error(f"خطأ في market_watch_job: {e}")

# ================= التشغيل الرئيسي =================

async def main():
    """إعداد وتشغيل البوت والمجدول"""
    
    try:
        scheduler = AsyncIOScheduler(timezone=BAGHDAD_TZ)
        
        # فحص الأخبار كل دقيقة
        scheduler.add_job(market_watch_job, 'interval', minutes=1, id='market_watch')
        
        # فحص الجلسات كل دقيقة (في ساعات التشغيل فقط)
        scheduler.add_job(send_session_alert, 'cron', hour='9,13,20', minute='0', id='sessions')
        
        scheduler.start()
        logger.info("✅ البوت جاهز وقيد التشغيل...")
        logger.info("📊 @falcon_pips - قناة التداول الاحترافية")
        
        # إبقاء البوت يعمل
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"خطأ في البوت الرئيسي: {e}")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت")
    except Exception as e:
        logger.error(f"خطأ حرج: {e}")