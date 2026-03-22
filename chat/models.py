from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator, RegexValidator
from .presets import (
    CHAT_BUBBLE_STYLES,
    CHAT_COLOR_THEMES,
    DEFAULT_CHAT_STYLE,
    DEFAULT_CHAT_THEME,
)


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
    district = models.CharField(max_length=100, blank=True, default='', verbose_name='区/县')
    township = models.CharField(max_length=100, blank=True, default='', verbose_name='镇/街道')
    latitude = models.FloatField(verbose_name='纬度')
    longitude = models.FloatField(verbose_name='经度')
    timezone = models.CharField(max_length=50, verbose_name='时区')
    last_updated = models.DateTimeField(auto_now=True, verbose_name='最后更新时间')
    
    class Meta:
        verbose_name = '用户地理位置'
        verbose_name_plural = '用户地理位置'
    
    def __str__(self):
        return f"{self.user.username} - {self.city}, {self.country}"

    @property
    def display_label(self):
        parts = []
        for part in [self.region, self.city, self.district, self.township]:
            normalized = (part or '').strip()
            if normalized and normalized not in parts:
                parts.append(normalized)

        if parts:
            return ' · '.join(parts)
        return (self.country or '').strip()


class UserChatProfile(models.Model):
    """用户聊天外观配置"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='chat_profile')
    friend_id = models.CharField(
        max_length=11,
        unique=True,
        blank=True,
        null=True,
        default=None,
        verbose_name='好友ID',
        validators=[
            MinLengthValidator(8),
            RegexValidator(
                regex=r'^[a-z0-9_]+$',
                message='好友 ID 只能包含小写字母、数字或下划线',
            ),
        ],
    )
    avatar_label = models.CharField(max_length=24, blank=True, default='', verbose_name='头像文本')
    bio = models.CharField(max_length=160, blank=True, default='', verbose_name='个人介绍')
    color_theme = models.CharField(
        max_length=20,
        default=DEFAULT_CHAT_THEME,
        verbose_name='聊天配色',
    )
    bubble_style = models.CharField(
        max_length=20,
        default=DEFAULT_CHAT_STYLE,
        verbose_name='气泡样式',
    )
    show_location = models.BooleanField(default=True, verbose_name='显示大致位置')

    class Meta:
        verbose_name = '聊天外观'
        verbose_name_plural = '聊天外观'

    def __str__(self):
        return f"{self.user.username} - {self.color_theme}/{self.bubble_style}"

    def save(self, *args, **kwargs):
        if not self.friend_id:
            self.friend_id = self.generate_unique_friend_id(self.user.username, exclude_user_id=self.user_id)
        super().save(*args, **kwargs)

    @staticmethod
    def build_default_friend_id(username):
        base = (username or 'user').strip().lower()
        normalized = ''.join(ch for ch in base if ch.isalnum() or ch == '_')
        normalized = normalized[:11]
        if len(normalized) < 8:
            normalized = f'{normalized}{"12345678"[:8-len(normalized)]}'
        return normalized

    @classmethod
    def generate_unique_friend_id(cls, username, exclude_user_id=None):
        base = cls.build_default_friend_id(username)
        candidate = base or 'user'
        suffix = 1
        queryset = cls.objects.all()
        if exclude_user_id:
            queryset = queryset.exclude(user_id=exclude_user_id)
        while queryset.filter(friend_id=candidate).exists():
            suffix += 1
            suffix_text = str(suffix)
            candidate = f'{base[:11-len(suffix_text)]}{suffix_text}'
        return candidate

    def get_theme_config(self):
        return CHAT_COLOR_THEMES.get(self.color_theme, CHAT_COLOR_THEMES[DEFAULT_CHAT_THEME])

    def get_style_config(self):
        return CHAT_BUBBLE_STYLES.get(self.bubble_style, CHAT_BUBBLE_STYLES[DEFAULT_CHAT_STYLE])

    def get_avatar_label(self):
        label = (self.avatar_label or '').strip()
        if label:
            return label[:6]

        username = (self.user.username or '').strip()
        if not username:
            return '用户'
        if any('\u4e00' <= char <= '\u9fff' for char in username):
            return username[:2]
        return username[:2].upper()

    def to_payload(self):
        theme = self.get_theme_config()
        style = self.get_style_config()
        return {
            'friend_id': self.friend_id,
            'avatar_label': self.get_avatar_label(),
            'theme': self.color_theme,
            'style': self.bubble_style,
            'bubble_bg': theme['bubble_bg'],
            'bubble_text': theme['bubble_text'],
            'bubble_accent': theme['bubble_accent'],
            'nameplate_bg': theme['nameplate_bg'],
            'radius': style['radius'],
            'shadow': style['shadow'],
            'border': style['border'],
            'backdrop_filter': style['backdrop_filter'],
        }


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
        try:
            active_members = self.memberships.filter(is_active=True).count()
            if active_members:
                return active_members
        except (OperationalError, ProgrammingError):
            pass

        participants = set(self.messages.exclude(username='').values_list('username', flat=True))
        if self.created_by:
            participants.add(self.created_by.username)
        return max(len(participants), 1)


class RoomMembership(models.Model):
    """群聊成员状态"""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='room_memberships')
    is_active = models.BooleanField(default=True, verbose_name='是否仍在群中')
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name='加入时间')
    removed_at = models.DateTimeField(null=True, blank=True, verbose_name='移出时间')

    class Meta:
        verbose_name = '群成员'
        verbose_name_plural = '群成员'
        constraints = [
            models.UniqueConstraint(fields=['room', 'user'], name='unique_room_membership'),
        ]

    def __str__(self):
        status = 'active' if self.is_active else 'removed'
        return f"{self.room.name} - {self.user.username} ({status})"


class Message(models.Model):
    """消息历史记录"""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages', verbose_name='房间')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='用户')
    username = models.CharField(max_length=100, verbose_name='用户名', default='匿名用户')
    message = models.TextField(verbose_name='消息内容')
    message_type = models.CharField(max_length=20, default='chat', verbose_name='消息类型')
    location_label = models.CharField(max_length=120, blank=True, default='', verbose_name='位置摘要')
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


class Friendship(models.Model):
    """好友关系"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='friendships')
    friend = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reverse_friendships')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '好友关系'
        verbose_name_plural = '好友关系'
        constraints = [
            models.UniqueConstraint(fields=['user', 'friend'], name='unique_friendship_pair'),
        ]


