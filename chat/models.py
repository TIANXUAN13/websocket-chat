from django.db import models
from django.contrib.auth.models import User


class UserSession(models.Model):
    """用户会话追踪"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = '用户会话'
        verbose_name_plural = '用户会话'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.session_key}"


class UserLocation(models.Model):
    """用户地理位置信息"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='location')
    ip_address = models.GenericIPAddressField(verbose_name='IP地址')
    country = models.CharField(max_length=100, verbose_name='国家')
    region = models.CharField(max_length=100, verbose_name='地区/省份')
    city = models.CharField(max_length=100, verbose_name='城市')
    latitude = models.FloatField(verbose_name='纬度')
    longitude = models.FloatField(verbose_name='经度')
    timezone = models.CharField(max_length=50, verbose_name='时区')
    last_updated = models.DateTimeField(auto_now=True, verbose_name='最后更新时间')
    
    class Meta:
        verbose_name = '用户地理位置'
        verbose_name_plural = '用户地理位置'
    
    def __str__(self):
        return f"{self.user.username} - {self.city}, {self.country}"


class Room(models.Model):
    """聊天室"""
    name = models.CharField(max_length=100, unique=True, verbose_name='房间名称')
    avatar = models.CharField(max_length=8, default='💬', verbose_name='房间头像')
    description = models.CharField(max_length=120, blank=True, default='一起聊聊吧', verbose_name='房间简介')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='创建者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    
    class Meta:
        verbose_name = '房间'
        verbose_name_plural = '房间'
        ordering = ['-created_at']
    
    def __str__(self):
        return str(self.name)

    @property
    def total_members(self):
        participants = set(
            self.messages.exclude(username='').values_list('username', flat=True)
        )
        if self.created_by:
            participants.add(self.created_by.username)
        return max(len(participants), 1)


class Message(models.Model):
    """消息历史记录"""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages', verbose_name='房间')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='用户')
    username = models.CharField(max_length=100, verbose_name='用户名', default='匿名用户')
    message = models.TextField(verbose_name='消息内容')
    message_type = models.CharField(max_length=20, default='chat', verbose_name='消息类型')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='发送时间')
    
    class Meta:
        verbose_name = '消息'
        verbose_name_plural = '消息'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['room', '-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.username}: {self.message[:50]}"
