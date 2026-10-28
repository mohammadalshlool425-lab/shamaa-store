from fastapi import HTTPException

MESSAGES = {
 'invalid_input': ('تحقّق من الحقول المطلوبة وصيغتها.', 'Please check the required fields and their format.'),
 'auth_required': ('سجّل الدخول للمتابعة.', 'Please sign in to continue.'),
 'forbidden': ('ليس لديك صلاحية لهذه العملية.', 'You do not have permission for this action.'),
 'not_found': ('العنصر المطلوب غير موجود أو غير متاح.', 'This item was not found or is unavailable.'),
 'invalid_login': ('البريد أو كلمة المرور أو رمز التحقق غير صحيح.', 'The email, password or verification code is incorrect.'),
 'email_exists': ('هذا البريد مسجّل بالفعل. سجّل الدخول أو استعد كلمة المرور.', 'This email is already registered. Sign in or reset your password.'),
 'verify_email': ('تحقّق من بريدك الإلكتروني أولًا.', 'Please verify your email address first.'),
 'invalid_token': ('الرابط غير صالح أو انتهت صلاحيته. اطلب رابطًا جديدًا.', 'This link is invalid or expired. Please request a new link.'),
 'rate_limit': ('محاولات كثيرة. انتظر قليلًا ثم أعد المحاولة.', 'Too many attempts. Please wait and try again.'),
 'csrf': ('تعذّر التحقق من الطلب. حدّث الصفحة وحاول مجددًا.', 'We could not verify this request. Refresh the page and try again.'),
 'store_pending': ('المتجر غير نشط. انتظر مراجعة الإدارة.', 'Your store is not active. Please wait for administrator review.'),
 'store_exists': ('لديك متجر بالفعل أو أن رابط المتجر مستخدم.', 'You already have a store, or this store URL is taken.'),
 'stock': ('تغيّر المخزون أو لم تعد الكمية المطلوبة متاحة. راجع السلة.', 'Stock has changed or the quantity is unavailable. Please review your bag.'),
 'cart_empty': ('سلتك فارغة. أضف منتجًا للمتابعة.', 'Your bag is empty. Add a product to continue.'),
 'cart_changed': ('تغيّرت الأسعار أو رسوم الشحن. راجع الملخص وحدّثه قبل التأكيد.', 'Prices or shipping have changed. Review the updated summary before confirming.'),
 'idempotency': ('استُخدم مفتاح هذا الطلب ببيانات مختلفة. حدّث ملخص الطلب.', 'This request key was used with different details. Please refresh checkout.'),
 'own_product': ('لا يمكنك شراء منتجات متجرك.', 'You cannot purchase from your own store.'),
 'invalid_status': ('حالة الطلب تغيّرت أو أن هذا الانتقال غير مسموح.', 'The order changed or this status transition is not allowed.'),
 'sku_exists': ('رمز SKU مستخدم في متجرك بالفعل.', 'This SKU is already used in your store.'),
 'media_type': ('ارفع صورة JPEG أو PNG أو WebP سليمة فقط.', 'Upload a valid JPEG, PNG or WebP image.'),
 'media_size': ('حجم الصورة يتجاوز 5 ميغابايت أو أبعادها كبيرة جدًا.', 'The image exceeds 5 MB or its dimensions are too large.'),
 'media_quota': ('بلغت حد الصور: 50 صورة أو 50 ميغابايت للحساب.', 'You reached the account limit of 50 images or 50 MB.'),
 'seller_limit': ('الحد الأولي 10 منتجات لكل متجر. تواصل مع الإدارة للتوسع.', 'The initial limit is 10 products per store. Contact an administrator to expand.'),
 'mfa_required': ('حساب الإدارة يتطلب التحقق الثنائي. استخدم رمز تطبيق المصادقة.', 'Administrator accounts require a code from your authenticator app.'),
 'reauth': ('أعد التحقق بكلمة المرور ورمز المصادقة قبل تغيير الإعدادات المالية.', 'Re-authenticate with your password and authenticator code before changing financial settings.'),
 'demo_disabled': ('لا يمكن تنفيذ هذا الإجراء على بيانات العرض.', 'This action is not allowed on illustrative data.'),
 'server_error': ('حدث خطأ داخلي. لم نتمكن من إكمال العملية، حاول مجددًا.', 'An internal error occurred. Please try again.'),
 'database_busy': ('الخادم مشغول حاليًا. أعد المحاولة بنفس الطلب.', 'The server is busy. Retry the same request.'),
 'self_suspend': ('لا يمكنك تعطيل حساب الإدارة الحالي.', 'You cannot disable your current administrator account.'),
}


def fail(code, status=400):
    raise HTTPException(status, detail={'code': code})


def language(request):
    return 'en' if request.headers.get('x-language', request.headers.get('accept-language', 'ar')).startswith('en') else 'ar'


def error_body(code, lang='ar', **extra):
    pair = MESSAGES.get(code, MESSAGES['invalid_input'])
    return {'code': code, 'message': pair[lang == 'en'], **extra}
