from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from typing import List, Optional
import datetime
import os

# ==========================================
# 1. 雲端資料庫連線設定
# ==========================================
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./miao_erp.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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
    items = Column(String, nullable=True)  # 🌟 新增：存放購買品項的欄位
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

# 🌟 無痛升級資料庫：自動幫舊有的 orders 表格加上 items 欄位
try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders ADD COLUMN items VARCHAR"))
except Exception:
    pass # 如果欄位已經存在就會安靜跳過

# ==========================================
# 3. 定義接收資料的格式 (Pydantic)
# ==========================================
class OrderData(BaseModel):
    order_no: str
    total_amount: int
    received: bool
    items: Optional[str] = ""  # 🌟 讓 API 能接收品項字串
    note: Optional[str] = ""

class RecipeInput(BaseModel):
    material_id: int; consume_qty: float
class ProductCreate(BaseModel):
    name: str; price: int; recipes: List[RecipeInput]
class MaterialInput(BaseModel):
    name: str; unit: str; unit_cost: float
class MaterialUpdate(BaseModel):
    name: str; unit: str; unit_cost: float; stock_qty: float

# ==========================================
# 4. 台灣時間 (UTC+8) 精準日曆轉換引擎
# ==========================================
def get_tw_time_ranges():
    tw_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_start_tw = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_tw = today_start_tw - datetime.timedelta(days=1)
    
    today_start_utc = today_start_tw - datetime.timedelta(hours=8)
    yesterday_start_utc = yesterday_start_tw - datetime.timedelta(hours=8)
    tomorrow_start_utc = today_start_utc + datetime.timedelta(days=1)
    
    return { "yesterday_start": yesterday_start_utc, "today_start": today_start_utc, "tomorrow_start": tomorrow_start_utc }

# ==========================================
# 5. 初始化 FastAPI 伺服器與路由
# ==========================================
app = FastAPI(title="喵逮雞 ERP API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def read_root(): return {"status": "success", "message": "喵逮雞 Cloud Run 伺服器運作中🚀"}

# --- POS 結帳 API ---
@app.post("/api/orders")
def create_orders(orders: List[OrderData]):
    db = SessionLocal()
    saved_count = 0
    try:
        for o in orders:
            # 🌟 寫入品項資料
            new_order = Order(order_no=o.order_no, total_amount=o.total_amount, received=o.received, items=o.items, note=o.note)
            db.add(new_order)
            saved_count += 1
        db.commit()
    except Exception as e: db.rollback(); return {"status": "error", "message": str(e)}
    finally: db.close()
    return {"status": "success", "message": f"成功寫入 {saved_count} 筆訂單！"}

@app.get("/api/orders")
def get_orders():
    db = SessionLocal()
    try:
        orders = db.query(Order).order_by(Order.created_at.desc()).limit(100).all()
        return {"status": "success", "data": orders}
    finally: db.close()

@app.get("/api/orders/today")
def get_today_orders():
    db = SessionLocal()
    try:
        ranges = get_tw_time_ranges()
        orders = db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).order_by(Order.created_at.desc()).all()
        return {"status": "success", "data": orders}
    finally: db.close()

@app.get("/api/orders/yesterday")
def get_yesterday_orders():
    db = SessionLocal()
    try:
        ranges = get_tw_time_ranges()
        orders = db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).order_by(Order.created_at.desc()).all()
        return {"status": "success", "data": orders}
    finally: db.close()

@app.get("/api/stats/today")
def get_today_stats():
    db = SessionLocal()
    try:
        ranges = get_tw_time_ranges()
        today_orders = db.query(Order).filter(Order.created_at >= ranges["today_start"], Order.created_at < ranges["tomorrow_start"]).all()
        return {
            "status": "success",
            "data": { "total_orders_count": len(today_orders), "revenue_received": sum(o.total_amount for o in today_orders if o.received), "revenue_unpaid": sum(o.total_amount for o in today_orders if not o.received) }
        }
    finally: db.close()

@app.get("/api/stats/yesterday")
def get_yesterday_stats():
    db = SessionLocal()
    try:
        ranges = get_tw_time_ranges()
        yesterday_orders = db.query(Order).filter(Order.created_at >= ranges["yesterday_start"], Order.created_at < ranges["today_start"]).all()
        return {
            "status": "success",
            "data": { "total_orders_count": len(yesterday_orders), "revenue_received": sum(o.total_amount for o in yesterday_orders if o.received), "revenue_unpaid": sum(o.total_amount for o in yesterday_orders if not o.received) }
        }
    finally: db.close()

