import {defineConfig} from '@playwright/test';
import chromium,{inflate,setupLambdaEnvironment} from '@sparticuz/chromium';
import {resolve} from 'node:path';
// Bundled browser fallback uses only the npm registry, useful in restricted CI.
// Remove flags that would bypass the browser's real web-security enforcement.
let launchOptions={};
if(process.env.SHAMAA_BUNDLED_BROWSER==='1'){
 await inflate(resolve('node_modules/@sparticuz/chromium/bin/al2023.tar.br'));
 setupLambdaEnvironment('/tmp/al2023/lib');
 launchOptions={executablePath:await chromium.executablePath(),args:chromium.args.filter(a=>!a.startsWith('--disable-web-security')&&!a.startsWith('--allow-running-insecure-content')&&!a.startsWith('--single-process')&&!a.startsWith('--disable-site-isolation-trials'))};
}
export default defineConfig({
 testDir:'./tests/browser',fullyParallel:false,workers:1,timeout:90000,
 expect:{timeout:20000},reporter:[['list'],['html',{open:'never'}]],
 use:{baseURL:'http://127.0.0.1:8011',locale:'ar-JO',viewport:{width:1440,height:1000},
      launchOptions,screenshot:'only-on-failure',trace:'retain-on-failure'},
 webServer:{command:(process.env.PYTHON||'.venv/bin/python')+' scripts/browser_test_server.py',
            url:'http://127.0.0.1:8011/healthz',reuseExistingServer:!process.env.CI,timeout:30000},
});
