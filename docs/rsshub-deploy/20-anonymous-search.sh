set -eu
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
import fs from "node:fs";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const context=await browser.newContext();const page=await context.newPage();
 await page.goto("https://search.bilibili.com/all?keyword=LangGraph",{waitUntil:"domcontentloaded",timeout:20000});
 await page.waitForTimeout(2000);
 const cookies=await context.cookies();
 const r=await context.request.get("https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=LangGraph&order=pubdate",{headers:{Referer:"https://search.bilibili.com/all?keyword=LangGraph"},timeout:15000});
 const data=await r.json();console.log("HTTP",r.status(),"API_CODE",data.code,"RESULT_COUNT",data.data?.result?.length??0,"COOKIE_COUNT",cookies.length);
 if(data.code!==0||!data.data?.result?.length)process.exitCode=1;
 else{fs.writeFileSync("/tmp/rsshub-bili-anonymous.env","BILIBILI_COOKIE_0="+cookies.map(c=>c.name+"="+c.value).join("; ")+"\n",{mode:0o600});console.log("Anonymous session saved privately; cookie values omitted.");}
}finally{await browser.close();}
'
