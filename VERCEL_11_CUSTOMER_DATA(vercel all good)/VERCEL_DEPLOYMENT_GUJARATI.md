# Vercel Deployment (Gujarati)

આ આવૃત્તિ Flask + PostgreSQL (Neon) માટે તૈયાર કરવામાં આવી છે. Vercel પર SQLite ફાઇલ વાપરવામાં આવતી નથી.

## 1) Neon PostgreSQL બનાવો
1. https://neon.tech પર account બનાવો અને નવો PostgreSQL project બનાવો.
2. Neon માંથી pooled અથવા direct connection string copy કરો. તેમાં `postgresql://` અથવા `postgres://` હોવું જોઈએ.

## 2) GitHub માં અપલોડ
1. ZIP extract કરો.
2. આ ZIP ની અંદરની બધી ફાઇલો repository ના root માં મૂકો. `api/index.py`, `vercel.json`, `server.py`, `requirements.txt` અને `data/catalog.json` root structure મુજબ જ રહેવા જોઈએ.
3. GitHub પર commit/push કરો.

## 3) Vercel Environment Variables
Vercel → Project → Settings → Environment Variables માં નીચે ઉમેરો:

- `DATABASE_URL` = Neon connection string (Secret તરીકે રાખો)
- `MANAGER_ID` = તમારું manager login ID (ઉદાહરણ: tirth)
- `MANAGER_PASSWORD` = નવો મજબૂત password, ઓછામાં ઓછા 12 characters
- `MANAGER_NAME` = Manager નું display name (વૈકલ્પિક)
- `DEV_OTP` = `0`
- `GOOGLE_CLIENT_ID` = Google login વાપરતા હો તો જ
- `FAST2SMS_KEY` = વાસ્તવિક SMS OTP મોકલવો હોય તો જ

ખાસ: નવી Neon database માં manager row પ્રથમ વખત બનાવતી વખતે `MANAGER_PASSWORD` ફરજિયાત છે. તેને ક્યારેય GitHub માં ન મૂકો.

## 4) Deploy
Vercel → Deployments → Redeploy (અથવા GitHub માં push કરતાં auto deploy થશે).

## નોંધ / ડેટા
- `data/catalog.json` માંથી 5,602 catalogue items પહેલી વખત PostgreSQL માં seed થશે.
- આ source ZIP માં SQLite `.db` ફાઇલ નહોતી. તેથી અગાઉના live customer/order/manager database records આ ZIP માંથી migrate કરી શકાતા નથી. જો જૂની SQLite database મળે તો અલગ migration કરવી પડશે.
- PostgreSQL data Neon માં રહેશે; Vercel function filesystem પર નહીં.
- આ ZIP માં code-level migration અને deployment wiring છે. Neon connection string નાખ્યા વગર production runtime ચલાવી શકાતું નથી.
