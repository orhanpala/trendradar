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
        raise HTTPException(status_code=400, detail="Bu email zaten kayitli veya gecersiz.")

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