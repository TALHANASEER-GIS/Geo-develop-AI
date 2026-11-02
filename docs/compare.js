'use strict';
const el=id=>document.getElementById(id);
const ctx=el('viewer').getContext('2d');
const state={before:null,after:null,result:null,version:0};
const say=text=>el('status').textContent=text;
function buttons(enabled){['pdf','png','json'].forEach(id=>el(id).disabled=!enabled);}
function invalidate(){state.version++;state.result=null;buttons(false);el('changed').textContent='—';el('grid').textContent='—';el('reportMeta').textContent='Inputs or settings changed. Run comparison to refresh the report.';el('overlay').width=1;el('printBefore').removeAttribute('src');el('printAfter').removeAttribute('src');updateReady();draw();}
function updateReady(){el('compare').disabled=!(state.before&&state.after&&el('aligned').checked);}
function canvas(w,h){const c=document.createElement('canvas');c.width=w;c.height=h;return c;}
async function loadFile(side,file){
 if(!file)return;
 const token=++state.version;state[side]=null;invalidate();say('Loading '+side+' image…');
 try{
  if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('Use PNG, JPG or WebP. GeoTIFF is not supported yet.');
  if(file.size>20*1024*1024)throw Error('Please use an image under 20 MB.');
  const bitmap=await createImageBitmap(file);
  if(bitmap.width*bitmap.height>24000000){bitmap.close();throw Error('Please use an image under 24 megapixels.');}
  // A separate revision per input prevents a slow file from replacing a newer selection.
  if(file!==el(side+'File').files[0]){bitmap.close();return;}
  const c=canvas(bitmap.width,bitmap.height);c.getContext('2d').drawImage(bitmap,0,0);bitmap.close();
  state[side]={canvas:c,name:file.name,width:c.width,height:c.height,demo:false};
  el('aligned').checked=false;invalidate();say('Image loaded. Check both views and confirm alignment.');
 }catch(e){say(e.message);updateReady();}
}
['before','after'].forEach(side=>el(side+'File').addEventListener('change',e=>loadFile(side,e.target.files[0])));
function draw(){
 const a=state.before,b=state.after,v=el('viewer');el('empty').hidden=!!(a||b);
 const reference=a||b;if(!reference){ctx.clearRect(0,0,v.width,v.height);return;}
 const scale=Math.min(1,1200/reference.width);v.width=Math.round(reference.width*scale);v.height=Math.round(reference.height*scale);
 ctx.fillStyle='#e4ece5';ctx.fillRect(0,0,v.width,v.height);
 el('beforeName').textContent=a?'BEFORE · '+a.name+' · '+a.width+' × '+a.height:'BEFORE · not loaded';
 el('afterName').textContent=b?'AFTER · '+b.name+' · '+b.width+' × '+b.height:'AFTER · not loaded';
 const mode=el('view').value;
 if(mode==='difference'&&state.result){ctx.drawImage(el('overlay'),0,0,v.width,v.height);return;}
 if(mode==='before'||!b){if(a)ctx.drawImage(a.canvas,0,0,v.width,v.height);return;}
 if(mode==='after'||!a){ctx.drawImage(b.canvas,0,0,v.width,v.height);return;}
 ctx.drawImage(b.canvas,0,0,v.width,v.height);const split=v.width*Number(el('swipe').value)/100;
 ctx.save();ctx.beginPath();ctx.rect(0,0,split,v.height);ctx.clip();ctx.drawImage(a.canvas,0,0,v.width,v.height);ctx.restore();
 ctx.strokeStyle='white';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(split,0);ctx.lineTo(split,v.height);ctx.stroke();
}
function metadata(){return {...state.result?.meta,title:el('title').value.trim()||'Two-date image comparison',beforeDate:el('beforeDate').value||'Not supplied',afterDate:el('afterDate').value||'Not supplied',userNotes:el('notes').value};}
function refreshReport(){if(!state.result)return;const m=metadata();el('reportHeading').textContent=m.title;
 el('reportMeta').textContent=`${m.illustrative?'ILLUSTRATIVE SYNTHETIC SAMPLE — not satellite observations.\n':''}Before: ${m.beforeName} | acquisition: ${m.beforeDate}\nAfter: ${m.afterName} | acquisition: ${m.afterDate}\nGenerated: ${m.generated}\nMethod: mean absolute RGB difference > ${m.threshold}/255; images sampled to ${m.width} × ${m.height}.\nValid coverage: ${m.validPercent.toFixed(2)}% (pixels opaque in both images).\nAlignment: confirmed by user; not verified automatically. No geographic location supplied.`;
 el('printedNotes').textContent=el('notes').value?'User observations: '+el('notes').value:'';
}
function compare(){
 const a=state.before,b=state.after;if(!a||!b||!el('aligned').checked)return say('Load both images and confirm alignment.');
 if(a.width!==b.width||a.height!==b.height)return say('Images must have identical pixel dimensions. Crop and align them to the same extent first.');
 const bd=el('beforeDate').value,ad=el('afterDate').value;if(bd&&ad&&bd>ad)return say('The before acquisition date must not be later than the after date.');
 const scale=Math.min(1,1200/a.width,1200/a.height),w=Math.max(1,Math.round(a.width*scale)),h=Math.max(1,Math.round(a.height*scale));
 const ca=canvas(w,h),cb=canvas(w,h);ca.getContext('2d').drawImage(a.canvas,0,0,w,h);cb.getContext('2d').drawImage(b.canvas,0,0,w,h);
 const da=ca.getContext('2d').getImageData(0,0,w,h).data,db=cb.getContext('2d').getImageData(0,0,w,h).data;
 const overlay=el('overlay');overlay.width=w;overlay.height=h;const oc=overlay.getContext('2d');const image=oc.createImageData(w,h);let valid=0,changed=0;const threshold=Number(el('threshold').value);
 for(let i=0;i<da.length;i+=4){if(da[i+3]!==255||db[i+3]!==255)continue;valid++;const difference=(Math.abs(da[i]-db[i])+Math.abs(da[i+1]-db[i+1])+Math.abs(da[i+2]-db[i+2]))/3;const flag=difference>threshold;if(flag)changed++;for(let j=0;j<3;j++)image.data[i+j]=flag?[242,138,36][j]:Math.round(db[i+j]*.6);image.data[i+3]=255;}
 if(!valid)return say('No pixels are fully opaque in both images. Use opaque RGB images.');
 oc.putImageData(image,0,0);state.result={meta:{generated:new Date().toISOString(),beforeName:a.name,afterName:b.name,originalWidth:a.width,originalHeight:a.height,width:w,height:h,threshold,validPixels:valid,changedPixels:changed,changedPercent:100*changed/valid,validPercent:100*valid/(w*h),illustrative:a.demo||b.demo,method:'RGB absolute difference; not semantic building detection',sam3Connected:false}};
 el('changed').textContent=(100*changed/valid).toFixed(2)+'%';el('grid').textContent=w+' × '+h;
 el('printBefore').src=ca.toDataURL('image/png');el('printAfter').src=cb.toDataURL('image/png');buttons(true);el('view').value='difference';refreshReport();draw();say('Comparison complete. Orange marks image differences, not verified new buildings.');
}
el('compare').onclick=compare;el('swipe').oninput=draw;el('view').onchange=draw;
el('aligned').onchange=()=>{invalidate();};
el('threshold').oninput=()=>{el('thresholdValue').value=el('threshold').value;invalidate();};
['beforeDate','afterDate'].forEach(id=>el(id).onchange=invalidate);
['title','notes'].forEach(id=>el(id).oninput=refreshReport);
function download(blob,name){const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
el('pdf').onclick=()=>{refreshReport();window.print();};
el('json').onclick=()=>download(new Blob([JSON.stringify(metadata(),null,2)],{type:'application/json'}),'geodevelop-comparison-report.json');
el('png').onclick=()=>{const a=state.before,b=state.after;if(!state.result)return;const w=el('overlay').width,h=el('overlay').height,out=canvas(w*3,h+110),c=out.getContext('2d');c.fillStyle='white';c.fillRect(0,0,out.width,out.height);c.fillStyle='#193d35';c.font='bold 22px system-ui';c.fillText('GeoDevelop AI · Visual comparison · SAM 3 not connected',18,30);c.font='16px system-ui';['Before','After','RGB difference overlay'].forEach((t,i)=>c.fillText(t,i*w+15,62));[a.canvas,b.canvas,el('overlay')].forEach((v,i)=>c.drawImage(v,i*w,75,w,h));c.font='13px system-ui';c.fillText(state.result.meta.illustrative?'Illustrative synthetic sample — not satellite imagery.':'Orange indicates RGB differences, not verified building additions.',15,h+98);out.toBlob(blob=>download(blob,'geodevelop-comparison.png'));};
el('demo').onclick=()=>{['before','after'].forEach((side,index)=>{el(side+'File').value='';el(side+'Date').value='';const c=canvas(800,520),g=c.getContext('2d');g.fillStyle='#9da985';g.fillRect(0,0,800,520);g.fillStyle='#d5ccb7';g.fillRect(0,210,800,65);g.fillRect(365,0,60,520);g.fillStyle='#777d73';for(let y=40;y<490;y+=110)for(let x=40;x<760;x+=150){if(x>340&&x<430||y>180&&y<280)continue;g.fillRect(x,y,70,45);g.fillStyle='#bab4a4';g.fillRect(x-4,y-4,70,45);g.fillStyle='#777d73';}if(index){g.fillStyle='#c2b7a3';g.fillRect(490,300,100,65);g.fillRect(190,300,105,68);g.fillStyle='#668657';g.fillRect(600,100,75,75);}state[side]={canvas:c,width:800,height:520,name:'Illustrative '+side+' sample',demo:true};});el('aligned').checked=false;invalidate();say('Synthetic sample loaded. Check alignment and tick the confirmation to compare.');};
draw();
