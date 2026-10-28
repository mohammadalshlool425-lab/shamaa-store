const queryLang = new URLSearchParams(location.search).get('lang');
let saved;
try { saved=localStorage.getItem('shamaa.lang'); } catch {}
export let lang = ['ar','en'].includes(queryLang) ? queryLang : ['ar','en'].includes(saved) ? saved : (navigator.language?.startsWith('en')?'en':'ar');
export const tr = (ar,en) => lang==='ar'?ar:en;
export const field = (obj,key) => obj?.[`${key}_${lang}`] || obj?.[`${key}_ar`] || '';
export function setLanguage(value) {
 lang=value==='en'?'en':'ar';
 try { localStorage.setItem('shamaa.lang',lang); } catch {}
 document.cookie=`shamaa_lang=${lang};path=/;max-age=31536000;SameSite=Lax`;
 document.documentElement.lang=lang;
 document.documentElement.dir=lang==='ar'?'rtl':'ltr';
}
export const money = n => new Intl.NumberFormat(lang==='ar'?'ar-JO':'en-JO',{minimumFractionDigits:2,maximumFractionDigits:3}).format((Number(n)||0)/1000);
export const number = n => new Intl.NumberFormat(lang==='ar'?'ar-JO':'en').format(n||0);
export const date = n => new Intl.DateTimeFormat(lang==='ar'?'ar-JO':'en-GB',{dateStyle:'medium',timeStyle:'short'}).format(n*1000);
export const currency = () => tr('د.أ','JOD');
export const status = value => ({new:tr('طلب جديد','New'),confirmed:tr('مؤكد','Confirmed'),preparing:tr('قيد التجهيز','Preparing'),shipped:tr('تم الشحن','Shipped'),delivered:tr('تم التسليم','Delivered'),cancelled:tr('ملغى','Cancelled'),pending:tr('قيد المراجعة','In review'),active:tr('نشط','Active'),paused:tr('متوقف','Paused'),rejected:tr('مرفوض','Not approved'),draft:tr('مسودة','Draft'),suspended:tr('معلّق','Suspended')})[value]||value;
export function fils(value) {
 const normalized=String(value).trim();
 if (!/^\d{1,9}(\.\d{1,3})?$/.test(normalized)) throw new Error(tr('أدخل مبلغًا صحيحًا بثلاث منازل عشرية كحد أقصى.','Enter a valid amount with up to three decimal places.'));
 const [whole,fraction='']=normalized.split('.');
 return Number(whole)*1000+Number(fraction.padEnd(3,'0'));
}
setLanguage(lang);
