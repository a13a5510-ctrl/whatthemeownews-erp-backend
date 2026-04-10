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
    "sqlite:///./miao_erp.db" # 預設回退機制
)

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
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
    cost = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String)
    total_amount = Column(Integer, default=0)
    received = Column(Boolean, default=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# 啟動時自動在雲端建立資料表
Base.metadata.create_all(bind=engine)

# ==========================================
# 3. 定義接收資料的格式 (Pydantic)
# ==========================================
class OrderData(BaseModel):
    order_no: str
    total_amount: int
    received: bool
    note: Optional[str] = ""

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
    return {"status": "success", "message": "喵逮雞 Cloud Run 伺服器與 Neon 資料庫成功上線！🚀"}

# 🚨 新增：接收前端訂單的專屬通道 🚨
@app.post("/api/orders")
def create_orders(orders: List[OrderData]):
    db = SessionLocal()
    saved_count = 0
    try:
        for o in orders:
            new_order = Order(
                order_no=o.order_no,
                total_amount=o.total_amount,
                received=o.received,
                note=o.note
            )
            db.add(new_order)
            saved_count += 1
        db.commit()
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
        
    return {"status": "success", "message": f"成功寫入 {saved_count} 筆訂單至雲端資料庫！"}