set -eu
for url in 'https://www.bilibili.com' 'https://search.bilibili.com/all?keyword=LangGraph' 'https://api.bilibili.com/x/web-interface/nav'; do
 curl -sS -o /dev/null --connect-timeout 5 --max-time 12 -w 'HTTP=%{http_code} time=%{time_total}\n' "$url" || true
done
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const page=await browser.newPage();
 page.on("response",r=>{const u=new URL(r.url()); if(u.hostname.endsWith("bilibili.com")&&(u.pathname.includes("search")||r.status()>=400))console.log("RESPONSE",r.status(),u.hostname,u.pathname);});
 page.on("requestfailed",r=>{const u=new URL(r.url());console.log("FAILED",u.hostname,u.pathname,r.failure()?.errorText);});
 try {const r=await page.goto("https://search.bilibili.com/all?keyword=LangGraph",{waitUntil:"domcontentloaded",timeout:20000});console.log("NAV",r?.status());await page.waitForTimeout(6000);console.log("TITLE",await page.title());console.log("TEXT",(await page.locator("body").innerText()).slice(0,900));}catch(e){console.log("ERROR",e.message.slice(0,300));}
}finally{await browser.close();}
'
