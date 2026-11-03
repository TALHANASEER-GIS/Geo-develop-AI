"""Authenticated GeoDevelop API. Run: uvicorn backend.app:app --host 127.0.0.1 --port 8765."""
import os,json,uuid,secrets,hmac,threading,datetime,importlib.util,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import httpx,numpy as np,rasterio
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from .raster import metadata,Pair,raster_profile,pixel_area_km2
from . import analysis,satellite,agent
from .boundary import validate_geometry

ROOT=Path(os.getenv('GEODEVELOP_DATA','runtime-data')).resolve();ROOT.mkdir(parents=True,exist_ok=True)
TOKEN=os.getenv('GEODEVELOP_API_KEY') or secrets.token_urlsafe(32)
POOL=ThreadPoolExecutor(max_workers=1);STATE_LOCK=threading.RLock()
MAX_UPLOAD=int(os.getenv('GEODEVELOP_MAX_UPLOAD_BYTES',str(5*1024**3)))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def auth(authorization:str=Header(default='')):
    if not hmac.compare_digest(authorization,'Bearer '+TOKEN):raise HTTPException(401,'Connect using the runtime access key.')
def write(path,value):
    with STATE_LOCK:
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2,default=str),encoding='utf8');temp.replace(path)
def load(path):
    with STATE_LOCK:return json.loads(path.read_text(encoding='utf8'))
def identifier(s):
    if len(s)!=32 or any(c not in '0123456789abcdef' for c in s):raise HTTPException(404,'Unknown ID')
    return s
def asset(id):
    p=ROOT/'assets'/identifier(id)/'metadata.json'
    if not p.exists():raise HTTPException(404,'Unknown asset')
    return load(p)
def jobdir(id):
    p=ROOT/'jobs'/identifier(id)
    if not (p/'job.json').exists():raise HTTPException(404,'Unknown job')
    return p
def assets():return [load(p) for p in (ROOT/'assets').glob('*/metadata.json')]
def jobs():return [load(p) for p in (ROOT/'jobs').glob('*/job.json')]

@asynccontextmanager
async def lifespan(app):
    for j in jobs():
        if j['status'] in {'queued','running'}:
            j.update(status='interrupted',message='Runtime restarted before completion. Submit again; partial outputs are not completed results.');write(jobdir(j['id'])/'job.json',j)
    if not os.getenv('GEODEVELOP_API_KEY'):
        print('GeoDevelop runtime access key (keep private): '+TOKEN)
    yield

app=FastAPI(title='GeoDevelop AI Processing',version='0.3.0',lifespan=lifespan)
origins=os.getenv('GEODEVELOP_ORIGINS','https://talhanaseer-gis.github.io,http://127.0.0.1:8765,http://localhost:8765,http://127.0.0.1:8766').split(',')
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type','X-Filename'])

class JobSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:str=Field(pattern='^(align|buildings|roads|flood|temperature|exposure)$')
    before:str=''
    after:str=''
    options:dict=Field(default_factory=dict)
class ChatSpec(BaseModel):history:list[dict]=Field(max_length=16)
class ReviewSpec(BaseModel):
    feature_id:str=Field(max_length=100)
    decision:str=Field(pattern='^(accepted|rejected|uncertain)$')
    label:str=Field(pattern='^(building|exposed|damaged|undamaged)$')
    reviewer:str=Field(min_length=1,max_length=120)
    evidence:str=Field(min_length=8,max_length=2000)
    notes:str=Field(default='',max_length=3000)
class SatelliteSpec(BaseModel):
    kind:str=Field(pattern='^(flood|temperature)$')
    bbox:list[float]=Field(min_length=4,max_length=4)
    geometry:dict|None=None
    start_before:str;end_before:str;start_after:str;end_after:str

