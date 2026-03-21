from .location_service import UserLocationService


class GeoIPService(UserLocationService):
    """兼容旧调用方的地理位置服务入口"""
