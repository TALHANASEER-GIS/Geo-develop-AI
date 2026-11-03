"""Earth Engine satellite acquisition. Authentication belongs to the server/runtime."""
import os,datetime
from pathlib import Path
import httpx
from .boundary import validate_geometry

def bounds(bbox):
    if len(bbox)!=4 or not(-180<=bbox[0]<bbox[2]<=180 and -85<=bbox[1]<bbox[3]<=85):raise ValueError('Invalid WGS84 bounding box.')
    if bbox[2]-bbox[0]>.25 or bbox[3]-bbox[1]>.25:raise ValueError('Start with a satellite pilot no wider than 0.25 degrees per side.')
    return bbox

def scene_search(collection,bbox,start,end):
    if collection not in {'sentinel-2-l2a','landsat-c2-l2','sentinel-1-rtc'}:raise ValueError('Unsupported satellite collection.')
    bounds(bbox)
    with httpx.Client(timeout=40) as client:
        r=client.post('https://planetarycomputer.microsoft.com/api/stac/v1/search',json=dict(collections=[collection],bbox=bbox,datetime=start+'/'+end,limit=20));r.raise_for_status()
    return [dict(id=f['id'],date=f['properties'].get('datetime'),cloud=f['properties'].get('eo:cloud_cover'),bbox=f.get('bbox')) for f in r.json().get('features',[])]

def gee_pair(kind,bbox,start_before,end_before,start_after,end_after,out,geometry=None):
    import ee
    bounds(bbox)
    if not os.getenv('EE_PROJECT'):raise ValueError('Configure EE_PROJECT and authorize Earth Engine on the backend. No satellite processing has run.')
    ee.Initialize(project=os.environ['EE_PROJECT'])
    if geometry:
        geometry, polygon_bounds=validate_geometry(geometry)
        bounds(polygon_bounds)
        if any(abs(a-b)>1e-6 for a,b in zip(bbox,polygon_bounds)):raise ValueError('Polygon and bounding box disagree.')
    aoi=ee.Geometry(geometry, proj='EPSG:4326', geodesic=False) if geometry else ee.Geometry.Rectangle(bbox)
    def period(s,e):
        if datetime.date.fromisoformat(e)<datetime.date.fromisoformat(s):raise ValueError('End date precedes start date.')
        if (datetime.date.fromisoformat(e)-datetime.date.fromisoformat(s)).days>120:raise ValueError('Use windows of at most 120 days.')
    period(start_before,end_before);period(start_after,end_after)
    provenance={}
    if kind=='temperature':
        collection=ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2')).filterBounds(aoi).filter(ee.Filter.eq('PROCESSING_LEVEL','L2SP'))
        def prepare(im):
            clear=im.select('QA_PIXEL').bitwiseAnd(63).eq(0).And(im.select('QA_RADSAT').eq(0))
            return im.select('ST_B10').multiply(.00341802).add(149).subtract(273.15).updateMask(clear).rename('temperature')
        cb=collection.filterDate(start_before,end_before);ca=collection.filterDate(start_after,end_after)
        counts=[cb.size().getInfo(),ca.size().getInfo()]
        if min(counts)==0:raise ValueError('No Landsat L2SP scenes in one period.')
        pair=cb.map(prepare).median().rename('before').addBands(ca.map(prepare).median().rename('after'))
        provenance=dict(sceneCounts=counts,source='Landsat 8/9 C2 L2 ST_B10',unit='Celsius',composite='QA-screened per-period median',scale=30)
    elif kind=='flood':
        collection=ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi).filter(ee.Filter.eq('instrumentMode','IW')).filter(ee.Filter.listContains('transmitterReceiverPolarisation','VV')).filter(ee.Filter.eq('resolution_meters',10))
        cb=collection.filterDate(start_before,end_before);ca=collection.filterDate(start_after,end_after)
        available=sorted(set(cb.aggregate_array('relativeOrbitNumber_start').getInfo())&set(ca.aggregate_array('relativeOrbitNumber_start').getInfo()))
        if not available:raise ValueError('No common Sentinel-1 relative orbit between periods.')
        orbit=available[0];cb=cb.filter(ee.Filter.eq('relativeOrbitNumber_start',orbit));ca=ca.filter(ee.Filter.eq('relativeOrbitNumber_start',orbit))
        b=cb.select('VV').median().focal_median(30,'circle','meters');a=ca.select('VV').median().focal_median(30,'circle','meters')
        slope=ee.Terrain.slope(ee.Image('USGS/SRTMGL1_003')).lt(5)
        permanent=ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('seasonality').gte(10).unmask(0)
        allowed=slope.And(permanent.Not())
        pair=b.updateMask(allowed).rename('before').addBands(a.updateMask(allowed).rename('after'))
        provenance=dict(relativeOrbit=orbit,sceneCounts=[cb.size().getInfo(),ca.size().getInfo()],source='Sentinel-1 GRD VV dB',composite='Same-relative-orbit median; 30 m focal median; slope <5 deg and JRC permanent water exclusion',scale=20)
    else:raise ValueError('Satellite acquisition supports temperature and flood.')
    provenance['studyGeometry']=geometry or dict(type='Polygon',coordinates=[[[bbox[0],bbox[1]],[bbox[2],bbox[1]],[bbox[2],bbox[3]],[bbox[0],bbox[3]],[bbox[0],bbox[1]]]])
    # Both bands retain a common mask; bounded pilot export stays below EE interactive export limits.
    pair=pair.updateMask(pair.mask().reduce(ee.Reducer.min())).clip(aoi).unmask(-9999, sameFootprint=False)
    url=pair.getDownloadURL(dict(region=aoi,scale=provenance['scale'],crs='EPSG:4326',format='GEO_TIFF'))
    p=Path(out)/'satellite_pair.tif'
    with httpx.stream('GET',url,timeout=180,follow_redirects=True) as response:
        response.raise_for_status()
        total=0
        with p.open('wb') as f:
            for chunk in response.iter_bytes():
                total+=len(chunk)
                if total>160_000_000:raise ValueError('Satellite export exceeded 160 MB; use a smaller pilot.')
                f.write(chunk)
    return p,provenance
