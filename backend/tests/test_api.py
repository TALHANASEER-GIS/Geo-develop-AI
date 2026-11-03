import os,tempfile,importlib
os.environ['GEODEVELOP_DATA']=tempfile.mkdtemp(prefix='geodevelop-test-')
os.environ['GEODEVELOP_API_KEY']='test-key-not-a-real-deployment-credential'
from fastapi.testclient import TestClient
from backend.app import app,TOKEN,ROOT,write

def headers():return {'Authorization':'Bearer '+TOKEN}
def test_authentication_and_private_upload_gate():
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        assert client.get('/api/assets').status_code==401
        assert client.get('/api/assets',headers=headers()).status_code==200
        assert client.post('/api/assets',content=b'no',headers={**headers(),'X-Filename':'bad.zip'}).status_code==400
        assert client.post('/api/assets',content=b'not-a-tiff',headers={**headers(),'X-Filename':'fake.tif'}).status_code==400

def test_review_requires_real_feature_and_evidence():
    id='a'*32;p=ROOT/'jobs'/id;p.mkdir(parents=True,exist_ok=True)
    write(p/'job.json',dict(id=id,status='completed',kind='buildings'))
    write(p/'instances.geojson',dict(type='FeatureCollection',features=[dict(id='after-1',type='Feature',geometry=None,properties={})]))
    with TestClient(app) as client:
        value=dict(feature_id='after-1',decision='accepted',label='building',reviewer='Tester',evidence='Dated aerial roof inspection, 2024-01-01')
        assert client.post('/api/jobs/'+id+'/reviews',json={**value,'evidence':''},headers=headers()).status_code==422
        assert client.post('/api/jobs/'+id+'/reviews',json={**value,'feature_id':'invented'},headers=headers()).status_code==400
        result=client.post('/api/jobs/'+id+'/reviews',json=value,headers=headers())
        assert result.status_code==200 and result.json()['humanConfirmedCounts']['building']==1
        assert result.json()['humanConfirmedCounts']['damaged']==0

def test_upload_temperature_job_and_download(tmp_path):
    import io,time
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    def tif(value):
        with MemoryFile() as memory:
            with memory.open(driver='GTiff',width=64,height=64,count=1,dtype='uint16',crs='EPSG:32642',transform=from_origin(400000,3200000,30,30)) as ds:ds.write(np.full((64,64),value,np.uint16),1)
            return memory.read()
    with TestClient(app) as client:
        ids=[]
        for name,value in [('before.tif',45000),('after.tif',46000),('qa.tif',0)]:
            response=client.post('/api/assets',content=tif(value),headers={**headers(),'X-Filename':name})
            assert response.status_code==200;ids.append(response.json()['id'])
        response=client.post('/api/jobs',json=dict(kind='temperature',before=ids[0],after=ids[1],options=dict(qa_before=ids[2],qa_after=ids[2])),headers=headers())
        assert response.status_code==200;id=response.json()['id']
        for _ in range(100):
            job=client.get('/api/jobs/'+id,headers=headers()).json()
            if job['status'] in {'completed','failed'}:break
            time.sleep(.05)
        assert job['status']=='completed',job
        assert abs(job['result']['stats']['meanDeltaC']-3.41802)<.001
        assert client.get('/api/jobs/'+id+'/files/change.tif',headers=headers()).status_code==200
        assert client.get('/api/jobs/'+id+'/files/change.tif').status_code==401
