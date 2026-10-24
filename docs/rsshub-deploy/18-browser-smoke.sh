set -eu
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const page=await browser.newPage();
 await page.setContent("<title>RSSHub browser smoke test</title><p>ok</p>");
 console.log("Browser version:",browser.version());
 console.log("Page title:",await page.title());
} finally {await browser.close();}
'
