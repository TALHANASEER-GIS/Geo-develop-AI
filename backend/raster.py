"""Windowed raster IO and conservative global alignment; never load a full orthomosaic."""
from contextlib import ExitStack
import math
import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds

MAX_WORKING_PIXELS = 4096 * 4096

def metadata(path):
    with rasterio.open(path, sharing=False) as src:
        if src.driver not in {'GTiff','PNG','JPEG','WEBP'}:
            raise ValueError('Use a GeoTIFF, PNG, JPEG or WebP raster.')
        if src.width*src.height > 20_000_000_000 or src.count>32:
            raise ValueError('Raster exceeds service dimensions/band limit.')
        return dict(width=src.width,height=src.height,bands=src.count,dtypes=src.dtypes,
                    crs=str(src.crs) if src.crs else None,bounds=list(src.bounds),
                    transform=list(src.transform)[:6],descriptions=src.descriptions,
                    nodata=src.nodata,driver=src.driver)

class Pair:
    def __init__(self,before,after,bbox=None):
        self.stack=ExitStack()
        self.after=self.stack.enter_context(rasterio.open(after,sharing=False))
        original=self.stack.enter_context(rasterio.open(before,sharing=False))
        if bool(original.crs)!=bool(self.after.crs):
            self.stack.close();raise ValueError('Both inputs must be georeferenced, or both must be ordinary RGB images.')
        if original.crs:
            self.before=self.stack.enter_context(WarpedVRT(original,crs=self.after.crs,
                transform=self.after.transform,width=self.after.width,height=self.after.height,
                resampling=Resampling.bilinear,add_alpha=True))
        else:
            if (original.width,original.height)!=(self.after.width,self.after.height):
                self.stack.close();raise ValueError('Unreferenced images must have matching dimensions.')
            self.before=original
        self.width=self.after.width;self.height=self.after.height
        self.window=Window(0,0,self.width,self.height)
        if bbox:
            if not self.after.crs:raise ValueError('A geographic crop needs a georeferenced image.')
            from rasterio.warp import transform_bounds
            b=transform_bounds('EPSG:4326',self.after.crs,*bbox)
            self.window=from_bounds(*b,transform=self.after.transform).round_offsets().round_lengths().intersection(self.window)
        self.scales={}
    def close(self):self.stack.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def windows(self,tile=512):
        w=self.window
        for y in range(int(w.row_off),int(w.row_off+w.height),tile):
            for x in range(int(w.col_off),int(w.col_off+w.width),tile):
                yield Window(x,y,min(tile,w.col_off+w.width-x),min(tile,w.row_off+w.height-y))
    def read(self,src,win,bands=None,shape=None,rgb=False):
        bands=bands or list(range(1,min(3,src.count)+1))
        if win.width*win.height>MAX_WORKING_PIXELS and shape is None:
            raise ValueError('Working window too large; choose a smaller tile or crop.')
        options=dict(indexes=bands,window=win,masked=True)
        if shape:options.update(out_shape=(len(bands),*shape),resampling=Resampling.bilinear if rgb else Resampling.nearest)
        a=src.read(**options)
        valid=~np.ma.getmaskarray(a).any(axis=0)
        # Valid dataset/alpha masks, plus opaque black gaps commonly used by orthomosaics.
        valid &= src.dataset_mask(window=win,out_shape=a.shape[1:])>0
        data=np.asarray(a.filled(0))
        if rgb:
            if data.shape[0]<3:data=np.repeat(data[:1],3,axis=0)
            if data.dtype!=np.uint8:
                key=src.name
                if key not in self.scales:
                    sample=src.read(bands,out_shape=(len(bands),min(src.height,256),min(src.width,256)),masked=True)
                    self.scales[key]=[(float(np.percentile(v.compressed(),2)),float(np.percentile(v.compressed(),98))) if v.count() else (0,1) for v in sample]
                for i,(lo,hi) in enumerate(self.scales[key]):data[i]=np.clip((data[i].astype('float32')-lo)*255/max(hi-lo,1e-6),0,255)
            data=np.moveaxis(data.astype('uint8'),0,-1)
            valid &= data.max(axis=2)>5
        return data,valid
    def sample(self,src,max_side=1600):
        w=self.window;s=min(1,max_side/max(w.width,w.height))
        shape=(max(1,round(w.height*s)),max(1,round(w.width*s)))
        return self.read(src,w,shape=shape,rgb=True)
    def alignment(self):
        a,va=self.sample(self.before);b,vb=self.sample(self.after)
        v=(va&vb).astype('uint8')*255
        detector=cv2.SIFT_create(nfeatures=14000)
        k0,d0=detector.detectAndCompute(cv2.cvtColor(a,cv2.COLOR_RGB2GRAY),v)
        k1,d1=detector.detectAndCompute(cv2.cvtColor(b,cv2.COLOR_RGB2GRAY),v)
        if d0 is None or d1 is None:raise ValueError('Alignment failed: insufficient textured overlap.')
        pairs=cv2.BFMatcher().knnMatch(d0,d1,k=2)
        matches=[p for pair in pairs if len(pair)==2 for p,q in [pair] if p.distance<.8*q.distance]
        if len(matches)<15:raise ValueError('Alignment failed: fewer than 15 reliable correspondences.')
        source=np.float32([k0[m.queryIdx].pt for m in matches]);target=np.float32([k1[m.trainIdx].pt for m in matches])
        M,inliers=cv2.estimateAffinePartial2D(source,target,method=cv2.RANSAC,ransacReprojThreshold=3,maxIters=5000,confidence=.999)
        if M is None or inliers.sum()<15:raise ValueError('Alignment failed: fewer than 15 inliers. No changes calculated.')
        residual=np.linalg.norm(cv2.transform(source[None],M)[0]-target,axis=1)[inliers.ravel().astype(bool)]
        scale=float(np.hypot(M[0,0],M[1,0]));angle=float(np.degrees(np.arctan2(M[1,0],M[0,0])))
        if not .9<scale<1.1 or abs(angle)>5:raise ValueError('Alignment requires excessive scale/rotation; review georeferencing.')
        if np.ptp(source[inliers.ravel().astype(bool)],axis=0).min()<min(a.shape[:2])*.2:
            raise ValueError('Matched landmarks cover too little of the image. Crop a better overlap.')
        # Convert sampled crop affine into full-resolution pixel coordinates.
        sx=self.window.width/a.shape[1];sy=self.window.height/a.shape[0]
        S=np.diag([sx,sy,1.]);O=np.array([[1,0,self.window.col_off],[0,1,self.window.row_off],[0,0,1.]])
        full=O@S@np.vstack([M,[0,0,1]])@np.linalg.inv(S)@np.linalg.inv(O)
        return dict(matrix=full[:2].tolist(),sampleMatrix=M.tolist(),inliers=int(inliers.sum()),matches=len(matches),
                    medianResidualSamplePixels=float(np.median(residual)),p95ResidualSamplePixels=float(np.percentile(residual,95)),
                    sampleScale=[sx,sy],scaleCorrection=scale,rotationDegrees=angle,
                    warning='Global registration is not independent validation; local roof parallax and distortion may remain.')
    def aligned_before(self,win,alignment):
        M=np.asarray(alignment['matrix']);inverse=cv2.invertAffineTransform(M)
        x,y,w,h=map(int,[win.col_off,win.row_off,win.width,win.height])
        corners=np.float32([[[x,y],[x+w,y],[x,y+h],[x+w,y+h]]])
        c=cv2.transform(corners,inverse)[0];lo=np.floor(c.min(0)).astype(int)-3;hi=np.ceil(c.max(0)).astype(int)+3
        lo=np.maximum(lo,[0,0]);hi=np.minimum(hi,[self.width,self.height])
        if (hi<=lo).any():return np.zeros((h,w,3),np.uint8),np.zeros((h,w),bool)
        sw=Window(int(lo[0]),int(lo[1]),int(hi[0]-lo[0]),int(hi[1]-lo[1]))
        data,valid=self.read(self.before,sw,rgb=True)
        local=M.copy();local[:,2]+=M[:,:2]@lo-np.array([x,y])
        img=cv2.warpAffine(data,local,(w,h),flags=cv2.INTER_LINEAR)
        mask=cv2.warpAffine(valid.astype('uint8'),local,(w,h),flags=cv2.INTER_NEAREST)>0
        return img,mask

