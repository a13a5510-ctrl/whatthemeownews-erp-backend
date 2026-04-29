from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from typing import List, Optional
import datetime
import os
import json
import google.generativeai as genai 

# ==========================================
# 1. 雲端資料庫與 AI 連線設定
# ==========================================
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./miao_erp.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 設定 Gemini AI 金鑰
gemini_key = os.getenv("GEMINI_API_KEY", "")
if gemini_key:
    genai.configure(api_key=gemini_key)

# ==========================================
# 2. 定義資料表 Schema
# ==========================================
class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    price = Column(Integer)
    cost = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String)
    total_amount = Column(Integer, default=0)
    received = Column(Boolean, default=False)
    items = Column(String, nullable=True)  
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Material(Base):
    __tablename__ = "materials"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)   
    unit = Column(String)                            
    stock_qty = Column(Float, default=0.0)           
    unit_cost = Column(Float, default=0.0)           

class RecipeItem(Base):
    __tablename__ = "recipe_items"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    material_id = Column(Integer, ForeignKey("materials.id"))
    consume_qty = Column(Float)                      

Base.metadata.create_all(bind=engine)

# 無痛擴建 items 欄位
try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders ADD COLUMN items VARCHAR"))
except Exception:
    pass 

# ==========================================
# 3. 定義接收資料的格式 (Pydantic)
# ==========================================
class OrderData(BaseModel):
    order_no: str; total_amount: int; received: bool; items: Optional[str] = ""; note: Optional[str] = ""

class RecipeInput(BaseModel):
    material_id: int; consume_qty: float

class ProductCreate(BaseModel):
    name: str; price: int; recipes: List[RecipeInput]

class MaterialInput(BaseModel):
    name: str; unit: str; unit_cost: float

class MaterialUpdate(BaseModel):
    name: str; unit: str; unit_cost: float; stock_qty: float

class VoiceOrderRequest(BaseModel):
    transcript: str

# ==========================================
# 4. 台灣時間轉換引擎
# ==========================================
def get_tw_time_ranges():
    tw_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_start_tw = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_tw = today_start_tw - datetime.timedelta(days=1)
    return { 
        "yesterday_start": yesterday_start_tw - datetime.timedelta(hours=8), 
        "today_start": today_start_tw - datetime.timedelta(hours=8), 
        "tomorrow_start": (today_start_tw - datetime.timedelta(hours=8)) + datetime.timedelta(days=1) 
    }

# ==========================================
# 5. 初始化 FastAPI 與 API 路由
# ==========================================
app = FastAPI(title="喵逮雞 ERP API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def read_root(): 
    return {"status": "success", "message": "喵逮雞 Cloud Run 伺服器運作中🚀"}

# --- 🌟 初始化庫存與菜單 API ---
@app.get("/api/init_inventory")
def init_inventory():
    db = SessionLocal()
    try:
        initial = [
            {"name": "雞蛋", "unit": "顆", "stock_qty": 300, "unit_cost": 5.0}, 
            {"name": "低筋麵粉", "unit": "克", "stock_qty": 10000, "unit_cost": 0.05}, 
            {"name": "泡打粉", "unit": "克", "stock_qty": 1000, "unit_cost": 0.2}, 
            {"name": "油", "unit": "毫升", "stock_qty": 5000, "unit_cost": 0.1}, 
            {"name": "糖", "unit": "克", "stock_qty": 5000, "unit_cost": 0.04}
        ]
        added_count = 0
        for mat in initial:
            if not db.query(Material).filter(Material.name == mat["name"]).first():
                db.add(Material(**mat))
                added_count += 1
        db.commit()
        return {"status": "success", "message": f"成功建檔 {added_count} 項！"}
    except Exception as e: 
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally: 
        db.close()

# --- 🌟 AI 語音解析引擎 API ---
@app.post("/api/ai/parse-order")
def parse_voice_order(req: VoiceOrderRequest):
    if not gemini_key:
        return {"status": "error", "message": "伺服器尚未設定 GEMINI_API_KEY"}
    
    db = SessionLocal()
    try:
        products = db.query(Product).all()
        product_names = [p.name for p in products]

        prompt = f"""
        你是一個頂級的 POS 系統點餐解析器。
        目前店裡的菜單有：{product_names}
        
        任務：將客人的口語語音紀錄轉為 JSON 格式。
        規則：
        1. 聰明地忽略錯別字與冗言贅字（例如：把「原位」、「圓味」當作「原味」，把「找媒」、「炒梅」當作「草莓」）。
        2. 將數量轉換為阿拉伯數字（例如：兩個、兩格=2，三盒=3）。
        3. 【極度重要】請「只」輸出 JSON 格式的字串，絕對不要包含 Markdown 語法（不要有 ```json ），也不要有任何其他說明文字。
        4. 如果聽不懂或沒有任何符合的品項，請回傳空字典 {{}}。
        
        客人語音：「{req.transcript}」
        """

        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        
        res_text = response.text.replace("```json", "").replace("```", "").strip()
        parsed_json = json.loads(res_text)
        
        return {"status": "success", "data": parsed_json}

    except json.JSONDecodeError:
        return {"status": "error", "message": f"AI 回傳格式錯誤: {response.text}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

# --- POS 結帳與統計 API ---
@app.post("/api/orders")
def create_orders(orders: List[OrderData]):
    db = SessionLocal()
    try:
        for o in orders: 
            db.add(Order(order_no=o.order_no, total_amount=o.total_amount, received=o.received, items=o.items, note=o.note))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}
    finally: 
        db.close()

@app.get("/api/orders")
def get_orders():
    db = SessionLocal()
    try: 
        return {"status": "success", "data": db.query(Order).order_by(Order.created_at.desc()).limit(100).all()}
    finally: 
        db.close()

@app.get("/api/orders/today")
def get_today_orders():
    db = SessionLocal()
    ranges = get_tw_time_ranges()
    try: 
        return {"status": "success", "data": db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).order_by(Order.created_at.desc()).all()}
    finally: 
        db.close()

