from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional
import datetime
import os
import json
import urllib.request 
import urllib.error   

# ==========================================
# 1. 雲端資料庫與 AI 連線設定
# ==========================================
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./miao_erp.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 取得 Gemini 金鑰
gemini_key = os.getenv("GEMINI_API_KEY", "")

# ==========================================
# 🌟 大師優化：依賴注入 (Dependency Injection)
# 自動管理 Session，避免 Memory Leak 與重複的 try-finally
# ==========================================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

@app.get("/api/init_inventory")
def init_inventory(db: Session = Depends(get_db)):
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

# --- 🌟 終極殺招：原生 API 直連語音解析引擎 ---
@app.post("/api/ai/parse-order")
def parse_voice_order(req: VoiceOrderRequest, db: Session = Depends(get_db)):
    if not gemini_key:
        return {"status": "error", "message": "伺服器尚未設定 GEMINI_API_KEY"}
    
    try:
        products = db.query(Product).all()
        product_names = [p.name for p in products]

        # 🌟 大師提速與防呆心法：加入「強制正名辭典」與「全包/排除陣法」
        prompt = f"""
        任務：將口語點餐轉為 JSON。
        標準菜單：{product_names}

        【強制正名辭典】（遇到以下發音或俗稱，一律轉換為標準菜單名稱）：
        - 泰奶：太乃、泰式、泰式奶茶、泰國奶茶
        - 鮪玉：鮪魚玉米、尾玉、尾魚、鮪魚
        - 菜脯米：菜脯、菜圃、蘿蔔絲、ㄘㄞˋㄅㄛ˙
        - 金沙：金莎、鹹蛋黃（注意：絕非巧克力，若聽到「金莎」一律指金沙口味）
        - 巧克力：巧克、黑巧、巧克力金莎
        - 卡士達：卡士、奶油、克林姆
        - 原味：原位、圓味
        - 數量詞容錯：兩個/兩格/兩顆=2，三個/散個=3，一份=1

        解析規則：
        1. 數量轉換：中文口語轉純阿拉伯數字。
        2. 【全包與排除】：若聽到「綜合、每種各一、全上」，請將標準菜單中【所有品項】皆設為 1。若聽到「除了X不要，其他各一」，請將標準菜單中除了 X 以外的所有品項皆設為 1。
        3. 收款判定：聽到「已收款、收錢了、付清、結帳」，加上 `"is_paid": true`。
        4. 擷取備註：若有特殊需求（如幾點拿、要袋子、烤酥一點），統整短句放入 `"note"`。
        5. 取消指令：若明確指示「取消這單、清空、不要了、刪除」，只回傳 {{"action": "clear"}}。
        6. 無效判定：若完全聽不懂或無符合品項，回傳 {{}}。

        客人語音：「{req.transcript}」
        輸出範例：{{"菜脯米": 1, "鮪玉": 1, "is_paid": true, "note": "下午拿"}}
        """

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        headers = {'Content-Type': 'application/json'}
        
        # 🌟 開啟神經直連：強制回傳乾淨的 JSON 格式
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }

        request = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')

        with urllib.request.urlopen(request) as response:
            result = json.loads(response.read().decode('utf-8'))
            res_text = result['candidates'][0]['content']['parts'][0]['text']

            res_text = res_text.replace("```json", "").replace("```", "").strip()
            parsed_json = json.loads(res_text)

        return {"status": "success", "data": parsed_json}

    except urllib.error.HTTPError as e:
        error_info = e.read().decode('utf-8')
        return {"status": "error", "message": f"AI 通訊失敗: {error_info}"}
    except json.JSONDecodeError:
        return {"status": "error", "message": f"AI 回傳格式錯誤: {res_text}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- POS 結帳與統計 API ---
@app.post("/api/orders")
def create_orders(orders: List[OrderData], db: Session = Depends(get_db)):
    try:
        for o in orders: 
            db.add(Order(order_no=o.order_no, total_amount=o.total_amount, received=o.received, items=o.items, note=o.note))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}

@app.get("/api/orders")
def get_orders(db: Session = Depends(get_db)):
    return {"status": "success", "data": db.query(Order).order_by(Order.created_at.desc()).limit(100).all()}

@app.get("/api/orders/today")
def get_today_orders(db: Session = Depends(get_db)):
    ranges = get_tw_time_ranges()
    return {"status": "success", "data": db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).order_by(Order.created_at.desc()).all()}

@app.get("/api/orders/yesterday")
def get_yesterday_orders(db: Session = Depends(get_db)):
    ranges = get_tw_time_ranges()
    return {"status": "success", "data": db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).order_by(Order.created_at.desc()).all()}

@app.get("/api/stats/today")
def get_today_stats(db: Session = Depends(get_db)):
    ranges = get_tw_time_ranges()
    ords = db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).all()
    return {
        "status": "success", 
        "data": { 
            "total_orders_count": len(ords), 
            "revenue_received": sum(o.total_amount for o in ords if o.received), 
            "revenue_unpaid": sum(o.total_amount for o in ords if not o.received) 
        }
    }

@app.get("/api/stats/yesterday")
def get_yesterday_stats(db: Session = Depends(get_db)):
    ranges = get_tw_time_ranges()
    ords = db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).all()
    return {
        "status": "success", 
        "data": { 
            "total_orders_count": len(ords), 
            "revenue_received": sum(o.total_amount for o in ords if o.received), 
            "revenue_unpaid": sum(o.total_amount for o in ords if not o.received) 
        }
    }

# --- 進銷存與品項管理 API ---
@app.get("/api/inventory")
def get_inventory(db: Session = Depends(get_db)):
    return {"status": "success", "data": db.query(Material).order_by(Material.id.desc()).all()}

@app.post("/api/materials")
def create_material(mat: MaterialInput, db: Session = Depends(get_db)):
    try:
        db.add(Material(name=mat.name, unit=mat.unit, unit_cost=mat.unit_cost, stock_qty=0.0))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        return {"status": "error"}

@app.put("/api/materials/{material_id}")
def update_material(material_id: int, mat: MaterialUpdate, db: Session = Depends(get_db)):
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

@app.post("/api/admin/products")
def create_full_product(data: ProductCreate, db: Session = Depends(get_db)):
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

@app.put("/api/admin/products/{product_id}")
def update_full_product(product_id: int, data: ProductCreate, db: Session = Depends(get_db)):
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

@app.get("/api/admin/products")
def get_products(db: Session = Depends(get_db)):
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