def raster_profile(pair,count=1,dtype='uint8',nodata=255):
    return dict(driver='GTiff',height=pair.height,width=pair.width,count=count,dtype=dtype,
                crs=pair.after.crs,transform=pair.after.transform,nodata=nodata,
                tiled=True,blockxsize=512,blockysize=512,compress='deflate',BIGTIFF='IF_SAFER')

def pixel_area_km2(src,win):
    """Area per row, respecting geographic coordinates and projected unit conversion."""
    from pyproj import CRS,Geod
    if not src.crs:return None
    crs=CRS.from_user_input(src.crs);t=src.transform
    if crs.is_projected:
        factor=crs.axis_info[0].unit_conversion_factor
        return np.full((int(win.height),1),abs(t.a*t.e-t.b*t.d)*factor**2/1e6)
    if abs(t.b)>1e-12 or abs(t.d)>1e-12:raise ValueError('Rotate geographic raster to a north-up grid before area measurement.')
    geod=crs.get_geod();areas=[]
    for row in range(int(win.row_off),int(win.row_off+win.height)):
        x0,y0=t*(win.col_off,row);x1,y1=t*(win.col_off+1,row+1)
        a,_=geod.polygon_area_perimeter([x0,x1,x1,x0],[y0,y0,y1,y1]);areas.append(abs(a)/1e6)
    return np.array(areas)[:,None]
