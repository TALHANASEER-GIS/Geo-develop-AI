const $=id=>document.getElementById(id), status=message=>$('status').textContent=message;
const map=L.map('map').setView([31.48,74.31],13);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
const boundary=L.featureGroup().addTo(map),footprints=L.featureGroup().addTo(map);
map.addControl(new L.Control.Draw({edit:{featureGroup:boundary},draw:{polyline:false,circle:false,circlemarker:false,marker:false}}));
function resetResults(){footprints.clearLayers();$('results').textContent='Boundary or dates changed. Run a new scene search.';}
function setBoundary(geo){
 const features=geo.type==='FeatureCollection'?geo.features:[geo.type==='Feature'?geo:{type:'Feature',properties:{},geometry:geo}];
 if(!features.length||features.some(f=>!['Polygon','MultiPolygon'].includes(f.geometry?.type)))throw Error('Upload polygon GeoJSON in WGS84 longitude/latitude.');
 const candidate=L.geoJSON({type:'FeatureCollection',features});
 const b=candidate.getBounds();if(!b.isValid()||b.getWest() < -180||b.getEast()>180||b.getSouth() < -85||b.getNorth()>85)throw Error('Invalid WGS84 boundary.');
 boundary.clearLayers();candidate.eachLayer(l=>boundary.addLayer(l));map.fitBounds(b);resetResults();
}
function sample(){setBoundary({type:'Polygon',coordinates:[[[74.30,31.47],[74.32,31.47],[74.32,31.49],[74.30,31.49],[74.30,31.47]]]});status('Small Lahore test boundary selected (illustrative area, not an administrative boundary).');}sample();
map.on(L.Draw.Event.CREATED,e=>{boundary.clearLayers();boundary.addLayer(e.layer);resetResults();status('Boundary selected. Ready to search.');});
map.on('draw:edited draw:deleted',()=>{revision++;resetResults();status('Boundary updated.');});
$('sample').onclick=sample;
$('upload').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>2000000)throw Error('Please use a file under 2 MB.');setBoundary(JSON.parse(await f.text()));status('Boundary loaded.');}catch(err){status(err.message);}};
$('search').onclick=async()=>{const b=$('search');b.disabled=true;try{status('Searching location…');const q=$('place').value.trim();if(!q)throw Error('Enter a location.');const r=await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q='+encodeURIComponent(q));if(!r.ok)throw Error('Location service unavailable.');const places=await r.json();if(!places.length)throw Error('Location not found.');map.setView([+places[0].lat,+places[0].lon],13);status('Location found. Draw a boundary here; the selected boundary has not changed.');}catch(e){status(e.message);}finally{b.disabled=false;}};
let revision=0;
function invalidate(){revision++;resetResults();}
boundary.on('layeradd layerremove',()=>revision++);
$('start').onchange=invalidate;$('end').onchange=invalidate;
$('scenes').onclick=async()=>{const button=$('scenes');if(!boundary.getLayers().length)return status('Draw or upload a boundary first.');const b=boundary.getBounds(),current=revision;button.disabled=true;status('Searching the real Sentinel-2 catalog…');try{const r=await fetch('/api/scenes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bbox:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()],start:$('start').value,end:$('end').value})});const data=await r.json();if(!r.ok)throw Error(data.error);if(current!==revision)return status('Selection changed during search. Please search again.');footprints.clearLayers();const table=document.createElement('table');const header=table.insertRow();['Acquired (UTC)','Cloud %','Scene ID'].forEach(t=>{const th=document.createElement('th');th.textContent=t;header.append(th);});for(const s of data.scenes){const row=table.insertRow();[s.date.slice(0,10),s.cloud?.toFixed(1)??'Unknown',s.id].forEach(t=>row.insertCell().textContent=t);L.geoJSON(s.geometry,{style:{color:'#c28735',weight:1,fillOpacity:0}}).addTo(footprints);}$('results').replaceChildren(table);status(`${data.scenes.length} scene(s) returned, up to 30. Search uses the boundary bounding box. No NDVI has been calculated.`);}catch(e){status(e.message);}finally{button.disabled=false;}};
$('export').onclick=()=>{if(!boundary.getLayers().length)return status('Select a boundary first.');const url=URL.createObjectURL(new Blob([JSON.stringify(boundary.toGeoJSON(),null,2)],{type:'application/geo+json'}));const a=document.createElement('a');a.href=url;a.download='geodevelop-boundary.geojson';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
let firstMapVisible=false;
function refreshMapLayout(){if(!document.getElementById('map').getBoundingClientRect().width)return;map.invalidateSize();if(!firstMapVisible){firstMapVisible=true;if(boundary.getLayers().length)map.fitBounds(boundary.getBounds());}}
window.addEventListener('message',event=>{if(event.source===parent&&event.data?.type==='geodevelop-resize')refreshMapLayout();});
requestAnimationFrame(refreshMapLayout);
