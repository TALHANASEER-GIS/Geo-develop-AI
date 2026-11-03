"""Streamed analysis pipelines. Predictions and human-reviewed evidence stay separate."""
from pathlib import Path
from contextlib import ExitStack
import json,math
import numpy as np
import cv2
import rasterio
from rasterio.windows import Window
from rasterio.features import shapes
from rasterio.enums import Resampling
from shapely.geometry import shape,mapping
from shapely import STRtree
from scipy import ndimage
from PIL import Image
from .raster import Pair,raster_profile,pixel_area_km2
from .models import segment

def components(mask,minimum):
    labels,n=ndimage.label(mask);sizes=np.bincount(labels.ravel());keep=sizes>=minimum;keep[0]=False
    return keep[labels]

def temperature(dn,qa):
    # Landsat 8/9 C2 L2 ST_B10: fill, dilated cloud, cirrus, cloud, shadow, snow.
    valid=(dn>0)&((qa.astype('uint32')&63)==0)
    return dn.astype('float32')*.00341802+149.-273.15,valid

def flood_change(before,after,valid,threshold=-16,drop=3):
    # Input must be calibrated/terrain-corrected VV dB, comparable same relative orbit.
    b=ndimage.median_filter(before,size=3);a=ndimage.median_filter(after,size=3)
    interior=ndimage.binary_erosion(valid,iterations=2)
    permanent=b<threshold
    water=(a<threshold)&((b-a)>drop)&~permanent&interior
    return components(water,8),interior,permanent

def aligned_qa(path,pair,win):
    from rasterio.vrt import WarpedVRT
    with rasterio.open(path) as src:
        if not src.crs or not pair.after.crs:raise ValueError('QA rasters must have a CRS.')
        with WarpedVRT(src,crs=pair.after.crs,transform=pair.after.transform,width=pair.width,height=pair.height,resampling=Resampling.nearest) as v:
            a=v.read(1,window=win,masked=True)
            return a.filled(1).astype('uint32'),~np.ma.getmaskarray(a)

def extract_instances(masks,scores,valid,x,y):
    from affine import Affine
    found=[]
    for mask,score in zip(masks,scores):
        mask=mask&valid
        if mask.sum()<20:continue
        geoms=[shape(g) for g,value in shapes(mask.astype('uint8'),mask=mask,transform=Affine.translation(x,y)) if value==1]
        for g in geoms:
            if g.area>=20:found.append((g,float(score)))
    return found

def deduplicate(features):
    # Suppress overlapping tile instances; do not invent a count from connected union masks.
    kept=[]
    for geom,score in sorted(features,key=lambda f:f[1],reverse=True):
        overlap=False
        for other,_ in kept:
            if not geom.intersects(other):continue
            inter=geom.intersection(other).area
            if inter/max(min(geom.area,other.area),1)>.5:overlap=True;break
        if not overlap:kept.append((geom,score))
    return kept

