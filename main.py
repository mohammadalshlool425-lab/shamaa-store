from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from database import engine, get_db
import models

# إنشاء جداول قاعدة البيانات
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Shama'a API", description="السوق المفتوح لطلاب جامعة اليرموك")

@app.get("/")
def root():
    return {"message": "مرحباً بك في الخادم الرئيسي لمنصة شمعة"}

@app.post("/users/")
def create_user(username: str, email: str, db: Session = Depends(get_db)):
    # التحقق من وجود المستخدم
    db_user = db.query(models.User).filter(models.User.email == email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني مسجل مسبقاً")
    
    # إضافة مستخدم جديد (كلمة المرور غير مشفرة مؤقتاً للتجربة)
    new_user = models.User(username=username, email=email, hashed_password="temp_password")
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.get("/ads/")
def get_ads(db: Session = Depends(get_db)):
    ads = db.query(models.Ad).all()
    return ads
