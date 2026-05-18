from fastapi import FastAPI, HTTPException
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

# Static dosyalar (HTML)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Supabase client
def get_db() -> Client:
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )

# Gemini
def get_gemini():
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    return genai.GenerativeModel("gemini-1.5-pro")


# ── Ana sayfa ──
@app.get("/")
def root():
    return FileResponse("static/trendradar.html")


# ══════════════════════════════
# COMPANIES
# ══════════════════════════════

@app.get("/api/companies")
def get_companies():
    try:
        db = get_db()
        res = db.table("companies").select("*").order("created_at", desc=False).execute()
        return {"data": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/companies/{company_id}")
def get_company(company_id: int):
    try:
        db = get_db()
        res = db.table("companies").select("*").eq("id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Firma bulunamadi.")
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
def create_company(payload: CompanyCreate):
    try:
        db = get_db()
        res = db.table("companies").insert(payload.dict()).execute()
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
        res = db.table("products").select("*, companies(name)").lt("stock", threshold).execute()
        return {"data": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# STATS (God Mode)
# ══════════════════════════════

@app.get("/api/stats")
def get_stats():
    try:
        db = get_db()
        companies = db.table("companies").select("id", count="exact").execute()
        products  = db.table("products").select("id, stock, category", count="exact").execute()

        total_stock = sum(p["stock"] for p in products.data)
        categories  = len(set(p["category"] for p in products.data if p["category"]))

        return {
            "total_companies": companies.count,
            "total_products":  products.count,
            "total_stock":     total_stock,
            "unique_categories": categories,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════
# AI — Otonom Aksiyonlar
# ══════════════════════════════

class AiRequest(BaseModel):
    company_id: int
    analysis_type: str = "fiyatlandirma"

@app.post("/api/ai/analyze")
def ai_analyze(req: AiRequest):
    try:
        db     = get_db()
        model  = get_gemini()

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

        prompt   = prompts.get(req.analysis_type, prompts["fiyatlandirma"])
        response = model.generate_content(prompt)

        return {"success": True, "result": response.text}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))