class FriendRequest(models.Model):
    """好友申请"""
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待处理'),
        (STATUS_ACCEPTED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
    ]

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_friend_requests')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_friend_requests')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '好友申请'
        verbose_name_plural = '好友申请'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['sender', 'recipient'], name='unique_friend_request_pair'),
        ]


class DirectConversation(models.Model):
    """私聊会话"""
    user1 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='direct_conversations_started')
    user2 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='direct_conversations_joined')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '私聊会话'
        verbose_name_plural = '私聊会话'
        constraints = [
            models.UniqueConstraint(fields=['user1', 'user2'], name='unique_direct_conversation_pair'),
        ]

    def save(self, *args, **kwargs):
        if self.user1_id and self.user2_id and self.user1_id > self.user2_id:
            self.user1_id, self.user2_id = self.user2_id, self.user1_id
        super().save(*args, **kwargs)

    def other_user(self, user):
        return self.user2 if user == self.user1 else self.user1


class DirectConversationState(models.Model):
    """私聊会话的用户状态"""
    conversation = models.ForeignKey(DirectConversation, on_delete=models.CASCADE, related_name='states')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='direct_conversation_states')
    cleared_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '私聊状态'
        verbose_name_plural = '私聊状态'
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'user'], name='unique_direct_conversation_state'),
        ]


class DirectMessage(models.Model):
    """私聊消息"""
    conversation = models.ForeignKey(DirectConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='direct_messages_sent')
    content = models.TextField(verbose_name='消息内容')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '私聊消息'
        verbose_name_plural = '私聊消息'
        ordering = ['created_at']


class RoomVisitState(models.Model):
    """群聊已读状态"""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='visit_states')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='room_visit_states')
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '群聊访问状态'
        verbose_name_plural = '群聊访问状态'
        constraints = [
            models.UniqueConstraint(fields=['room', 'user'], name='unique_room_visit_state'),
        ]


class SiteConfiguration(models.Model):
    """站点运行配置"""
    trusted_origins = models.TextField(blank=True, default='', verbose_name='CSRF 受信任来源')
    cors_allowed_origins = models.TextField(blank=True, default='', verbose_name='CORS 允许来源')
    allow_all_cors = models.BooleanField(default=False, verbose_name='允许全部跨域来源')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        verbose_name = '站点配置'
        verbose_name_plural = '站点配置'

    @classmethod
    def get_solo(cls):
        try:
            return cls.objects.order_by('id').first() or cls.objects.create()
        except (OperationalError, ProgrammingError):
            return None

    @staticmethod
    def parse_origin_lines(raw_value):
        items = []
        for line in (raw_value or '').splitlines():
            normalized = line.strip()
            if normalized and normalized not in items:
                items.append(normalized)
        return items