@app.get("/api/orders/yesterday")
def get_yesterday_orders():
    db = SessionLocal()
    ranges = get_tw_time_ranges()
    try: 
        return {"status": "success", "data": db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).order_by(Order.created_at.desc()).all()}
    finally: 
        db.close()

@app.get("/api/stats/today")
def get_today_stats():
    db = SessionLocal()
    ranges = get_tw_time_ranges()
    try:
        ords = db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).all()
        return {
            "status": "success", 
            "data": { 
                "total_orders_count": len(ords), 
                "revenue_received": sum(o.total_amount for o in ords if o.received), 
                "revenue_unpaid": sum(o.total_amount for o in ords if not o.received) 
            }
        }
    finally: 
        db.close()

@app.get("/api/stats/yesterday")
def get_yesterday_stats():
    db = SessionLocal()
    ranges = get_tw_time_ranges()
    try:
        ords = db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).all()
        return {
            "status": "success", 
            "data": { 
                "total_orders_count": len(ords), 
                "revenue_received": sum(o.total_amount for o in ords if o.received), 
                "revenue_unpaid": sum(o.total_amount for o in ords if not o.received) 
            }
        }
    finally: 
        db.close()

# --- 進銷存與品項管理 API ---
@app.get("/api/inventory")
def get_inventory():
    db = SessionLocal()
    try: 
        return {"status": "success", "data": db.query(Material).order_by(Material.id.desc()).all()}
    finally: 
        db.close()

@app.post("/api/materials")
def create_material(mat: MaterialInput):
    db = SessionLocal()
    try:
        db.add(Material(name=mat.name, unit=mat.unit, unit_cost=mat.unit_cost, stock_qty=0.0))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}
    finally: 
        db.close()

@app.put("/api/materials/{material_id}")
def update_material(material_id: int, mat: MaterialUpdate):
    db = SessionLocal()
    try:
        m = db.query(Material).filter(Material.id == material_id).first()
        m.name = mat.name
        m.unit = mat.unit
        m.unit_cost = mat.unit_cost
        m.stock_qty = mat.stock_qty
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}
    finally: 
        db.close()

@app.post("/api/admin/products")
def create_full_product(data: ProductCreate):
    db = SessionLocal()
    try:
        new_prod = Product(name=data.name, price=data.price)
        db.add(new_prod)
        db.flush() 
        for r in data.recipes: 
            db.add(RecipeItem(product_id=new_prod.id, material_id=r.material_id, consume_qty=r.consume_qty))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}
    finally: 
        db.close()

@app.put("/api/admin/products/{product_id}")
def update_full_product(product_id: int, data: ProductCreate):
    db = SessionLocal()
    try:
        prod = db.query(Product).filter(Product.id == product_id).first()
        prod.name = data.name
        prod.price = data.price
        db.query(RecipeItem).filter(RecipeItem.product_id == prod.id).delete()
        for r in data.recipes: 
            db.add(RecipeItem(product_id=prod.id, material_id=r.material_id, consume_qty=r.consume_qty))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}
    finally: 
        db.close()

@app.get("/api/admin/products")
def get_products():
    db = SessionLocal()
    try:
        products = db.query(Product).order_by(Product.id.desc()).all()
        result = []
        for p in products:
            recipes = db.query(RecipeItem).filter(RecipeItem.product_id == p.id).all()
            total_cost = sum([db.query(Material).filter(Material.id == r.material_id).first().unit_cost * r.consume_qty for r in recipes if db.query(Material).filter(Material.id == r.material_id).first()])
            result.append({ 
                "id": p.id, 
                "name": p.name, 
                "price": p.price, 
                "total_cost": round(total_cost, 2), 
                "gross_profit": round(p.price - total_cost, 2) 
            })
        return {"status": "success", "data": result}
    finally: 
        db.close()
# 強制觸發 Cloud Run 更新 AI 通道