@app.get('/health')
def health():return dict(status='online',version='0.3.0',polygonSupported=True,gpuAdapterInstalled=importlib.util.find_spec('sam3') is not None,earthEngineConfigured=bool(os.getenv('EE_PROJECT')),languageModelConfigured=bool(os.getenv('GEODEVELOP_LLM_URL') or os.getenv('GEODEVELOP_LLM_PROVIDER')),authentication='Bearer key required')
@app.get('/api/assets',dependencies=[Depends(auth)])
def list_assets():return assets()
@app.get('/api/jobs',dependencies=[Depends(auth)])
def list_jobs():return jobs()
@app.post('/api/assets',dependencies=[Depends(auth)])
async def upload(request:Request,x_filename:str=Header(default='raster.tif')):
    ext=Path(x_filename).suffix.lower()
    if ext not in {'.tif','.tiff','.png','.jpg','.jpeg','.webp'}:raise HTTPException(400,'Upload a raster image, not a ZIP or raw drone flight.')
    id=uuid.uuid4().hex;p=ROOT/'assets'/id;p.mkdir(parents=True)
    target=p/('input'+ext);total=0;digest=hashlib.sha256()
    try:
        with target.open('wb') as f:
            async for chunk in request.stream():
                total+=len(chunk)
                if total>MAX_UPLOAD:raise ValueError('Upload exceeds the configured limit (default 5 GiB).')
                f.write(chunk);digest.update(chunk)
        info=metadata(target);info.update(id=id,name=Path(x_filename).name[:200],bytes=total,sha256=digest.hexdigest(),created=now())
        write(p/'metadata.json',info);return info
    except Exception as e:
        target.unlink(missing_ok=True)
        raise HTTPException(400,str(e)[:400]) from None
def path_for(id):
    asset(id)
    return next((ROOT/'assets'/id).glob('input.*'))

def submit(spec,extra=None):
    if spec.options.get('geometry'):
        spec.options['geometry'], spec.options['bbox']=validate_geometry(spec.options['geometry'])
    if spec.kind=='exposure':
        for key in ['building_job','flood_job']:jobdir(spec.options.get(key,''))
    elif not extra:path_for(spec.before);path_for(spec.after)
    id=uuid.uuid4().hex;p=ROOT/'jobs'/id;p.mkdir(parents=True)
    record=dict(id=id,status='queued',kind=spec.kind,created=now(),message='Queued',spec=spec.model_dump())
    write(p/'job.json',record);POOL.submit(worker,id,spec,extra);return record
@app.post('/api/jobs',dependencies=[Depends(auth)])
def create_job(spec:JobSpec):return submit(spec)
@app.post('/api/satellite-jobs',dependencies=[Depends(auth)])
def create_satellite_job(spec:SatelliteSpec):
    try:
        satellite.bounds(spec.bbox)
        if spec.geometry:
            spec.geometry, polygon_bounds=validate_geometry(spec.geometry)
            if any(abs(a-b)>1e-6 for a,b in zip(spec.bbox,polygon_bounds)):raise ValueError('Polygon and bounding box disagree.')
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return submit(JobSpec(kind=spec.kind,options={}),spec.model_dump())
@app.get('/api/jobs/{id}',dependencies=[Depends(auth)])
def get_job(id:str):return load(jobdir(id)/'job.json')
@app.get('/api/jobs/{id}/files/{name}',dependencies=[Depends(auth)])
def get_file(id:str,name:str):
    p=jobdir(id)
    if name not in {f.name for f in p.iterdir() if f.is_file() and f.suffix in {'.tif','.png','.jpg','.json','.geojson','.pdf','.zip'}}:raise HTTPException(404,'Unknown artifact')
    return FileResponse(p/name,filename=name)

def exposure(spec,out):
    from shapely.geometry import shape,mapping
    from shapely.ops import transform as geom_transform
    from pyproj import Transformer
    from rasterio.features import geometry_mask,geometry_window
    from affine import Affine
    building=jobdir(spec.options['building_job']);flood=jobdir(spec.options['flood_job'])
    bj=load(building/'job.json');fj=load(flood/'job.json')
    if bj['status']!='completed' or fj['status']!='completed' or bj['kind']!='buildings' or fj['kind']!='flood':raise ValueError('Exposure needs completed building and flood jobs.')
    grid=load(building/'grid.json');features=load(building/'instances.geojson')['features']
    if grid['crs']=='None':raise ValueError('Building footprints need georeferencing for flood exposure.')
    t=Affine(*grid['transform']);result=[]
    with rasterio.open(flood/'change.tif') as src:
        tr=Transformer.from_crs(grid['crs'],src.crs,always_xy=True)
        for f in features:
            if f['properties']['epoch']!='after':continue
            def convert(x,y,z=None):
                xx=np.asarray(x);yy=np.asarray(y)
                return tr.transform(t.a*xx+t.b*yy+t.c,t.d*xx+t.e*yy+t.f)
            g=geom_transform(convert,shape(f['geometry']))
            try:
                win=geometry_window(src,[mapping(g)])
                if win.width*win.height>16_000_000:raise ValueError('Footprint window too large.')
                a=src.read(1,window=win);inside=geometry_mask([mapping(g)],out_shape=a.shape,transform=src.window_transform(win),invert=True)
                valid=inside&(a!=255);flooded=int(((a==1)&inside).sum())
                props=dict(f['properties'],floodPixels=flooded,validPixels=int(valid.sum()),exposure='candidate exposed' if flooded else ('not detected exposed' if valid.any() else 'unknown - masked'))
            except rasterio.errors.WindowError:props=dict(f['properties'],exposure='unknown - outside flood coverage')
            result.append(dict(type='Feature',id=f['id'],geometry=mapping(g),properties=props))
    write(out/'exposure.geojson',dict(type='FeatureCollection',crs={'type':'name','properties':{'name':str(src.crs)}},features=result))
    return dict(kind='exposure',status='completed',stats=dict(candidateExposedBuildings=sum(f['properties']['exposure']=='candidate exposed' for f in result),verifiedDamageCount=0),limitations=['Overlap indicates potential flood exposure, not structural damage. Review acquisition dates, coverage and footprint accuracy.'])

