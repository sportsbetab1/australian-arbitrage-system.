const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const handlers={};const notifications=[];
const context={URL,clients:{matchAll:async()=>[],openWindow:async()=>{}},self:{
 location:{origin:'https://example.invalid'},
 addEventListener:(name,handler)=>handlers[name]=handler,
 registration:{showNotification:async(title,options)=>notifications.push({title,options})}
}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/sw.js'),'utf8'),context);
(async()=>{
 for(const payload of [null,[],42,{}, {title:'A'.repeat(200),body:'B'.repeat(300)}]){
  let pending;handlers.push({data:{json:()=>payload},waitUntil:p=>pending=p});await pending;
 }
 let pending;handlers.push({data:{json:()=>{throw Error('invalid JSON')}},waitUntil:p=>pending=p});await pending;
 assert.equal(notifications.length,6);
 assert.equal(notifications[0].title,'Arbitrage alert');
 assert.equal(notifications[4].title.length,120);
 assert.equal(notifications[4].options.body.length,240);
 let focused=0,opened=0;
 context.clients.matchAll=async()=>[{url:'https://example.invalid/dashboard',focus:async()=>focused++}];
 context.clients.openWindow=async()=>opened++;
 handlers.notificationclick({notification:{close(){}},waitUntil:p=>pending=p});await pending;
 assert.equal(focused,1);assert.equal(opened,0);
 context.clients.matchAll=async()=>[];
 handlers.notificationclick({notification:{close(){}},waitUntil:p=>pending=p});await pending;
 assert.equal(opened,1);
 console.log('PASS: malformed push payloads, bounded text and existing-window reuse.');
})().catch(error=>{console.error(error);process.exit(1)});
