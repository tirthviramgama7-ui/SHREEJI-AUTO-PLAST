ACTIVE GENUINE FIBER PARTS  (Customer website + Manager panel)
==============================================================
Customer: http://localhost:5000
Manager:  http://localhost:5000/manager

VS CODE / WINDOWS
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python server.py
(athva run_windows.bat par double-click)

MAC / LINUX
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python server.py

MANAGER LOGIN (local testing)
   Manager ID: tirth      Password: tirth@1234
   Manager phone number FIXED che: 9998040666 (profile ma badlay nahi)
   Login na thay to server bandh karo (Ctrl+C) ane:  python reset_manager.py   (athva reset_manager.bat)

CUSTOMER WEBSITE
 - Excel list na badha 5,602 items, have darek item sathe MRP (Price_list.xlsx mathi). Rate "UD" hoy tya "MRP: UD" batave, total ma gani nathi.
 - Front page upar SEARCH BAR: pehla 2 letter sachha j hova joiye (list ma koi pan word ni shuruat thi). Jem ke "SP" = side panel,
   "TP" = tail panel, "FM" = front mudguard. Pachi model lakho: "SP splendor". 3rd letter thi je letter sachho hoy te pramane best match batave.
 - Folder ni andar company na naam badlaya che: HERO=HIRA, HONDA=HODA, TVS=TSV, BAJAJ=BJJ, SUZUKI=SUJUKI, MAHINDRA=MHR, YAMAHA=YAHAMA
   (darek jagya e). Badlava hoy to static/customer.html ma DISP list.
 - Customer order place kare etle automatic CONFIRM. Customer ne Print/Download bill no option nathi.
 - Cart ma ane My Orders ma item sathe MRP, quantity ane total amount (MRP) dekhay che. Company/model subtitle kadhi nakhya.

MANAGER PANEL
 - Order automatic Confirmed thai ne manager ma aave che (confirm button nathi). Buttons: Delivered / Cancel.
 - Darek order ma Print ane Download (PDF) bill: Article No., Item, MRP, Qty. Ek page ma 25+ items aave. Contact ane Total Amount nathi, subtitle nathi.
 - Naya order ni awaaz + notification.

NAVA FEATURES (v8)
 CUSTOMER LOGIN
 - Customer mobile number (OTP) athva Google account thi login kare. Ek var login thay pachi logout na kare tya sudhi (1 varsh) login j rahe.
 - Login pachi "My Orders" ma teni badha orders (Confirmed / Delivered / Cancelled) dekhay. Order place karva login jaruri che (cart safe rahe).
 - Juna (login pehla na) orders aa device par hoy to login vakhte automatic account ma jodai jay.
 - Mobile OTP: DEV_OTP=1 hoy to OTP screen par batave (demo). Real SMS mate Fast2SMS key set karo:  FAST2SMS_KEY=xxxx  (ane DEV_OTP=0)
 - Google login mate: console.cloud.google.com > APIs & Services > Credentials > "OAuth client ID" (Web application).
   "Authorized JavaScript origins" ma tamari site no URL nakho (jem ke https://tamari-site.onrender.com ane http://localhost:5000).
   Pachi Client ID environment ma set karo:  GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com   (set na hoy to Google button dekhase nahi)
 MANAGER
 - Profile ma badha fields save thay (naam, shop naam, WhatsApp, email, address, GST, timing, bill note, dukan band no message). Logout/login pachi pan rahe.
 - Settings > "ID / Password badlo": current password sathe Manager ID ane password badlay. MANAGER_ID / MANAGER_PASSWORD environment have fakt PEHLI var
   (database banti vakhte) vapray che, etle profile mathi badlelo password restart pachi pan rahe. Bhuli jav to: python reset_manager.py
 - Products tab: customer ne dekhay che te badhi items search bar (customer jevo j: pehla 2 letter) sathe dekhay. Kai pan item delete thay (Excel ni pan) ane customer mathi pan gayab.
   "Add new product" thi navi item add / edit. Customer ne turant dekhay.
 - Customers: Dashboard ma Customers card athva Profile > My Customers. Search kari shako, customer par tap karo to teni details ane badha orders dekhay.
 - Dashboard: Chalu / Aaj na orders / Customers, order filter (Chalu, Delivered, Cancelled, Badha), order ma customer no phone/email.
 - Profile ma Business summary ane "My Customers" list.
 - IMPORTANT (hosting): DATA_DIR persistent disk par rakho, nahi to restart par customers, orders, products, profile jatu rahe.

NOTES
 - Items ma "company" Excel ma alag column nathi; model na naam parthi (SPL=Hero, ACTIVA=Honda, PULSAR=Bajaj,
   JUPITER=TVS vagere) automatic goth-vayu che. Search ma pan company/model naam thi item malse.
 - Excel ma 15 article numbers repeat thay che (jem ke 45000); te Excel pramane j batavya che, system potana internal id thi alag olkhe che.
 - Database: data/agap_v2.db (automatic banse). Juna version na orders data/agap.db ma safe che (navu system navi database vape che).
 - Hosting: gunicorn --workers 2 --threads 4 --bind 0.0.0.0:$PORT server:app
   Environment: MANAGER_ID, MANAGER_PASSWORD, DEV_OTP=0, DATA_DIR (persistent disk). Real SMS mate send_sms() ma gateway jodo.
 - Forgot password OTP: DEV_OTP=1 hoy tyare OTP screen par batave (demo).

VERCEL ERROR FIX / RECOMMENDED HOSTING
======================================
Aa project Flask + SQLite chhe. Vercel serverless runtime ma project folder read-only hoy chhe,
etle /var/task/... ma SQLite database create/open nathi thati. /tmp par database muksho to
server restart/redeploy pachi data permanent nahi rahe. Etle aa project mate persistent disk
sathe Render Web Service vapro.

RENDER DEPLOY
1. Aa ZIP extract kari ne agap folder ne GitHub repository ma upload/push karo.
2. Render dashboard > New > Blueprint par jao ane repository connect karo.
3. Render agap/render.yaml read kari service banavshe. Blueprint file agap folder ni andar hoy
   to Render ma Root Directory = agap set karo, athva render.yaml ne repository root ma mukho.
4. Service ma disk mount path /var/data ane DATA_DIR=/var/data set thayelu hovu joiye.
5. Deploy pachi service Environment ma MANAGER_PASSWORD ni generated value copy kari surakshit rakho.
   Manager ID tirth chhe; login password generated value hase. Pachi manager settings ma badli shako.
6. DEV_OTP=1 demo mode chhe: OTP app ma dekhase. Real public OTP login mate Fast2SMS configure karo
   ane DEV_OTP=0 set karo. DEV_OTP=1 public production ma rakhvu surakshit nathi.
7. Deploy complete thay pachi https://YOUR-SERVICE.onrender.com/ ane /manager kholo.

IMPORTANT
- Vercel par aa Flask + SQLite build ne direct chalavva thi 500 error aave chhe.
- SQLite data /var/data/agap_v2.db ma persistent disk par raheshe. Disk vagar deploy/restart pachi
  orders, customers, manager settings ane custom products gumai shake chhe.
- ZIP ma database file nathi; catalog.json mathi 5,602 catalog records first startup par load thase.
- Aa hosting setup local SQLite database migrate kartu nathi. Jo existing live site par orders/customers
  hoy to old database file no backup/export alag thi karvo.
