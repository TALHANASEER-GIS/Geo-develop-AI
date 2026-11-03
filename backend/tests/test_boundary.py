import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.features import geometry_mask
from PIL import Image
from backend.boundary import validate_geometry
from backend.raster import Pair
from backend.analysis import map_preview

TRIANGLE=dict(type='Polygon',coordinates=[[[74,31],[74.01,31],[74,31.01],[74,31]]])

def test_reject_crossed_boundary():
    with pytest.raises(ValueError):
        validate_geometry(dict(type='Polygon',coordinates=[[[0,0],[1,1],[1,0],[0,1],[0,0]]]))
    with pytest.raises(ValueError):validate_geometry(dict(type='Point',coordinates=[74,31]))

def test_exact_polygon_mask_and_transparent_map_overlay(tmp_path):
    path=tmp_path/'input.tif'
    transform=from_origin(74,31.01,.0001,.0001)
    with rasterio.open(path,'w',driver='GTiff',width=100,height=100,count=1,dtype='uint8',nodata=255,crs='EPSG:4326',transform=transform) as ds:
        ds.write(np.ones((100,100),np.uint8),1)
    expected=geometry_mask([TRIANGLE],out_shape=(100,100),transform=transform,invert=True)
    with Pair(path,path,geometry=TRIANGLE) as pair:
        _,valid=pair.read(pair.after,pair.window,bands=[1])
        np.testing.assert_array_equal(valid,expected)
    masked=tmp_path/'masked.tif'
    with rasterio.open(path) as src:
        with rasterio.open(masked,'w',**src.profile) as dst:dst.write(np.where(expected,1,255).astype('uint8'),1)
    context=map_preview(masked,tmp_path/'map.png')
    rgba=np.asarray(Image.open(tmp_path/'map.png'))
    assert context['crs']=='EPSG:4326'
    assert (rgba[:,:,3]==0).any() and (rgba[:,:,3]==255).any()
    assert 4900<(rgba[:,:,3]>0).sum()<5100
