// Unit tests for the spray-windows logic embedded in index.html (between /*SPRAY-START*/ and /*SPRAY-END*/).
// Usage: node scripts/test_spray_windows.js [path/to/index.html]
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const html=fs.readFileSync(process.argv[2]||path.join(__dirname,'..','index.html'),'utf8');
const src=html.slice(html.indexOf('/*SPRAY-START*/'),html.indexOf('/*SPRAY-END*/'));assert.ok(src.length>1000,'spray module not found');
const eq=(a,b,m)=>assert.strictEqual(JSON.stringify(a),JSON.stringify(b),m);
const ctx={};vm.runInNewContext(src,ctx);const S=ctx.svSprayCore,T=S.T;assert.ok(S,'svSprayCore missing');
const w10=w2=>w2/T.W2M; // forecast wind is 10 m; logic works at 2 m
const hr=(o={})=>Object.assign({H:'10:00',t:[22,22.4,21.8],rh:60,w:w10(8),gu:w10(14),rv:0,r:[0,0,0],p:5,day:1},o);
const C=(o,ahead)=>S.classify(hr(o),ahead);
// Delta T (Stull wet bulb): 25 °C / 50 % ≈ 7 °C; 30 °C / 20 % > 10; 15 °C / 95 % < 2
const d=S.deltaT(25,50);assert.ok(d>6.5&&d<7.6,d);assert.ok(S.deltaT(30,20)>10);assert.ok(S.deltaT(15,95)<2);
// ideal hour
let o=C();assert.strictEqual(o.c,'good',JSON.stringify(o));assert.ok(Math.abs(o.w2-8)<1e-9);
assert.strictEqual(C({day:0}).c,'night');
// wind at 2 m: ≥15 not suitable, 12–15 marginal, <3 calm (inversion) not suitable
eq([C({w:w10(16)}).c,C({w:w10(16)}).why[0]],['no','wind']);
eq([C({w:w10(13)}).c,C({w:w10(13)}).why[0]],['marg','windm']);
eq([C({w:w10(2)}).c,C({w:w10(2)}).why[0]],['no','calm']);
assert.strictEqual(C({w:w10(3.2)}).c,'good');
// gusts at 2 m: ≥25 no, 20–25 marginal
assert.strictEqual(C({gu:w10(26)}).c,'no');assert.strictEqual(C({gu:w10(22)}).c,'marg');
// rain in the hour / probability
assert.strictEqual(C({rv:0.4}).c,'no');assert.strictEqual(C({rv:0.15}).c,'marg');assert.strictEqual(C({p:60}).c,'no');assert.strictEqual(C({p:35}).c,'marg');
assert.strictEqual(C({rv:undefined,r:[0.5,0.6,0.4]}).c,'no','falls back to the model median when no scaled hourly rain');
// rain-fastness: rain in the next 2 h -> no; only in the 3rd hour -> marginal; small chance later -> fine
assert.strictEqual(C({},[hr(),hr({rv:0.5})]).why.includes('rainsoon'),true);
assert.strictEqual(C({},[hr(),hr(),hr({rv:0.5})]).c,'marg');
assert.strictEqual(C({},[hr(),hr(),hr(),hr({rv:3})]).c,'good');
// temperature and Delta T
assert.strictEqual(C({t:[31,31,31],rh:70}).c,'no');assert.strictEqual(C({t:[29,29,29],rh:75}).c,'marg');
assert.strictEqual(C({t:[27,27,27],rh:15}).why.includes('dry'),true);
o=C({t:[18,18,18],rh:96});eq([o.c,o.why[0]],['marg','humid']);
assert.strictEqual(C({w:null}).c,'no','missing data is never suitable');
// windows: merge consecutive good/marginal hours, keep runs ≥2 h, skip past and night hours
const mk=(cs,past=-1)=>cs.split('').map((c,i)=>({h:i,c:{G:'good',M:'marg',N:'no',D:'night'}[c],why:c==='M'?['windm']:[],past:i<past}));
let W=S.windows(mk('DDDDDDGGMNGNGGGMMDD'));
eq(W.map(w=>[w.a,w.b,w.kind]),[[6,9,'mostly'],[12,17,'mostly']]);
W=S.windows(mk('DDDDDDGGGGNMMDD'));eq(W.map(w=>[w.a,w.b,w.kind]),[[6,10,'good'],[11,13,'marg']]);eq(W[1].why,['windm']);
W=S.windows(mk('DDDDDDGGGGGGDD',9));eq(W.map(w=>[w.a,w.b]),[[9,12]],'past hours of today are not offered');
assert.strictEqual(S.windows(mk('DDDDDDGNGNGND')).length,0,'single hours are ignored');
// analyse: a full day, rain at 13 h blocks 11–13; look-ahead crosses into the next day
const day=Array.from({length:24},(_,h)=>hr({H:String(h).padStart(2,'0')+':00',day:h>=6&&h<20?1:0,rv:h===13?1.2:0}));
let A=S.analyse(day,[],null);eq(A.W.map(w=>[w.a,w.b,w.kind]),[[6,11,"mostly"],[14,20,"good"]]);eq(A.cls[10].why,["rainsoonm"],"rain 3 h ahead = marginal");
A=S.analyse(day,[],15);eq(A.W.map(w=>[w.a,w.b]),[[15,20]]);
const late=day.map(x=>Object.assign({},x,{day:1}));A=S.analyse(late,[hr({H:'00:00',rv:2})],null);assert.ok(A.cls[23].why.includes('rainsoon'),'rain just after midnight blocks 23 h');
console.log('spray windows: all checks passed');