@app.get("/api/init_inventory")
def init_inventory():
    db = SessionLocal()
    try:
        initial = [{"name": "雞蛋", "unit": "顆", "stock_qty": 300, "unit_cost": 5.0}, {"name": "低筋麵粉", "unit": "克", "stock_qty": 10000, "unit_cost": 0.05}, {"name": "泡打粉", "unit": "克", "stock_qty": 1000, "unit_cost": 0.2}, {"name": "油", "unit": "毫升", "stock_qty": 5000, "unit_cost": 0.1}, {"name": "糖", "unit": "克", "stock_qty": 5000, "unit_cost": 0.04}]
        added_count = 0
        for mat in initial:
            if not db.query(Material).filter(Material.name == mat["name"]).first():
                db.add(Material(**mat)); added_count += 1
        db.commit()
        return {"status": "success"}
    except Exception as e: db.rollback(); return {"status": "error"}
    finally: db.close()

@app.get("/api/inventory")
def get_inventory():
    db = SessionLocal()
    try: return {"status": "success", "data": db.query(Material).order_by(Material.id.desc()).all()}
    finally: db.close()

@app.post("/api/materials")
def create_material(mat: MaterialInput):
    db = SessionLocal()
    try:
        if db.query(Material).filter(Material.name == mat.name).first(): return {"status": "error"}
        db.add(Material(name=mat.name, unit=mat.unit, unit_cost=mat.unit_cost, stock_qty=0.0))
        db.commit()
        return {"status": "success"}
    except Exception as e: db.rollback(); return {"status": "error"}
    finally: db.close()

@app.put("/api/materials/{material_id}")
def update_material(material_id: int, mat: MaterialUpdate):
    db = SessionLocal()
    try:
        m = db.query(Material).filter(Material.id == material_id).first()
        if not m: return {"status": "error"}
        m.name = mat.name; m.unit = mat.unit; m.unit_cost = mat.unit_cost; m.stock_qty = mat.stock_qty
        db.commit()
        return {"status": "success"}
    except Exception as e: db.rollback(); return {"status": "error"}
    finally: db.close()

@app.post("/api/admin/products")
def create_full_product(data: ProductCreate):
    db = SessionLocal()
    try:
        if db.query(Product).filter(Product.name == data.name).first(): return {"status": "error"}
        new_prod = Product(name=data.name, price=data.price)
        db.add(new_prod); db.flush() 
        for r in data.recipes: db.add(RecipeItem(product_id=new_prod.id, material_id=r.material_id, consume_qty=r.consume_qty))
        db.commit()
        return {"status": "success"}
    except Exception as e: db.rollback(); return {"status": "error"}
    finally: db.close()

@app.put("/api/admin/products/{product_id}")
def update_full_product(product_id: int, data: ProductCreate):
    db = SessionLocal()
    try:
        prod = db.query(Product).filter(Product.id == product_id).first()
        if not prod: return {"status": "error"}
        prod.name = data.name; prod.price = data.price
        db.query(RecipeItem).filter(RecipeItem.product_id == prod.id).delete()
        for r in data.recipes: db.add(RecipeItem(product_id=prod.id, material_id=r.material_id, consume_qty=r.consume_qty))
        db.commit()
        return {"status": "success"}
    except Exception as e: db.rollback(); return {"status": "error"}
    finally: db.close()

@app.get("/api/admin/products")
def get_products():
    db = SessionLocal()
    try:
        products = db.query(Product).order_by(Product.id.desc()).all()
        result = []
        for p in products:
            recipes = db.query(RecipeItem).filter(RecipeItem.product_id == p.id).all()
            total_cost = sum([db.query(Material).filter(Material.id == r.material_id).first().unit_cost * r.consume_qty for r in recipes if db.query(Material).filter(Material.id == r.material_id).first()])
            result.append({ "id": p.id, "name": p.name, "price": p.price, "total_cost": round(total_cost, 2), "gross_profit": round(p.price - total_cost, 2) })
        return {"status": "success", "data": result}
    finally: db.close()
