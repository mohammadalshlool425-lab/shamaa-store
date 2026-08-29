import streamlit as st

# إعدادات الصفحة وتصميم واجهة نظيفة ومريحة
st.set_page_config(page_title="منصة الشامل الذكية", page_icon="🌟", layout="wide")

st.markdown("<h1 style='text-align: center; color: #2c3e50;'>🌟 منصة الشامل الذكية 🌟</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #7f8c8d;'>المتجر والإعلانات المبوبة الأسرع والأذكى - بديل عصري وآمن ومجاني 100%</p>", unsafe_allow_html=True)
st.write("---")

# القائمة الجانبية للتنقل السلس بين الأقسام
choice = st.sidebar.radio("القائمة الرئيسية", [
    "🏠 تصفح المنتجات والمقايضة", 
    "🤖 مساعد الذكاء الاصطناعي للتسعير", 
    "➕ إضافة إعلان جديد", 
    "🛡️ ضمان حقوق المستخدمين"
])

# قاعدة بيانات مؤقتة في الذاكرة لتجربة المنصة فوراً
if 'products' not in st.session_state:
    st.session_state.products = [
        {"title": "لابتوب جيرلاب بحالة ممتازة", "price": 220, "category": "إلكترونيات", "barter": True, "user": "أحمد الشلول"},
        {"title": "دراجة هوائية رياضية للتبادل", "price": 60, "category": "رياضة وترفيه", "barter": True, "user": "محمد فراس"},
        {"title": "طقم صالون خشب زان نظيف", "price": 150, "category": "أثاث منزلي", "barter": False, "user": "خالد"},
    ]

# 1. قسم تصفح المنتجات والمقايضة
if choice == "🏠 تصفح المنتجات والمقايضة":
    st.subheader("📦 أحدث الإعلانات والسلع المتوفرة")
    
    col1, col2 = st.columns(2)
    with col1:
        search_query = st.text_input("🔍 ابحث عن أي منتج تريده...")
    with col2:
        filter_barter = st.checkbox("🔄 عرض المنتجات القابلة للمقايضة والتبادل فقط")

    st.write("---")
    
    for p in st.session_state.products:
        if search_query and search_query not in p['title']:
            continue
        if filter_barter and not p['barter']:
            continue
            
        with st.container():
            st.markdown(f"""
            ### 🏷️ {p['title']}
            * **السعر المقدر:** {p['price']} دينار أردني
            * **القسم:** {p['category']}
            * **خاصية المقايضة:** {'✅ متاح للتبادل والمقايضة' if p['barter'] else '❌ بيع نقدي فقط'}
            * **صاحب الإعلان:** {p['user']}
            """)
            if st.button(f"تواصل لشراء أو مقايضة: {p['title']}", key=p['title']):
                st.success("✨ تم فتح نافذة التواصل الآمن مع البائع بنجاح!")
            st.markdown("---")

# 2. قسم مساعد الذكاء الاصطناعي للتسعير
elif choice == "🤖 مساعد الذكاء الاصطناعي للتسعير":
    st.subheader("🤖 مساعد الذكاء الاصطناعي لتسعير منتجك بعدالة")
    st.write("هل تحتار في سعر السلعة التي تريد بيعها؟ أدخل تفاصيلها وسيقوم الذكاء الاصطناعي بتحليل السوق وإعطائك السعر المناسب تماماً:")
    
    item_name = st.text_input("اسم المنتج أو وصفه (مثلاً: بلايستيشن 4 مستعمل مع يدين)")
    item_category = st.selectbox("القسم الرئيسي", ["إلكترونيات", "سيارات", "أثاث منزلي", "أجهزة كهربائية", "أخرى"])
    item_condition = st.slider("حالة المنتج (من 1 إلى 10 حيث 10 يعني جديد تماماً)", 1, 10, 8)
    
    if st.button("احسب السعر العادل بالذكاء الاصطناعي 🚀"):
        if item_name:
            estimated_price = item_condition * 18
            st.success(f"✨ السعر المقترح العادل لمنتجك ({item_name}) هو ما بين **{estimated_price}** إلى **{estimated_price + 35}** دينار.")
            st.info("💡 ميزة ذكية: التسعير ضمن هذا النطاق يرفع فرصة بيع منتجك أو مقايضته بنسبة كبيرة جداً!")
        else:
            st.warning("الرجاء كتابة اسم أو وصف المنتج أولاً.")

# 3. قسم إضافة إعلان جديد
elif choice == "➕ إضافة إعلان جديد":
    st.subheader("➕ أضف إعلانك الجديد في ثوانٍ معدودة")
    
    with st.form("new_ad"):
        new_title = st.text_input("عنوان الإعلان بوضوح")
        new_price = st.number_input("السعر المتوقع (بالدينار)", min_value=0, value=50)
        new_category = st.selectbox("اختر القسم المناسب", ["إلكترونيات", "سيارات", "أثاث منزلي", "أجهزة كهربائية", "أخرى"])
        accepts_barter_val = st.checkbox("🔄 أقبل المقايضة والتبادل بسلع أخرى")
        seller_name = st.text_input("اسمك الكريم أو اسم المعرف")
        
        submit_btn = st.form_submit_button("نشر الإعلان على المنصة فوراً 🚀")
        
        if submit_btn and new_title:
            st.session_state.products.append({
                "title": new_title,
                "price": new_price,
                "category": new_category,
                "barter": accepts_barter_val,
                "user": seller_name or "مستخدم جديد"
            })
            st.success("🎉 مبروك! تمت إضافة إعلانك وأصبح مرئياً لجميع المستخدمين على المنصة.")

# 4. قسم الضمان والأمان
elif choice == "🛡️ ضمان حقوق المستخدمين":
    st.subheader("🛡️ نظام الضمان والأمان المطور")
    st.markdown("""
    * **بيئة خالية من الإعلانات المزعجة:** تصفح نظيف وسريع جداً على عكس التطبيقات والمنصات البطيئة.
    * **توثيق المقايضة:** حماية حقوق الطرفين عند التبادل السلعي لضمان الأمان والثقة التامة.
    * **دعم ذكي مستمر:** إمكانية تقييم المستخدمين وبناء سمعة طيبة داخل المنصة بكل شفافية.
    """)
