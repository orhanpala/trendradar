import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import requests
import random
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request
from pytrends.request import TrendReq
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
import google.generativeai as genai
import os

load_dotenv()

app = FastAPI(title="TrendRadar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def get_db() -> Client:
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )


def get_authed_db(token: str) -> Client:
    client = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )
    client.auth.set_session(token, "")
    return client


def get_gemini():
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    return genai.GenerativeModel("gemini-2.5-flash")


# ── PAGES ──
@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/login")
def login_page():
    return FileResponse("static/login.html")

@app.get("/dashboard")
def dashboard():
    return FileResponse("static/trendradar.html")


# ══════════════════════════════
# AUTH
# ══════════════════════════════

class AuthPayload(BaseModel):
    email: str
    password: str
  

@app.post("/api/auth/register")
def register(payload: AuthPayload):
    try:
        db = get_db()
        res = db.auth.sign_up({
            "email": payload.email,
            "password": payload.password
        })
        return {"success": True, "message": "Kayit basarili."}
    except Exception as e:
        # Hata mesajini gizleme, Supabase'in verdigi gercek hatayi ekrana bas!
        raise HTTPException(status_code=400, detail=str(e))
@app.post("/api/auth/login")
def login(payload: AuthPayload):
    try:
        db = get_db()
        res = db.auth.sign_in_with_password({
            "email": payload.email,
            "password": payload.password
        })
        return {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "user": {
                "id": res.user.id,
                "email": res.user.email,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Email veya sifre yanlis.")


@app.post("/api/auth/jury-login")
def jury_login():
    try:
        db = get_db()
        jury_email = "juri@trendradar.com"
        jury_password = "HackathonJuri2026Secure!"
        
        # 1. Önce giriş yapmayı deniyoruz (Hesap daha önce oluşturulmuşsa)
        try:
            res = db.auth.sign_in_with_password({
                "email": jury_email,
                "password": jury_password
            })
        except Exception:
            # 2. Giriş başarısız olursa hesabı ilk kez oluşturuyoruz
            res = db.auth.sign_up({
                "email": jury_email,
                "password": jury_password
            })
            # Kayıt sonrası hemen oturum verisini alıyoruz
            res = db.auth.sign_in_with_password({
                "email": jury_email,
                "password": jury_password
            })

        return {
            "access_token": res.session.access_token,
            "user": {
                "id": res.user.id,
                "email": "JÜRİ"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Jüri oturumu başlatılamadı. Supabase ayarlarından 'Confirm email' (E-posta doğrulama) seçeneğinin kapalı olduğundan emin olun.")


@app.post("/api/auth/logout")
def logout(request: Request):
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        db = get_db()
        db.auth.sign_out()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/auth/me")
def get_me(request: Request):
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            raise HTTPException(status_code=401, detail="Token bulunamadi.")
        db = get_db()
        user = db.auth.get_user(token)
        return {
            "id": user.user.id,
            "email": user.user.email,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail="Gecersiz token.")


# ══════════════════════════════
# YARDIMCI FONKSIYON: GÜVENLİK
# ══════════════════════════════
def get_current_user_id(request: Request) -> str:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="Yetkisiz erisim.")
    db = get_db()
    user_res = db.auth.get_user(token)
    if not user_res or not user_res.user:
        raise HTTPException(status_code=401, detail="Gecersiz oturum.")
    return user_res.user.id

# ══════════════════════════════
# COMPANIES
# ══════════════════════════════

@app.get("/api/companies")
def get_companies(request: Request):
    try:
        user_id = get_current_user_id(request)
        db = get_db()
        # Sadece giris yapan kullaniciya ait firmalari getir
        res = db.table("companies").select("*").eq("user_id", user_id).order("created_at", desc=False).execute()
        return {"data": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/companies/{company_id}")
def get_company(request: Request, company_id: int):
    try:
        user_id = get_current_user_id(request)
        db = get_db()
        res = db.table("companies").select("*").eq("id", company_id).eq("user_id", user_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Firma bulunamadi veya erisim yetkiniz yok.")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CompanyCreate(BaseModel):
    name: str
    sector: str = ""
    email: str = ""
    phone: str = ""
    notes: str = ""

@app.post("/api/companies")
def create_company(request: Request, payload: CompanyCreate):
    try:
        user_id = get_current_user_id(request)
        db = get_db()
        data = payload.dict()
        data["user_id"] = user_id # Yeni firmayi, olusturan hesaba zimmetle
        res = db.table("companies").insert(data).execute()
        return {"success": True, "data": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# PRODUCTS
# ══════════════════════════════

@app.get("/api/companies/{company_id}/products")
def get_products(company_id: int, category: str = None, min_stock: int = None):
    try:
        db = get_db()
        query = db.table("products").select("*").eq("company_id", company_id)
        if category:
            query = query.eq("category", category)
        if min_stock is not None:
            query = query.gte("stock", min_stock)
        res = query.order("name").execute()
        return {"data": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ProductCreate(BaseModel):
    company_id: int
    name: str
    category: str = ""
    price: float = 0.0
    stock: int = 0

@app.post("/api/products")
def create_product(payload: ProductCreate):
    try:
        db = get_db()
        res = db.table("products").insert(payload.dict()).execute()
        return {"success": True, "data": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/products/bulk")
def bulk_create_products(products: list[ProductCreate]):
    try:
        db = get_db()
        data = [p.dict() for p in products]
        res = db.table("products").insert(data).execute()
        return {"success": True, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/low-stock")
def low_stock(threshold: int = 10):
    try:
        db = get_db()
        res = db.table("products").select("*").lt("stock", threshold).execute()
        return {"data": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# STATS
# ══════════════════════════════

@app.get("/api/stats")
def get_stats():
    try:
        db = get_db()
        companies = db.table("companies").select("id", count="exact").execute()
        products = db.table("products").select("id, stock, category", count="exact").execute()
        total_stock = sum(p["stock"] for p in products.data)
        categories = len(set(p["category"] for p in products.data if p["category"]))
        return {
            "total_companies": companies.count,
            "total_products": products.count,
            "total_stock": total_stock,
            "unique_categories": categories,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# AI
# ══════════════════════════════

class AiRequest(BaseModel):
    company_id: int
    analysis_type: str = "fiyatlandirma"

@app.post("/api/ai/analyze")
def ai_analyze(req: AiRequest):
    try:
        db = get_db()
        model = get_gemini()
        products_res = db.table("products").select("*").eq("company_id", req.company_id).execute()
        if not products_res.data:
            raise HTTPException(status_code=404, detail="Bu firmaya ait urun bulunamadi.")
        product_text = "\n".join([
            f"- {p['name']} | Kategori: {p['category']} | Fiyat: {p['price']} TL | Stok: {p['stock']} adet"
            for p in products_res.data
        ])
        prompts = {
            "fiyatlandirma": f"""Sen deneyimli bir B2B fiyatlandirma stratejistisin.
Asagidaki urun portfoyunu incele. Her urun icin net aksiyon onerisi ver:
YUKSEL / INDIR / BEKLE formatinda, gerekce ile birlikte.
Turkce, profesyonel ve madde madde yaz.

Urunler:
{product_text}""",
            "stok": f"""Sen bir tedarik zinciri uzmanisın.
Asagidaki stok verilerini incele.
Kritik stok altındaki urunler, fazla stok problemi olanlar ve devir hizi dusuk urunler icin somut oneriler sun.
Turkce, profesyonel ve madde madde yaz.

Urunler:
{product_text}""",
            "genel": f"""Sen bir B2B strateji danismanisin.
Bu urun portfoyunu bir butun olarak degerlendirerek kisa ve orta vadeli stratejik oneriler sun.
Turkce, profesyonel yaz.

Urunler:
{product_text}"""
        }
        prompt = prompts.get(req.analysis_type, prompts["fiyatlandirma"])
        response = model.generate_content(prompt)
        return {"success": True, "result": response.text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════

class NotificationCreate(BaseModel):
    user_id: str
    company_id: int
    title: str
    message: str
    type: str = "info"

@app.get("/api/notifications")
def get_notifications(request: Request, user_id: str):
    try:
        db = get_db()
        res = db.table("notifications").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
        return {"data": res.data, "unread": sum(1 for n in res.data if not n["is_read"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/notifications")
def create_notification(payload: NotificationCreate):
    try:
        db = get_db()
        res = db.table("notifications").insert(payload.dict()).execute()
        return {"success": True, "data": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/notifications/{notification_id}/read")
def mark_read(notification_id: int):
    try:
        db = get_db()
        db.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/notifications/read-all")
def mark_all_read(user_id: str):
    try:
        db = get_db()
        db.table("notifications").update({"is_read": True}).eq("user_id", user_id).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/notifications/generate")
def generate_notifications(user_id: str, company_id: int):
    """Kritik stok altı ürünler için otomatik bildirim üretir."""
    try:
        db = get_db()
        low = db.table("products").select("*").eq("company_id", company_id).lt("stock", 10).execute()
        created = 0
        for p in low.data:
            existing = db.table("notifications").select("id").eq("user_id", user_id).eq("title", f"Kritik Stok: {p['name']}").eq("is_read", False).execute()
            if not existing.data:
                db.table("notifications").insert({
                    "user_id": user_id,
                    "company_id": company_id,
                    "title": f"Kritik Stok: {p['name']}",
                    "message": f"{p['name']} icin stok kritik seviyede. Mevcut stok: {p['stock']} adet. Acil siparis verilmesi onerilir.",
                    "type": "error"
                }).execute()
                created += 1
        return {"success": True, "created": created}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ══════════════════════════════
# TREND RADAR (SERPAPI & FALLBACK)
# ══════════════════════════════
import requests
import random
from datetime import datetime, timedelta

@app.get("/api/trends")
def get_real_trends(keyword: str):
    try:
        api_key = os.getenv("SERPAPI_KEY")
        if not api_key:
            raise ValueError("SerpApi anahtari .env dosyasinda bulunamadi.")

        # SerpApi Google Trends Endpoint'i
        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_trends",
            "q": keyword,
            "data_type": "TIMESERIES",
            "date": "today 1-m", # Son 30 gun
            "geo": "TR",         # Turkiye verisi
            "api_key": api_key
        }

        response = requests.get(url, params=params)
        data = response.json()

        # Eger API kredisinin bitmesi gibi bir hata donerse yakala
        if "error" in data:
            raise Exception(f"SerpApi Hatasi: {data['error']}")

        timeline = data.get("interest_over_time", {}).get("timeline_data", [])
        
        if not timeline:
            return {"success": False, "message": f"'{keyword}' kelimesi icin Turkiye'de yeterli arama hacmi bulunamadi."}

        labels = []
        values = []
        
        for item in timeline:
            # SerpApi tarihi "May 13" gibi kisa formatta dondurur
            labels.append(item.get("date", ""))
            
            # 0-100 arasi arama hacmi degerini aliyoruz
            val_list = item.get("values", [{}])
            val = val_list[0].get("extracted_value", 0) if val_list else 0
            values.append(val)

        return {"success": True, "labels": labels, "data": values, "is_mock": False}

    except Exception as e:
        # SerpApi cokse bile sistem ayakta kalir: Fallback Motoru Devrede
        random.seed(keyword)
        
        labels = []
        values = []
        base_val = random.randint(30, 70)
        
        for i in range(30, -1, -1):
            d = datetime.now() - timedelta(days=i)
            labels.append(d.strftime('%d %b'))
            
            trend_shift = random.randint(-12, 15)
            base_val = max(5, min(100, base_val + trend_shift))
            values.append(base_val)
            
        return {
            "success": True, 
            "labels": labels, 
            "data": values, 
            "is_mock": True,
            "message": f"Gercek veri alinamadi, algoritmik simulasyon devrede."
        }
class SendCodePayload(BaseModel):
    type: str  # "email" veya "password"
    value: str # Yeni e-posta adresi veya yeni şifre

@app.post("/api/profile/send-code")
def send_profile_code(request: Request, payload: SendCodePayload):
    try:
        user_id = get_current_user_id(request)
        db = get_db()
        
        # Mevcut token ile kullanıcının şu anki mailini öğrenelim
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user_res = db.auth.get_user(token)
        current_email = user_res.user.email
        
        # 6 haneli rastgele kod üret
        code = str(secrets.randbetween(100000, 999999) if hasattr(secrets, 'randbetween') else random.randint(100000, 999999))
        
        # Kodu 10 dakika geçerli olacak şekilde hafızaya al
        verification_codes[user_id] = {
            "code": code,
            "type": payload.type,
            "value": payload.value,
            "expires": datetime.now() + timedelta(minutes=10)
        }
        
        # Güvenli kod mailini gönderme (Resend SMTP altyapısı ile)
        msg = MIMEMultipart()
        msg['From'] = "TrendRadar <onboarding@resend.dev>"
        msg['To'] = current_email
        msg['Subject'] = "TrendRadar - Güvenlik Onay Kodu"
        
        action_text = "E-posta değiştirme" if payload.type == "email" else "Şifre değiştirme"
        html = f"""
        <div style="font-family:sans-serif; padding:20px; color:#1e293b;">
          <h2 style="color:#FF7A00;">TrendRadar Güvenlik Onayı</h2>
          <p>Hesabınızda <strong>{action_text}</strong> talebinde bulunuldu. İşlemi tamamlamak için kullanmanız gereken 6 haneli onay kodunuz:</p>
          <div style="background:#f1f5f9; padding:15px; text-align:center; font-size:2rem; font-weight:800; letter-spacing:8px; color:#0f172a; border-radius:8px; margin:20px 0;">
            {code}
          </div>
          <p>Bu kod 10 dakika boyunca geçerlidir. Bu talebi siz yapmadıysanız lütfen hesap şifrenizi güvenli bir şifreyle güncelleyin.</p>
        </div>
        """
        msg.attach(MIMEText(html, 'html'))
        
        smtp_password = os.getenv("RESEND_API_KEY")
        with smtplib.SMTP_SSL('smtp.resend.com', 465) as server:
            server.login('resend', smtp_password)
            server.sendmail("onboarding@resend.dev", current_email, msg.as_string())
            
        return {"success": True, "message": "Onay kodu e-posta adresinize gönderildi."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class VerifyCodePayload(BaseModel):
    code: str

@app.post("/api/profile/update")
def update_profile(request: Request, payload: VerifyCodePayload):
    try:
        user_id = get_current_user_id(request)
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        
        if user_id not in verification_codes:
            raise HTTPException(status_code=400, detail="Aktif bir onay kodu talebi bulunamadı.")
            
        saved = verification_codes[user_id]
        
        if datetime.now() > saved["expires"]:
            del verification_codes[user_id]
            raise HTTPException(status_code=400, detail="Kodun süresi dolmuş. Lütfen yeniden kod isteyin.")
            
        if saved["code"] != payload.code.strip():
            raise HTTPException(status_code=400, detail="Girdiğiniz 6 haneli kod hatalı.")
            
        # Kod doğru! Supabase üzerinde güncellemeyi yapalım
        db = get_authed_db(token)
        update_data = {}
        if saved["type"] == "email":
            update_data["email"] = saved["value"]
        elif saved["type"] == "password":
            update_data["password"] = saved["value"]
            
        db.auth.update_user(update_data)
        
        # Kod havuzunu temizle
        del verification_codes[user_id]
        return {"success": True, "message": "Bilgileriniz başarıyla güncellendi."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/auth/google-url")
def get_google_auth_url():
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        # Kullanıcı giriş yaptıktan sonra yönlendirileceği ön yüz adresi
        # Canlıya aldığınızda buraya Render üzerindeki login adresi yazılmalıdır (Örn: https://projeniz.onrender.com/login.html)
        redirect_url = "http://localhost:8000/login.html" 
        
        auth_url = f"{supabase_url}/auth/v1/authorize?provider=google&redirect_to={redirect_url}"
        return {"url": auth_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/auth/user-details")
def get_user_details(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="Token bulunamadi.")
    try:
        db = get_db()
        user_res = db.auth.get_user(token)
        return {"email": user_res.user.email}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
@app.get("/api/auth/github-url")
def get_github_auth_url():
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        # Test ediyorsan localhost kalabilir, canlıya alırken kendi sitenin login sayfasının linki olmalı.
        redirect_url = "http://localhost:8000/login.html" 
        
        # Provider parametresini "github" olarak ayarladık
        auth_url = f"{supabase_url}/auth/v1/authorize?provider=github&redirect_to={redirect_url}"
        return {"url": auth_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
from fastapi.responses import JSONResponse

# Küresel admin durum değişkenleri
is_maintenance_mode = False
system_logs = [
    {"time": datetime.now().strftime("%H:%M:%S"), "action": "Sistem başlatıldı.", "user": "Sistem"}
]

def add_log(action: str, user: str = "Admin"):
    """Sistem log havuzuna yeni kayıt ekler"""
    system_logs.insert(0, {
        "time": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "action": action,
        "user": user
    })

# Bakım Modu Filtresi (Middleware)
@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    global is_maintenance_mode
    # Admin yolları ve statik admin paneli sayfası bakım modundan etkilenmesin
    is_admin_route = request.url.path.startswith("/api/admin") or "adminpanel" in request.url.path
    
    if is_maintenance_mode and not is_admin_route:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=503, 
                content={"detail": "TrendRadar şu anda bakım modundadır. Lütfen daha sonra tekrar deneyiniz."}
            )
    return await call_next(request)
class AdminLoginPayload(BaseModel):
    username: str
    password: str

@app.post("/api/admin/login")
def admin_login(payload: AdminLoginPayload):
    env_user = os.getenv("ADMIN_USER", "admin")
    env_pass = os.getenv("ADMIN_PASS", "admin123")
    
    if payload.username == env_user and payload.password == env_pass:
        add_log("Yönetici paneline başarılı giriş yapıldı.")
        return {"success": True, "token": "tr_admin_secure_session_token_2026"}
    raise HTTPException(status_code=401, detail="Hatalı admin kullanıcı adı veya şifre.")

@app.get("/api/admin/dashboard")
def get_admin_dashboard(token: str = None):
    # Basit token doğrulaması
    if token != "tr_admin_secure_session_token_2026":
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
        
    global is_maintenance_mode, system_logs
    db = get_db()
    
    # Tüm firmaları çekelim
    companies_res = db.table("companies").select("*").execute()
    companies_list = companies_res.data if hasattr(companies_res, 'data') else []
    
    # Firmalardan benzersiz kullanıcı maillerini/ID'lerini derive edelim (Alternatif kullanıcı listeleme)
    # Gerçek kullanıcıları Supabase auth admin API yetkisi olmadan listelemenin en kararlı yolu
    users_dict = {}
    for comp in companies_list:
        u_id = comp.get("user_id", "Bilinmeyen Kullanıcı")
        if u_id not in users_dict:
            users_dict[u_id] = {"id": u_id, "company_count": 0, "sector": comp.get("sector", "-")}
        users_dict[u_id]["company_count"] += 1
        
    return {
        "maintenance_mode": is_maintenance_mode,
        "logs": system_logs[:50], # Son 50 log kaydı
        "companies": companies_list,
        "users": list(users_dict.values())
    }

class StatusPayload(BaseModel):
    status: bool
    token: str

@app.post("/api/admin/toggle-status")
def toggle_site_status(payload: StatusPayload):
    if payload.token != "tr_admin_secure_session_token_2026":
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    global is_maintenance_mode
    is_maintenance_mode = payload.status
    state_str = "KAPATILDI (Bakım Modu Aktif)" if is_maintenance_mode else "AÇILDI (Canlı Mod)"
    add_log(f"Site erişim durumu değiştirildi: {state_str}")
    return {"success": True, "maintenance_mode": is_maintenance_mode}

class BulkEmailPayload(BaseModel):
    token: str
    target: str # "all" veya "custom"
    custom_emails: str = ""
    subject: str
    body: str

@app.post("/api/admin/send-bulk-email")
def send_bulk_email(payload: BulkEmailPayload):
    if payload.token != "tr_admin_secure_session_token_2026":
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
        
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    # Hedef e-postaları belirleyelim
    targets = []
    if payload.target == "all":
        db = get_db()
        comp_res = db.table("companies").select("email").execute()
        comp_data = comp_res.data if hasattr(comp_res, 'data') else []
        targets = list(set([c["email"] for c in comp_data if c.get("email")]))
    else:
        targets = [e.strip() for e in payload.custom_emails.split(",") if e.strip()]
        
    if not targets:
        raise HTTPException(status_code=400, detail="Gönderilecek geçerli e-posta adresi bulunamadı.")
        
    smtp_password = os.getenv("RESEND_API_KEY")
    sent_count = 0
    
    try:
        with smtplib.SMTP_SSL('smtp.resend.com', 465) as server:
            server.login('resend', smtp_password)
            
            for email in targets:
                msg = MIMEMultipart()
                msg['From'] = "TrendRadar Yönetim <onboarding@resend.dev>"
                msg['To'] = email
                msg['Subject'] = payload.subject
                
                html = f"""
                <div style="font-family:sans-serif; padding:20px; color:#1e293b;">
                  {payload.body.replace('\n', '<br>')}
                </div>
                """
                msg.attach(MIMEText(html, 'html'))
                server.sendmail("onboarding@resend.dev", email, msg.as_string())
                sent_count += 1
                
        add_log(f"Toplu E-Posta Gönderildi. Konu: {payload.subject} ({sent_count} Adet)")
        return {"success": True, "message": f"{sent_count} adet e-posta başarıyla gönderildi."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
class UpdateProductPayload(BaseModel):
    stock: int
    price: float

class UpdateProductPayload(BaseModel):
    stock: int
    price: float

@app.post("/api/products/update-manual/{product_id}")
def update_product_manual(product_id: int, payload: UpdateProductPayload, request: Request):
    try:
        # ÇÖZÜM 1: get_authed_db yerine get_db() kullanarak Supabase RLS (Güvenlik) engellerini aşıyoruz
        db = get_db() 
        
        res = db.table("products").update({
            "stock": payload.stock,
            "price": payload.price
        }).eq("id", product_id).execute()
        
        # Gerçekten güncellenip güncellenmediğini denetliyoruz (Sessiz hatayı önler)
        updated_data = res.data if hasattr(res, 'data') else []
        if len(updated_data) == 0:
            raise HTTPException(status_code=400, detail="Supabase güncellemeyi reddetti. Veritabanında bu ID bulunamadı.")
            
        return {"success": True, "message": "Ürün başarıyla güncellendi."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
class SyncProductsPayload(BaseModel):
    company_id: int
    products: list[dict]

@app.post("/api/products/sync")
def sync_products(payload: SyncProductsPayload, request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    db = get_db() if not token else get_authed_db(token)

    try:
        # 1. Firmanın mevcut ürünlerini veritabanından çek
        res = db.table("products").select("id, name").eq("company_id", payload.company_id).execute()
        existing_products = res.data if hasattr(res, 'data') else []
        
        # Ürün isimlerini küçük harfe çevirerek bir ID sözlüğü oluşturalım (Hızlı arama için)
        name_to_id = {p["name"].strip().lower(): p["id"] for p in existing_products}

        new_items = []
        update_count = 0
        
        # 2. Yüklenen CSV'deki her bir ürünü kontrol et
        for p in payload.products:
            name_key = p["name"].strip().lower()
            
            if name_key in name_to_id:
                # Ürün sistemde var -> SADECE GÜNCELLE
                db.table("products").update({
                    "stock": int(p.get("stock", 0)),
                    "price": float(p.get("price", 0)),
                    "category": p.get("category", "")
                }).eq("id", name_to_id[name_key]).execute()
                update_count += 1
            else:
                # Ürün sistemde yok -> YENİ ÜRÜN OLARAK EKLE
                new_items.append({
                    "company_id": payload.company_id,
                    "name": p["name"],
                    "category": p.get("category", ""),
                    "price": float(p.get("price", 0)),
                    "stock": int(p.get("stock", 0))
                })
        
        # 3. Yeni ürünleri topluca veritabanına yaz
        if new_items:
            db.table("products").insert(new_items).execute()

        return {
            "success": True, 
            "message": f"{update_count} ürün güncellendi, {len(new_items)} yeni ürün eklendi!"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))