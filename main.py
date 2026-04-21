from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from typing import List, Optional
import datetime
import os

# ==========================================
# 1. 雲端資料庫連線設定 (PostgreSQL)
# ==========================================
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "sqlite:///./miao_erp.db"
)

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

# ==========================================
# 3. 定義接收資料的格式 (Pydantic)
# ==========================================
class OrderData(BaseModel):
    order_no: str
    total_amount: int
    received: bool
    note: Optional[str] = ""

# 這是 admin.html 傳過來的格式
class RecipeInput(BaseModel):
    material_id: int
    consume_qty: float

class ProductCreate(BaseModel):
    name: str
    price: int
    recipes: List[RecipeInput]

# ==========================================
# 4. 初始化 FastAPI 伺服器與路由
# ==========================================
app = FastAPI(title="喵逮雞 ERP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "success", "message": "喵逮雞 Cloud Run 伺服器運作中🚀"}

# --- POS 結帳 API ---
@app.post("/api/orders")
def create_orders(orders: List[OrderData]):
    db = SessionLocal()
    saved_count = 0
    try:
        for o in orders:
            new_order = Order(order_no=o.order_no, total_amount=o.total_amount, received=o.received, note=o.note)
            db.add(new_order)
            saved_count += 1
        db.commit()
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
    return {"status": "success", "message": f"成功寫入 {saved_count} 筆訂單！"}

# --- 戰情室 API ---
@app.get("/api/orders")
def get_orders():
    db = SessionLocal()
    try:
        orders = db.query(Order).order_by(Order.created_at.desc()).limit(100).all()
        return {"status": "success", "data": orders}
    finally:
        db.close()

@app.get("/api/stats/today")
def get_today_stats():
    db = SessionLocal()
    try:
        twenty_four_hours_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        today_orders = db.query(Order).filter(Order.created_at >= twenty_four_hours_ago).all()
        return {
            "status": "success",
            "data": {
                "total_orders_count": len(today_orders),
                "revenue_received": sum(o.total_amount for o in today_orders if o.received),
                "revenue_unpaid": sum(o.total_amount for o in today_orders if not o.received)
            }
        }
    finally:
        db.close()

# --- 進銷存與管理 API ---
@app.get("/api/init_inventory")
def init_inventory():
    db = SessionLocal()
    try:
        # 這裡設定了您的五大天王
        initial_materials = [
            {"name": "雞蛋", "unit": "顆", "stock_qty": 300, "unit_cost": 5.0},
            {"name": "低筋麵粉", "unit": "克", "stock_qty": 10000, "unit_cost": 0.05}, 
            {"name": "泡打粉", "unit": "克", "stock_qty": 1000, "unit_cost": 0.2},
            {"name": "油", "unit": "毫升", "stock_qty": 5000, "unit_cost": 0.1},
            {"name": "糖", "unit": "克", "stock_qty": 5000, "unit_cost": 0.04}
        ]
        added_count = 0
        for mat in initial_materials:
            if not db.query(Material).filter(Material.name == mat["name"]).first():
                db.add(Material(**mat))
                added_count += 1
        db.commit()
        return {"status": "success", "message": f"成功建檔 {added_count} 項核心原物料！"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.get("/api/inventory")
def get_inventory():
    db = SessionLocal()
    try:
        materials = db.query(Material).all()
        return {"status": "success", "data": materials}
    finally:
        db.close()

# 🌟 新增：老闆建檔新口味的專屬通道
@app.post("/api/admin/products")
def create_full_product(data: ProductCreate):
    db = SessionLocal()
    try:
        new_prod = Product(name=data.name, price=data.price)
        db.add(new_prod)
        db.flush() 
        
        for r in data.recipes:
            new_recipe = RecipeItem(product_id=new_prod.id, material_id=r.material_id, consume_qty=r.consume_qty)
            db.add(new_recipe)
        
        db.commit()
        return {"status": "success", "message": f"【{data.name}】口味與配方已成功建檔！"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
