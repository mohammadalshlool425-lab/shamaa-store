import {lang,tr} from './i18n.js';
let csrf='';
export const setCsrf = token => { csrf=token||''; };
export async function api(path,{method='GET',body,headers={}}={}) {
 const form=body instanceof FormData;
 let response;
 try {
  response=await fetch(path,{method,credentials:'same-origin',headers:{'X-Language':lang,...(method==='GET'?{}:{'X-Requested-With':'shamaa','X-CSRF-Token':csrf}),...(!form&&body?{'Content-Type':'application/json'}:{}),...headers},body:body?(form?body:JSON.stringify(body)):undefined});
 } catch {
  throw new Error(tr('تعذّر الاتصال. لم نؤكد العملية؛ تحقق من الإنترنت ثم أعد المحاولة.','Connection failed. The action is not confirmed; check your connection and retry.'));
 }
 let data;
 try { data=await response.json(); } catch { throw new Error(tr('تعذّرت قراءة رد الخادم. حاول مجددًا.','Unable to read the server response. Please retry.')); }
 if (!response.ok) {
  const error=new Error(data.message||tr('تعذّر إتمام العملية.','The action could not be completed.'));
  error.code=data.code; error.status=response.status; error.fields=data.fields||[];
  throw error;
 }
 return data;
}
