# Option Alert

מערכת התראות חינמית לחלוטין, שרצה בענן (GitHub Actions — לא נדרש מחשב דלוק), שסורקת
תנועות חריגות באופציות ובנפח מסחר עבור:

- 300 מניות ה-S&P 500 הגדולות ביותר לפי שווי שוק
- מתכות: GLD, SLV (ETF-ים)
- קריפטו: BTC, ETH (דרך Deribit)

ההתראות נשלחות לטלגרם בעברית, עם דוח יומי מסכם למייל. **זו מערכת מידע/התראות
בלבד — אין ביצוע מסחר אוטומטי בשום שלב.**

## איך זה עובד

- `scan.yml` רץ כל 10 דקות בשעות המסחר של NYSE, סורק שירד (1/6) מ-300 המניות
  בכל הרצה (כיסוי מלא כל שעה) + GLD/SLV/BTC/ETH בכל הרצה.
- ציון חריגות משוקלל 0-100 מ-3 מרכיבים: יחס Volume/Open-Interest, קפיצת
  Implied Volatility מול תנודתיות ריאלית, וגודל עסקה (נקוב $) כפרוקסי לבלוקים.
- ניקוד/ספים מוגדרים ב-[`src/optionalert/config.py`](src/optionalert/config.py) —
  כל הכיוונון נעשה שם, בלי לגעת בשאר הקוד.
- `eod_report.yml` שולח מייל יומי אחרי סגירת המסחר עם כל התנועות של היום + גרפים.
- `refresh_universe.yml` מרענן שבועית (שבת) את רשימת 300 המניות.
- **tvremix (אופציונלי)**: `yfinance` לא מזהה חוזים שלא נסחרו היום (מציג
  volume/IV ישנים בלי לסמן שהם ישנים — ראו `src/optionalert/data_equity.py`),
  אז ה-IV שלו לפעמים לא אמין. אם מוגדר `TVREMIX_API_KEY`, המערכת שולפת IV
  מאומת מ-tvremix (ה-MCP הרשמי של TradingView) לחוזה הבודד שכבר עומד
  להתריע, לפני השליחה בפועל — לא בשימוש ל-volume/OI (tvremix לא חושף את
  זה בכלל). בלי ה-secret הזה המערכת ממשיכה לעבוד בדיוק כמו קודם.

## הקמה חד-פעמית

1. **טלגרם**: שלחו הודעה כלשהי לבוט הקיים שלכם, ואז פתחו בדפדפן:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` וקראו את ה-`chat_id`.
2. **Google Cloud**: [console.cloud.google.com](https://console.cloud.google.com) →
   פרויקט חדש → הפעילו "Google Sheets API" → Credentials → Create Credentials →
   Service Account → צרו מפתח JSON → שמרו את הקובץ ואת כתובת המייל של ה-Service Account
   (נראית כמו `xxx@yyy.iam.gserviceaccount.com`).
3. **Google Sheet**: צרו גיליון חדש (ריק) → Share עם כתובת ה-Service Account
   כ-Editor → העתיקו את ה-Sheet ID מתוך ה-URL
   (`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`).
4. **Gmail App Password**: הפעילו אימות דו-שלבי בחשבון ה-Gmail → Security →
   App Passwords → צרו סיסמה עבור "Mail" (16 תווים).
5. **GitHub repo**: הפכו את הריפו לציבורי (מומלץ, כדי לקבל דקות Actions ללא
   הגבלה בחינם) → Settings → Secrets and variables → Actions → הוסיפו:

   | Secret | ערך |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | הטוקן מ-BotFather |
   | `TELEGRAM_CHAT_ID` | מהשלב 1 |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | כל תוכן קובץ ה-JSON מהשלב 2 |
   | `GOOGLE_SHEET_ID` | מהשלב 3 |
   | `EMAIL_ADDRESS` | כתובת ה-Gmail ששולחת/מקבלת את הדוח |
   | `EMAIL_APP_PASSWORD` | מהשלב 4 |
   | `TVREMIX_API_KEY` | אופציונלי — `tvremix.xyz` → Account → API Keys → צרו טוקן (`tvr_...`) |

6. הריצו ידנית את `refresh_universe.yml` (Actions tab → workflow → Run workflow)
   כדי ליצור את `data/universe_cache.json` לפני שמסתמכים על `scan.yml`.
7. הריצו ידנית את `scan.yml` עם `dry_run=true` כדי לבדוק שהכל עובד בלי לשלוח
   בפועל, ואז עם `tickers` קטן (למשל `AAPL,MSFT`) לפני הפעלת הלו"ז המלא.

## פיתוח מקומי

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v

# בדיקה ידנית (dry-run, בלי לשלוח כלום):
PYTHONPATH=src python -m optionalert.run_scan --dry-run --tickers AAPL,MSFT,GLD
```

## כיוונון אחרי שבוע ריצה

עיינו בטאב "History" בגיליון Google Sheets כדי לראות את התפלגות הציונים,
ועדכנו את `alert_score_threshold` (וכל שאר הפרמטרים) ב-
[`src/optionalert/config.py`](src/optionalert/config.py).

## מגבלות ידועות

- `yfinance` הוא ספרייה לא רשמית שגורדת את Yahoo Finance — עלולה להיחסם/להשתנות.
- אין מקור חינמי ל-IV היסטורי — ה-IV מושווה לתנודתיות ריאלית (Realized Vol) של
  הנכס הבסיסי, לא ל-IV היסטורי אמיתי.
- אין נתוני "tape" (עסקה-בעסקה) חינמיים — גודל עסקה מחושב מנפח כולל, לא מזיהוי
  עסקת בלוק בודדת.
- נפח המניה של "היום" מושווה לממוצע יומי מלא של 20 יום, כך שמוקדם ביום המסחר
  היחס עלול להיראות נמוך יותר ממה שהוא בפועל.
- שילוב tvremix (`src/optionalert/tvremix_client.py`) נכתב מול תיעוד ה-MCP
  שלהם (JSON-RPC מעל HTTP), לא נבדק מול מפתח אמיתי בפועל — נדרשת בדיקה חיה
  (`--dry-run --tickers`) אחרי הוספת `TVREMIX_API_KEY` כדי לוודא שהפרסור
  של התשובה תואם. נכשל בשקט (מדלג על השיפור) אם הפורמט לא תואם — לא מפיל
  את הסריקה.