def run(kind,before,after,out,options,progress):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if kind not in {'align','buildings','roads','flood','temperature','exposure'}:raise ValueError('Unsupported analysis.')
    if kind=='temperature' and not (options.get('qa_before') and options.get('qa_after')):
        raise ValueError('Landsat temperature requires ST_B10 and QA_PIXEL rasters for both dates.')
    if kind=='flood':
        if not options.get('sar_confirmed'):raise ValueError('Confirm calibrated terrain-corrected Sentinel-1 VV dB input, not an RGB image.')
        if not options.get('relative_orbit_before') or options.get('relative_orbit_before')!=options.get('relative_orbit_after'):
            raise ValueError('SAR before/after relative orbit numbers must be supplied and equal.')
    with Pair(before,after,options.get('bbox')) as pair, ExitStack() as stack:
        # Initialize tiled outputs with nodata even when processing only a geographic crop.
        profile=raster_profile(pair,dtype='float32' if kind=='temperature' else 'uint8',nodata=-9999 if kind=='temperature' else 255)
        dst=stack.enter_context(rasterio.open(out/'change.tif','w',**profile))
        if kind=='temperature':
            tbdst=stack.enter_context(rasterio.open(out/'before_celsius.tif','w',**profile))
            tadst=stack.enter_context(rasterio.open(out/'after_celsius.tif','w',**profile))
        if kind in {'buildings','roads'}:
            bdst=stack.enter_context(rasterio.open(out/'before_mask.tif','w',**raster_profile(pair)))
            adst=stack.enter_context(rasterio.open(out/'after_mask.tif','w',**raster_profile(pair)))
        alignment=None
        if kind in {'align','buildings','roads'}:
            progress('Checking automatic image alignment')
            alignment=pair.alignment()
        if kind in {'buildings','roads'}:
            before_preview,bv=pair.sample(pair.before);after_preview,av=pair.sample(pair.after)
            before_preview=cv2.warpAffine(before_preview,np.asarray(alignment['sampleMatrix']),(after_preview.shape[1],after_preview.shape[0]))
            before_preview[~av]=100;after_preview[~av]=100
            Image.fromarray(before_preview).save(out/'before_rgb.png');Image.fromarray(after_preview).save(out/'after_rgb.png')
        if kind=='align':
            dst.close()
            # Output the registered RGB raster, window by window.
            with rasterio.open(out/'aligned_before.tif','w',**raster_profile(pair,count=3,nodata=0)) as aligned:
                for win in pair.windows():
                    a,v=pair.aligned_before(win,alignment);a[~v]=0;aligned.write(np.moveaxis(a,-1,0),window=win)
            return dict(kind=kind,alignment=alignment,status='completed',limitations=['Global affine registration; review local distortion and parallax.'])
        totals=dict(validPixels=0,additionPixels=0,removalPixels=0,floodPixels=0,validAreaKm2=0.,floodAreaKm2=0.,deltaSum=0.,beforeSum=0.,afterSum=0.)
        features_before=[];features_after=[];tile=512
        windows=list(pair.windows(tile));n=len(windows)
        for index,win in enumerate(windows):
            progress(f'Processing tile {index+1}/{n}')
            # A halo prevents median/filter/change tolerance artifacts at core tile edges.
            x=max(0,int(win.col_off)-64);y=max(0,int(win.row_off)-64)
            halo=Window(x,y,min(pair.width-x,int(win.col_off+win.width)+64-x),min(pair.height-y,int(win.row_off+win.height)+64-y))
            core=(slice(int(win.row_off-y),int(win.row_off-y+win.height)),slice(int(win.col_off-x),int(win.col_off-x+win.width)))
            if kind in {'buildings','roads'}:
                b,vb=pair.aligned_before(halo,alignment);a,va=pair.read(pair.after,halo,rgb=True)
                valid=ndimage.binary_erosion(va&vb,iterations=4)
                a[~valid]=100;b[~valid]=100
                prompt='building' if kind=='buildings' else 'road'
                mb,sb=segment(b,prompt);ma,sa=segment(a,prompt)
                bm=mb.any(axis=0) if len(mb) else np.zeros(valid.shape,bool)
                am=ma.any(axis=0) if len(ma) else np.zeros(valid.shape,bool)
                bm &=valid;am &=valid
                adds=components(am&~ndimage.binary_dilation(bm,iterations=3),25)&valid
                removes=components(bm&~ndimage.binary_dilation(am,iterations=3),25)&valid
                change=np.zeros(valid.shape,np.uint8);change[adds]=1;change[removes]=2;change[~valid]=255
                if kind=='buildings':
                    features_before.extend(extract_instances(mb,sb,valid,x,y));features_after.extend(extract_instances(ma,sa,valid,x,y))
                bdst.write(np.where(valid,bm.astype('uint8'),255)[core],1,window=win)
                adst.write(np.where(valid,am.astype('uint8'),255)[core],1,window=win)
                totals['additionPixels']+=int(adds[core].sum());totals['removalPixels']+=int(removes[core].sum())
            else:
                b,vb=pair.read(pair.before,halo,bands=[1]);a,va=pair.read(pair.after,halo,bands=[1]);b=b[0];a=a[0];valid=va&vb
                if kind=='temperature':
                    qb,qbv=aligned_qa(options['qa_before'],pair,halo);qa,qav=aligned_qa(options['qa_after'],pair,halo)
                    b,bclear=temperature(b,qb);a,aclear=temperature(a,qa);valid &=bclear&aclear&qbv&qav
                    change=np.where(valid,a-b,-9999).astype('float32')
                    tbdst.write(np.where(valid,b,-9999).astype('float32')[core],1,window=win)
                    tadst.write(np.where(valid,a,-9999).astype('float32')[core],1,window=win)
                    totals['beforeSum']+=float(b[core][valid[core]].sum());totals['afterSum']+=float(a[core][valid[core]].sum())
                    totals['deltaSum']+=float(change[core][valid[core]].sum())
                elif kind=='flood':
                    if valid.any() and (np.percentile(b[valid],1)<-70 or np.percentile(b[valid],99)>30 or np.percentile(a[valid],1)<-70 or np.percentile(a[valid],99)>30):raise ValueError('SAR values are outside expected dB range. Confirm input units.')
                    wet,valid,permanent=flood_change(b,a,valid,float(options.get('water_db',-16)),float(options.get('drop_db',3)))
                    change=np.where(valid,wet.astype('uint8'),255)
                    totals['floodPixels']+=int(wet[core].sum())
                else:
                    raise ValueError('Exposure requires a flood result and building features; use the exposure endpoint.')
            cv=valid[core];totals['validPixels']+=int(cv.sum())
            weights=pixel_area_km2(pair.after,win)
            if weights is not None:
                totals['validAreaKm2']+=float((cv*weights).sum())
                if kind=='flood':totals['floodAreaKm2']+=float(((change[core]==1)*weights).sum())
            dst.write(change[core],1,window=win)
        if not totals['validPixels']:raise ValueError('No common valid pixels. No result was calculated.')
        result=dict(kind=kind,status='completed',stats=totals,alignment=alignment,reviewStatus='unverified',
            inputDates={'before':options.get('before_date'),'after':options.get('after_date')},
            limitations=['Model outputs are candidates. No structural damage or verified counts are inferred.','Windowed processing uses a 64-pixel halo; results near tile boundaries still require review.'])
        result['pixelContext']=dict(offsetX=pair.window.col_off,offsetY=pair.window.row_off,width=pair.window.width,height=pair.window.height)
        if kind=='temperature':
            result['stats'].update(meanBeforeC=totals['beforeSum']/totals['validPixels'],meanAfterC=totals['afterSum']/totals['validPixels'],meanDeltaC=totals['deltaSum']/totals['validPixels'])
            result['limitations']+=['Landsat 8/9 Collection 2 Level-2 ST_B10 only; QA_PIXEL bits 0-5 excluded. ST uncertainty and QA_RADSAT should also be reviewed.','Land surface temperature is not air temperature or evidence of global warming from two dates. Native thermal detail is coarser than the delivered 30 m grid.']
        if kind=='flood':result['limitations']+=['Threshold-based VV SAR change screening, not a validated universal flood model.','Permanent-water candidates are excluded from the before observation; no terrain, vegetation or urban correction is automatic for uploaded SAR.']
        if kind=='roads':result['limitations']+=['SAM 3 text-prompt road segmentation is experimental; apparent changes can be shadows, bare soil or model errors. No road length or verified road additions are claimed.']
        if kind=='buildings':
            features_before=deduplicate(features_before);features_after=deduplicate(features_after)
            features=[]
            for prefix,items in [('before',features_before),('after',features_after)]:
                for i,(geom,score) in enumerate(items):
                    features.append(dict(type='Feature',id=f'{prefix}-{i+1}',geometry=mapping(geom),properties=dict(epoch=prefix,score=score,status='unreviewed',coordinateSpace='raster pixel coordinates')))
            (out/'instances.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=features)))
            result['stats'].update(candidateBeforeInstances=len(features_before),candidateAfterInstances=len(features_after),verifiedBeforeBuildings=0,verifiedAfterBuildings=0)
            result['limitations']+=['Instance counts use overlap suppression across tiles; they remain unverified and may split or merge roofs.','Instance GeoJSON coordinates are raster pixel coordinates, not longitude/latitude.']
        (out/'grid.json').write_text(json.dumps(dict(width=pair.width,height=pair.height,crs=str(pair.after.crs),transform=list(pair.after.transform)[:6])))
        return result

def preview(path,output):
    with rasterio.open(path) as ds:
        s=min(1,1200/max(ds.width,ds.height));a=ds.read(1,out_shape=(max(1,int(ds.height*s)),max(1,int(ds.width*s))),resampling=Resampling.nearest)
        color=np.full((*a.shape,3),[224,229,225],np.uint8)
        if ds.dtypes[0]=='float32':
            valid=(a!=-9999)&np.isfinite(a);v=np.clip(a/10,-1,1)
            color[:,:,0]=np.where(valid,220+35*np.maximum(v,0)-120*np.maximum(-v,0),180)
            color[:,:,1]=np.where(valid,220-150*np.abs(v),185)
            color[:,:,2]=np.where(valid,220+35*np.maximum(-v,0)-120*np.maximum(v,0),190)
        else:
            color[a==1]=[0,158,128];color[a==2]=[224,70,85];color[a==255]=[120,128,132]
        Image.fromarray(color).save(output)