def satellite_analysis(extra,out,progress):
    pair,provenance=satellite.gee_pair(out=out,**extra)
    # Preserve EE QA masks and physical units in two separate local assets.
    with rasterio.open(pair) as src:
        profile=src.profile;profile.update(count=1,nodata=-9999)
        for index,name in [(1,'sat_before.tif'),(2,'sat_after.tif')]:
            with rasterio.open(out/name,'w',**profile) as dst:
                for _,win in src.block_windows(index):
                    a=src.read(index,window=win,masked=True);dst.write(np.where(np.ma.getmaskarray(a)|(a.data==-9999),-9999,a.data),1,window=win)
    if extra['kind']=='flood':
        result=analysis.run('flood',out/'sat_before.tif',out/'sat_after.tif',out,dict(sar_confirmed=True,relative_orbit_before=provenance['relativeOrbit'],relative_orbit_after=provenance['relativeOrbit']),progress)
    else:
        with Pair(out/'sat_before.tif',out/'sat_after.tif') as p,rasterio.open(out/'change.tif','w',**raster_profile(p,dtype='float32',nodata=-9999)) as dst:
            count=0;bs=0.;ads=0.
            for win in p.windows():
                b,vb=p.read(p.before,win,bands=[1]);a,va=p.read(p.after,win,bands=[1]);v=vb&va
                dst.write(np.where(v,a[0]-b[0],-9999).astype('float32'),1,window=win)
                count+=int(v.sum());bs+=float(b[0][v].sum());ads+=float(a[0][v].sum())
            if not count:raise ValueError('No common clear Landsat temperature pixels.')
        result=dict(kind='temperature',status='completed',stats=dict(validPixels=count,meanBeforeC=bs/count,meanAfterC=ads/count,meanDeltaC=(ads-bs)/count),limitations=['Surface temperature, not air temperature. Two periods do not establish a climate trend. QA gaps and seasonal differences affect interpretation.'])
    result['satelliteProvenance']=provenance;return result

def worker(id,spec,extra):
    p=ROOT/'jobs'/id;record=load(p/'job.json')
    def progress(message):record.update(message=message,updated=now());write(p/'job.json',record)
    try:
        record['status']='running';progress('Starting analysis')
        if extra:result=satellite_analysis(extra,p,progress)
        elif spec.kind=='exposure':result=exposure(spec,p)
        else:
            options=dict(spec.options)
            for role in ['qa_before','qa_after']:
                if options.get(role):options[role]=str(path_for(options[role]))
            result=analysis.run(spec.kind,path_for(spec.before),path_for(spec.after),p,options,progress)
        result.update(jobId=id,generated=now(),reviewStatus='unverified')
        if not extra and spec.kind!='exposure':result['inputAssets']={'before':asset(spec.before),'after':asset(spec.after)}
        if extra:result['satelliteRequest']=extra
        if (p/'change.tif').exists() and spec.kind!='align':
            result['mapContext']=analysis.map_preview(p/'change.tif',p/'map_preview.png')
        write(p/'result.json',result)
        if (p/'change.tif').exists() and spec.kind!='align':analysis.preview(p/'change.tif',p/'preview.png')
        record.update(status='completed',message='Completed; predictions require review',result=result,files=[f.name for f in p.iterdir() if f.suffix in {'.tif','.png','.geojson','.json'}]);write(p/'job.json',record)
    except Exception as e:
        # Errors are bounded; credentials and signed URLs are never sent to the browser.
        message=str(e).split('https://')[0][:500]
        record.update(status='failed',message=message or type(e).__name__,updated=now());write(p/'job.json',record)

