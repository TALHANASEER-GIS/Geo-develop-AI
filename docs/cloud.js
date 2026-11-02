// Hosted version uses the public STAC API directly; no local Python service.
$('scenes').onclick=async()=>{
 const button=$('scenes');if(!boundary.getLayers().length)return status('Draw or upload a boundary first.');
 const b=boundary.getBounds(),bbox=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()];
 if(bbox.some(x=>!Number.isFinite(x))||b.getEast()-b.getWest()>.15||b.getNorth()-b.getSouth()>.15)return status('Use a small area under roughly 15 km across.');
 const start=$('start').value,end=$('end').value;
 if(!start||!end||start>end||end>new Date().toISOString().slice(0,10))return status('Choose an ordered date range ending today or earlier.');
 const current=revision;button.disabled=true;status('Searching the live Sentinel-2 catalog…');
 try{
  const r=await fetch('https://planetarycomputer.microsoft.com/api/stac/v1/search',{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(45000),body:JSON.stringify({collections:['sentinel-2-l2a'],bbox,datetime:`${start}T00:00:00Z/${end}T23:59:59Z`,limit:30,query:{'eo:cloud_cover':{lt:30}}})});
  if(!r.ok)throw Error(`Satellite catalog returned ${r.status}. Please retry.`);
  const data=await r.json();if(current!==revision)return status('Selection changed. Please search again.');
  footprints.clearLayers();const table=document.createElement('table');const head=table.insertRow();
  ['Acquired (UTC)','Cloud %','Scene ID'].forEach(t=>{const th=document.createElement('th');th.textContent=t;head.append(th);});
  for(const f of data.features){const p=f.properties,row=table.insertRow();[p.datetime.slice(0,10),typeof p['eo:cloud_cover']==='number'?p['eo:cloud_cover'].toFixed(1):'Unknown',f.id].forEach(t=>row.insertCell().textContent=t);L.geoJSON(f.geometry,{style:{color:'#c28735',weight:1,fillOpacity:0}}).addTo(footprints);}
  $('results').replaceChildren(table);status(`${data.features.length} real scene(s) returned (maximum 30). No vegetation change has been calculated.`);
 }catch(e){status(e.name==='TimeoutError'?'Catalog timed out. Retry with a shorter date range.':'Could not load scenes. Check internet or catalog availability. '+e.message);}finally{button.disabled=false;}
};
$('plan').onclick=()=>{
 const q=$('prompt').value.trim(),dates=q.match(/\b\d{4}-\d{2}-\d{2}\b/g)||[];
 const unsupported=/ndvi|vegetation|temperature|warming|building|road|change|loss|gain/i.test(q);
 if(unsupported){$('planResult').textContent='This request needs an analysis module that is not connected yet. You can search the imagery archive to check available observations first.';return;}
 if(!/scene|sentinel|image|imagery/i.test(q)){$('planResult').textContent='Try: “Find Sentinel-2 scenes for the selected area from 2024-03-01 to 2024-04-30.” This is a simple search helper, not an LLM.';return;}
 if(dates.length!==2||dates.some(s=>{const d=new Date(s+'T00:00:00Z');return Number.isNaN(d.valueOf())||d.toISOString().slice(0,10)!==s;})||dates[0]>dates[1]){$('planResult').textContent='Include two valid ISO dates in order: YYYY-MM-DD to YYYY-MM-DD.';return;}
 $('start').value=dates[0];$('end').value=dates[1];invalidate();
 $('planResult').textContent=`Prepared: Sentinel-2 L2A scene search, ${dates[0]} through ${dates[1]}, cloud cover under 30%, current map boundary. Check the selected area, then click Find Sentinel-2 scenes. Place names in this text do not move the boundary.`;
};
