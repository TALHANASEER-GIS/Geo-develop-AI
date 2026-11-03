"""Validate bounded WGS84 polygon inputs before raster or Earth Engine use."""
import math
from shapely.geometry import shape, mapping

def validate_geometry(value):
    if not isinstance(value, dict) or value.get('type') not in {'Polygon','MultiPolygon'}:
        raise ValueError('Use a WGS84 GeoJSON Polygon or MultiPolygon.')
    def vertices(item):
        if not isinstance(item, (list,tuple)):
            raise ValueError('Invalid polygon coordinates.')
        if item and isinstance(item[0], (int,float)):
            if len(item)!=2 or not all(math.isfinite(v) for v in item) or not(-180<=item[0]<=180 and -85<=item[1]<=85):
                raise ValueError('Use longitude/latitude coordinates within the map extent.')
            return 1
        return sum(vertices(child) for child in item)
    if vertices(value.get('coordinates',[]))>2000:
        raise ValueError('Simplify the boundary to at most 2,000 vertices.')
    geom=shape(value)
    if geom.is_empty or not geom.is_valid or geom.area<=0:
        raise ValueError('Boundary is empty or invalid; check crossing edges and closed rings.')
    return mapping(geom), list(geom.bounds)