@app.post('/api/jobs/{id}/reviews',dependencies=[Depends(auth)])
def review(id:str,spec:ReviewSpec):
    p=jobdir(id);j=load(p/'job.json')
    if j['status']!='completed':raise HTTPException(400,'Review requires a completed job.')
    fp=p/('instances.geojson' if j['kind']=='buildings' else 'exposure.geojson')
    if not fp.exists():raise HTTPException(400,'This job has no reviewable building instances.')
    feature_lookup={str(f['id']):f for f in load(fp)['features']}
    if spec.feature_id not in feature_lookup:raise HTTPException(400,'Unknown feature ID.')
    entry=dict(spec.model_dump(),recorded=now(),verification='human-reviewed assertion, not independent automated validation')
    with STATE_LOCK:
        reviews=load(p/'reviews.json') if (p/'reviews.json').exists() else []
        exclusive={'damaged','undamaged'} if spec.label in {'damaged','undamaged'} else {spec.label}
        reviews=[r for r in reviews if not(r['feature_id']==spec.feature_id and r['label'] in exclusive)]+[entry]
        write(p/'reviews.json',reviews)
    counts={label:sum(r['decision']=='accepted' and r['label']==label for r in reviews) for label in ['building','exposed','damaged','undamaged']}
    by_epoch={epoch:sum(r['decision']=='accepted' and r['label']=='building' and feature_lookup[r['feature_id']].get('properties',{}).get('epoch')==epoch for r in reviews) for epoch in ['before','after']}
    return dict(review=entry,humanConfirmedCounts=counts,humanConfirmedBuildingsByEpoch=by_epoch,limitations='Counts reflect the recorded human assertions and evidence. They are not independent ground-truth validation.')

@app.post('/api/chat',dependencies=[Depends(auth)])
def chat(spec:ChatSpec):
    def dispatch(name,args):
        if name=='list_assets':return assets()
        if name=='list_jobs':return jobs()
        if name=='search_scenes':return satellite.scene_search(**args)
        if name=='prepare_analysis':
            proposed=JobSpec(**args);path_for(proposed.before);path_for(proposed.after)
            return proposed.model_dump()
        if name=='find_location':
            query=str(args.get('query',''))[:200]
            response=httpx.get('https://nominatim.openstreetmap.org/search',params={'q':query,'format':'jsonv2','limit':3},headers={'User-Agent':'GeoDevelopAI-prototype/0.2'},timeout=30);response.raise_for_status()
            return [dict(name=p['display_name'],lat=p['lat'],lon=p['lon'],bbox=p['boundingbox']) for p in response.json()]
        raise ValueError('Unknown tool')
    return agent.converse(spec.history,dispatch)

@app.post('/api/jobs/{id}/report',dependencies=[Depends(auth)])
def report(id:str):
    p=jobdir(id);record=load(p/'job.json')
    if record['status']!='completed':raise HTTPException(400,'Only completed jobs can be reported.')
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Image as PDFImage
    from reportlab.lib.styles import getSampleStyleSheet
    from xml.sax.saxutils import escape
    styles=getSampleStyleSheet();story=[Paragraph('GeoDevelop AI analysis report',styles['Title'])]
    for line in [f"Analysis: {record['kind']}",f"Job: {id}",f"Generated: {now()}",'Candidate model outputs; verify dates, alignment and evidence before decisions.']:
        story.extend([Paragraph(escape(line),styles['BodyText']),Spacer(1,8)])
    for k,v in record['result'].get('stats',{}).items():story.append(Paragraph(escape(f'{k}: {v}'),styles['BodyText']))
    if (p/'preview.png').exists():
        from PIL import Image
        w,h=Image.open(p/'preview.png').size;ratio=min(460/w,350/h);story.extend([Spacer(1,16),PDFImage(str(p/'preview.png'),width=w*ratio,height=h*ratio)])
    story.append(Paragraph('Provenance and limits',styles['Heading2']))
    for line in [json.dumps(record['result'].get('inputDates',{})),json.dumps(record['result'].get('satelliteProvenance',{})),*record['result'].get('limitations',[])]:story.append(Paragraph(escape(line),styles['BodyText']))
    if (p/'reviews.json').exists():
        story.append(Paragraph('Recorded human review',styles['Heading2']))
        for item in load(p/'reviews.json'):story.append(Paragraph(escape(json.dumps(item)),styles['BodyText']))
    SimpleDocTemplate(str(p/'report.pdf')).build(story)
    return dict(file='report.pdf')

STATIC=Path(__file__).resolve().parents[1]/'docs'
app.mount('/',StaticFiles(directory=STATIC,html=True),name='dashboard')
