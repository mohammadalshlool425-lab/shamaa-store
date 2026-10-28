from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*', mode='before')
    @classmethod
    def strip_text_not_passwords(cls, value, info):
        if isinstance(value, str):
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise ValueError('Invalid Unicode text') from None
            if info.field_name != 'password':
                return value.strip()
        return value


class Register(Input):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)
    locale: Literal['ar','en'] = 'ar'

    @field_validator('password')
    @classmethod
    def password_strength(cls, value):
        if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
            raise ValueError('Use letters and numbers')
        return value


class Login(Input):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)
    otp: str = Field(default='', max_length=6)


class EmailInput(Input):
    email: EmailStr = Field(max_length=254)


class TokenInput(Input):
    token: str = Field(min_length=20, max_length=128)


class Reset(TokenInput):
    password: str = Field(min_length=10, max_length=128)
    _strong = field_validator('password')(Register.password_strength.__func__)


class Reauth(Input):
    password: str = Field(min_length=1, max_length=128)
    otp: str = Field(min_length=6, max_length=6)


City = Literal['amman','irbid','zarqa','aqaba','salt','madaba','jerash','ajloun','mafraq','karak','tafilah','maan','ramtha']


class StoreInput(Input):
    slug: str = Field(min_length=3, max_length=50, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    name_ar: str = Field(min_length=2, max_length=80)
    name_en: str = Field(min_length=2, max_length=80)
    description_ar: str = Field(default='', max_length=1000)
    description_en: str = Field(default='', max_length=1000)
    city: City
    accent: Literal['green','sand','blue'] = 'green'
    shipping_fils: int = Field(default=3000, ge=0, le=100000, strict=True)
    delivery_days: int = Field(default=3, ge=1, le=30, strict=True)


class ProductInput(Input):
    category_id: str = Field(min_length=1, max_length=50)
    title_ar: str = Field(min_length=3, max_length=140)
    title_en: str = Field(min_length=3, max_length=140)
    description_ar: str = Field(min_length=10, max_length=5000)
    description_en: str = Field(min_length=10, max_length=5000)
    price_fils: int = Field(ge=1, le=1000000000, strict=True)
    compare_at_fils: int | None = Field(default=None, ge=2, le=1000000000, strict=True)
    stock: int = Field(ge=0, le=1000000, strict=True)
    sku: str = Field(min_length=1, max_length=60, pattern=r'^[\w.\-]+$')
    media_id: int | None = Field(default=None, ge=1, strict=True)
    status: Literal['pending','draft','paused'] = 'pending'
    expected_version: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode='after')
    def valid_discount(self):
        if self.compare_at_fils is not None and self.compare_at_fils <= self.price_fils:
            raise ValueError('Original price must exceed the sale price')
        if self.status == 'pending' and self.media_id is None:
            raise ValueError('A product image is required for review')
        return self


class StockInput(Input):
    stock: int = Field(ge=0, le=1000000, strict=True)
    expected_version: int = Field(ge=1, strict=True)


class CartItem(Input):
    product_id: int = Field(ge=1, strict=True)
    quantity: int = Field(ge=0, le=99, strict=True)


class Checkout(Input):
    recipient: str = Field(min_length=2, max_length=80)
    phone: str = Field(pattern=r'^07[789]\d{7}$')
    city: City
    address: str = Field(min_length=10, max_length=400)
    expected_total_fils: int = Field(ge=1, le=100000000000, strict=True)
    quote_hash: str = Field(min_length=64, max_length=64)


class StatusChange(Input):
    status: Literal['confirmed','preparing','shipped','delivered','cancelled']
    expected_status: Literal['new','confirmed','preparing','shipped','delivered','cancelled']
    note: str = Field(default='', max_length=500)


class Review(Input):
    approved: bool
    note: str = Field(default='', max_length=500)


class UserActive(Input):
    active: bool


class SettingsInput(Input):
    commission_bps: int = Field(ge=0, le=5000, strict=True)
    home_title_ar: str = Field(min_length=3, max_length=100)
    home_title_en: str = Field(min_length=3, max_length=100)


class CommissionInput(Input):
    commission_bps: int | None = Field(default=None, ge=0, le=5000, strict=True)
