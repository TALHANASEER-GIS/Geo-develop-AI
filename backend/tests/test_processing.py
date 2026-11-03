import json
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window
from shapely.geometry import box
from backend.analysis import temperature,flood_change,deduplicate,run
from backend.raster import Pair

def write(path,a,nodata=None):
    if a.ndim==2:a=a[None]
    with rasterio.open(path,'w',driver='GTiff',width=a.shape[2],height=a.shape[1],count=a.shape[0],dtype=a.dtype,crs='EPSG:32642',transform=from_origin(400000,3200000,10,10),nodata=nodata) as ds:ds.write(a)
    return path

def test_landsat_scaling_and_all_qa_bits():
    dn=np.full((1,8),45000,np.uint16);qa=np.array([[0,1,2,4,8,16,32,128]],np.uint16)
    c,v=temperature(dn,qa)
    assert float(c[0,0])==pytest.approx(29.6609,abs=.0001)
    assert v.tolist()==[[True,False,False,False,False,False,False,True]]

def test_flood_excludes_existing_water_and_missing_data():
    b=np.full((100,100),-10.,np.float32);a=b.copy();v=np.ones(b.shape,bool)
    b[10:25,10:25]=-22;a[10:25,10:25]=-25
    a[40:65,40:65]=-23
    a[75:90,75:90]=-24;v[75:90,75:90]=False
    flood,valid,permanent=flood_change(b,a,v)
    assert flood[50,50] and not flood[15,15] and not flood[80,80]
    assert permanent[15,15] and not valid[80,80]

def test_duplicate_instances_not_double_counted():
    found=deduplicate([(box(0,0,20,20),.8),(box(1,0,21,20),.9),(box(40,0,60,20),.7)])
    assert len(found)==2

def test_alignment_rejects_blank_images(tmp_path):
    a=np.full((3,512,512),100,np.uint8)
    b=write(tmp_path/'a.tif',a);c=write(tmp_path/'b.tif',a)
    with Pair(b,c) as pair,pytest.raises(ValueError,match='Alignment failed'):pair.alignment()

def test_alignment_recovers_known_shift_and_tiled_read(tmp_path):
    import cv2
    rng=np.random.default_rng(12)
    a=rng.integers(20,230,(800,800,3),dtype=np.uint8)
    a=cv2.GaussianBlur(a,(5,5),0)
    b=cv2.warpAffine(a,np.array([[1,0,8],[0,1,-5]],float),(800,800))
    p=write(tmp_path/'a.tif',np.moveaxis(a,-1,0));q=write(tmp_path/'b.tif',np.moveaxis(b,-1,0))
    with Pair(p,q) as pair:
        alignment=pair.alignment();M=np.asarray(alignment['matrix'])
        assert M[:,2]==pytest.approx([8,-5],abs=.5)
        aligned,v=pair.aligned_before(Window(128,128,256,256),alignment)
        assert np.abs(aligned.astype(float)-b[128:384,128:384]).mean()<3

def test_streamed_temperature_qa_and_geographic_overlap(tmp_path):
    b=np.full((600,600),45000,np.uint16);a=b+1000
    qb=np.zeros(b.shape,np.uint16);qa=qb.copy();qa[:100]=8
    paths=[write(tmp_path/n,x) for n,x in [('b.tif',b),('a.tif',a),('qb.tif',qb),('qa.tif',qa)]]
    result=run('temperature',paths[0],paths[1],tmp_path/'out',{'qa_before':str(paths[2]),'qa_after':str(paths[3])},lambda m:None)
    assert result['stats']['validPixels']==300000
    assert result['stats']['meanDeltaC']==pytest.approx(3.41802,abs=.0001)
    with rasterio.open(tmp_path/'out/change.tif') as ds:
        assert ds.read(1)[50,50]==-9999
        assert ds.read(1)[550,550]==pytest.approx(3.41802,abs=.0001)

def test_sar_orbit_mismatch_rejected_before_processing(tmp_path):
    with pytest.raises(ValueError,match='orbit'):
        run('flood','missing','missing',tmp_path/'out',dict(sar_confirmed=True,relative_orbit_before=5,relative_orbit_after=71),lambda m:None)
