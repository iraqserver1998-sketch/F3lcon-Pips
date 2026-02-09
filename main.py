import logging
import asyncio
import cloudscraper
import pytz
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= إعدادات البوت =================
BOT_TOKEN = "8450630765:AAG0oBdaYc9uZavkmEJdoNRXhOwL3ITdG38"
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

# ================= دوال مساعدة =================
def clean_number(text):
    if not text: 
        return None
    text = text.replace(',', '').replace('%', '').strip()
    multiplier = 1
    if 'K' in text: multiplier = 1000; text = text.replace('K','')
    if 'M' in text: multiplier = 1000000; text = text.replace('M','')
    if 'B' in text: multiplier = 1000000000; text = text.replace('B','')
    try: return float(text) * multiplier
    except ValueError: return None

def analyze_impact(event_name, actual, forecast, impact_str):
    if actual is None or forecast is None:
        return "⚪️ النتيجة متعادلة أو غير واضحة."
    reverse_logic = any(x in event_name.lower() for x in ['unemployment','jobless','budget deficit','trade deficit'])
    diff = actual - forecast
    if diff == 0:
        return "⚪️ النتيجة طابقت التوقعات (تأثير محايد)."
    usd_positive = (diff > 0) if not reverse_logic else (diff < 0)
    if usd_positive:
        return "🇺🇸 إيجابي للدولار\n📉 سلبي للذهب - هبوط محتمل ⬇️"
    else:
        return "🇺🇸 سلبي للدولار\n📈 إيجابي للذهب - صعود محتمل ⬆️"

def get_impact_emoji(impact_level):
    if impact_level.lower() == "high": return "🔴"
    if impact_level.lower() == "medium": return "🟠"
    return "🟡"

# ================= دوال السكرابينج =================
def get_forex_news():
    scraper = cloudscraper.create_scraper()
    url = "https://www.forexfactory.com/calendar?day=today"
    try:
        r = scraper.get(url, timeout=10)
        if r.status_code != 200: return []
        soup = BeautifulSoup(r.text, 'html.parser')
        table = soup.find('table', class_='calendar__table')
        if not table: return []
        news_list = []
        rows = table.find_all('tr', class_='calendar__row')
        for row in rows:
            try:
                currency = row.find('td', class_='calendar__currency')
                currency = currency.text.strip() if currency else ""
                if currency != "USD": continue

                impact_cell = row.find('td', class_='calendar__impact')
                impact_span = impact_cell.find('span') if impact_cell else None
                impact_class = impact_span.get('class', []) if impact_span else []
                impact_level = "Low"
                if any('high' in str(c).lower() for c in impact_class): impact_level="High"
                elif any('medium' in str(c).lower() for c in impact_class): impact_level="Medium"
                else: continue

                time_cell = row.find('td', class_='calendar__time')
                time_str = time_cell.text.strip() if time_cell else ""

                event_cell = row.find('td', class_='calendar__event')
                event_name = event_cell.text.strip() if event_cell else "Economic News"

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
                logger.debug(f"خطأ في صف: {e}")
        return news_list
    except Exception as e:
        logger.error(f"خطأ في السكرابينج: {e}")
        return []

# ================= وظائف البوت =================
bot_instance = Bot(token=BOT_TOKEN)

async def send_msg(text):
    try:
        await bot_instance.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN_V2)
        logger.info("رسالة أرسلت بنجاح")
    except Exception as e:
        logger.error(f"خطأ في إرسال الرسالة: {e}")

async def send_session_alert():
    now = datetime.now(BAGHDAD_TZ)
    sessions = {
        "09:00": ("جلسة آسيا 🇯🇵🇦🇺","🌅 بداية اليوم التجاري، تحضر للحركة"),
        "13:00": ("جلسة أوروبا 🇬🇧","🌍 جلسة قوية - السيولة تبدأ بالارتفاع"),
        "20:00": ("جلسة نيويورك 🇺🇸","🔥 أقوى الجلسات - السيولة في الذروة!")
    }
    current_time = now.strftime("%H:%M")
    if current_time in sessions:
        name, desc = sessions[current_time]
        msg = f"{name}\n{desc}\n⚠️ انتبه لتحركات الذهب والدولار!\n@falcon_pips"
        await send_msg(msg)

async def pre_alert_news(news_item, minutes_before=30):
    alert_id = f"{news_item['id']}_pre"
    if alert_id in PRE_ALERT_NEWS: return
    PRE_ALERT_NEWS.add(alert_id)
    msg = f"⏰ تنبيه مسبق - {news_item['event']} بعد {minutes_before} دقيقة\nتوقع: {news_item['forecast_txt']}"
    await send_msg(msg)

async def send_news_alert(news_item):
    if news_item['id'] in NOTIFIED_NEWS: return
    if not news_item['actual_txt'] or news_item['actual_txt'] == '-': return
    analysis = analyze_impact(news_item['event'], news_item['actual'], news_item['forecast'], news_item['impact'])
    icon = get_impact_emoji(news_item['impact'])
    msg = f"{icon} الخبر: {news_item['event']}\nالعملة: {news_item['currency']}\nالتأثير: {news_item['impact']}\nالحالي: {news_item['actual_txt']}\nالمتوقع: {news_item['forecast_txt']}\nالتحليل:\n{analysis}\n@falcon_pips"
    await send_msg(msg)
    NOTIFIED_NEWS.add(news_item['id'])

async def market_watch_job():
    news_data = await asyncio.to_thread(get_forex_news)
    for item in news_data:
        await send_news_alert(item)

# ================= التشغيل الرئيسي =================
async def main():
    scheduler = AsyncIOScheduler(timezone=BAGHDAD_TZ)
    scheduler.add_job(market_watch_job, 'interval', minutes=1)
    scheduler.add_job(send_session_alert, 'cron', hour='9,13,20', minute='0')
    scheduler.start()
    logger.info("✅ البوت جاهز وقيد التشغيل...")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت")
    except Exception as e:
        logger.error(f"خطأ حرج: {e}")
