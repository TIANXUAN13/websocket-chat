# chat/services/geoip_service.py
import requests
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class GeoIPService:
    """IP地理位置服务"""
    
    @staticmethod
    def get_location(ip_address):
        """
        获取IP地址的地理位置信息
        使用ip-api.com免费API
        """
        # 跳过本地IP地址
        if ip_address in ['127.0.0.1', 'localhost', '::1'] or ip_address.startswith('192.168.'):
            return None
            
        # 先检查缓存
        cache_key = f'geoip_{ip_address}'
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"从缓存获取地理位置: {ip_address}")
            return cached_data
        
        # 调用API获取地理位置
        try:
            response = requests.get(f'http://ip-api.com/json/{ip_address}', timeout=5)
            data = response.json()
            
            if data['status'] == 'success':
                location_data = {
                    'country': data.get('country', ''),
                    'region': data.get('regionName', ''),
                    'city': data.get('city', ''),
                    'latitude': data.get('lat', 0.0),
                    'longitude': data.get('lon', 0.0),
                    'timezone': data.get('timezone', '')
                }
                
                # 缓存结果24小时
                cache.set(cache_key, location_data, 86400)
                logger.info(f"成功获取地理位置: {ip_address} -> {location_data['city']}, {location_data['country']}")
                return location_data
            else:
                logger.warning(f"获取地理位置失败: {ip_address} - {data.get('message', '未知错误')}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"请求地理位置API失败: {ip_address} - {str(e)}")
            return None
        except Exception as e:
            logger.error(f"处理地理位置数据失败: {ip_address} - {str(e)}")
            return None
    
    @staticmethod
    def get_client_ip(request):
        """
        获取客户端真实IP地址
        考虑代理服务器和负载均衡器
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            # X-Forwarded-For可能包含多个IP，取第一个
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        
        return ip
    
    @staticmethod
    def save_user_location(user, ip_address):
        """
        保存用户地理位置信息
        """
        if not ip_address or ip_address in ['127.0.0.1', 'localhost', '::1']:
            return False
            
        location_data = GeoIPService.get_location(ip_address)
        if not location_data:
            return False
            
        try:
            from ..models import UserLocation
            UserLocation.objects.update_or_create(
                user=user,
                defaults={
                    'ip_address': ip_address,
                    'country': location_data['country'],
                    'region': location_data['region'],
                    'city': location_data['city'],
                    'latitude': location_data['latitude'],
                    'longitude': location_data['longitude'],
                    'timezone': location_data['timezone']
                }
            )
            logger.info(f"保存用户地理位置: {user.username} -> {location_data['city']}, {location_data['country']}")
            return True
        except Exception as e:
            logger.error(f"保存用户地理位置失败: {user.username} - {str(e)}")
            return False