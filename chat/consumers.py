# chat/consumers.py
import hashlib
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from .presets import DEFAULT_CHAT_STYLE, DEFAULT_CHAT_THEME
from .services.geoip_service import GeoIPService


class ChatConsumer(AsyncWebsocketConsumer):
    room_users = {}
    HISTORY_LIMIT = 50  # 每个房间显示的历史记录数量

    @staticmethod
    def build_group_name(room_name):
        room_hash = hashlib.sha256(room_name.encode("utf-8")).hexdigest()[:32]
        return f"chat_{room_hash}"

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = self.build_group_name(self.room_name)
        self.user = None
        self.read_only_removed = False
        self.is_group_member = False
        
        if self.scope['user'].is_authenticated:
            self.user = self.scope['user'].username
        else:
            self.user = '匿名用户'

        await self.accept()

        self.read_only_removed = await self.is_removed_from_room()

        if not self.read_only_removed:
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            self.is_group_member = True

            if self.room_name not in self.room_users:
                self.room_users[self.room_name] = {}

            self.room_users[self.room_name][self.channel_name] = {
                'username': self.user,
                'is_owner': await self.check_is_owner(),
                'avatar_label': await self.get_user_avatar_label(),
                'friend_id': await self.get_user_friend_id(),
            }

        await self.send_history()
        if self.read_only_removed:
            await self.send(text_data=json.dumps({
                'type': 'removed',
                'message': '你已被移出该群聊，仅可查看历史消息，不能再发送消息。',
            }))
            return

        await self.send_welcome()
        await self.broadcast_user_list()

    async def disconnect(self, code):
        if hasattr(self, 'room_name') and self.room_name in self.room_users:
            if self.channel_name in self.room_users[self.room_name]:
                del self.room_users[self.room_name][self.channel_name]
                
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_list',
                        'users': self.get_users_dict()
                    }
                )

        if hasattr(self, 'room_group_name'):
            if self.is_group_member:
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name
                )

    async def receive(self, text_data, bytes_data=None):
        data = json.loads(text_data)
        msg_type = data.get('type', 'chat')

        if msg_type == 'chat':
            await self.handle_chat(data)
        elif msg_type == 'join':
            await self.handle_join(data)
        elif msg_type == 'leave':
            await self.handle_leave(data)
        elif msg_type == 'kick':
            await self.handle_kick(data)
        elif msg_type == 'delete_room':
            await self.handle_delete_room(data)
        elif msg_type == 'load_more_history':
            await self.handle_load_more_history(data)

    async def handle_chat(self, data):
        if self.read_only_removed:
            await self.send(text_data=json.dumps({
                'type': 'removed',
                'message': '你已被移出该群聊，不能再发送消息。',
            }))
            return

        message = data.get('message', '')
        user = data.get('user', '匿名用户')

        if not message.strip():
            return

        payload = await self.save_message(message, user)
        if not payload:
            await self.send(text_data=json.dumps({
                'type': 'removed',
                'message': '你已被移出该群聊，不能再发送消息。',
            }))
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'payload': payload
            }
        )

    async def handle_join(self, data):
        self.user = data.get('user', '匿名用户')
        if self.room_name in self.room_users:
            self.room_users[self.room_name][self.channel_name] = {
                'username': self.user,
                'is_owner': await self.check_is_owner(),
                'avatar_label': await self.get_user_avatar_label(),
                'friend_id': await self.get_user_friend_id(),
            }
        await self.broadcast_user_list()

    async def handle_leave(self, data):
        if self.room_name in self.room_users:
            if self.channel_name in self.room_users[self.room_name]:
                del self.room_users[self.room_name][self.channel_name]
        await self.broadcast_user_list()

    async def handle_kick(self, data):
        kicked_user = data.get('user')

        if await self.check_is_owner():
            removal_result = await self.remove_room_member(kicked_user)
            if not removal_result.get('ok'):
                return

            kicked_channel = None
            if self.room_name in self.room_users:
                for channel, user_info in list(self.room_users[self.room_name].items()):
                    if user_info['username'] == kicked_user:
                        kicked_channel = channel
                        break

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'system_message',
                    'message': f'{kicked_user} 已被房主移出房间'
                }
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'member_removed_message',
                    'username': kicked_user,
                }
            )
            if kicked_channel:
                await self.channel_layer.group_discard(self.room_group_name, kicked_channel)
                await self.channel_layer.send(
                    kicked_channel,
                    {
                        'type': 'kick_message'
                    }
                )
                del self.room_users[self.room_name][kicked_channel]
            await self.broadcast_user_list()

    async def handle_delete_room(self, data):
        if await self.check_is_owner():
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'delete_room_message',
                    'message': f'房间已被房主删除'
                }
            )

            await self.delete_room_from_db()

    async def handle_load_more_history(self, data):
        """加载更多历史记录"""
        offset = data.get('offset', 0)
        history = await self.get_history(offset=offset, limit=self.HISTORY_LIMIT)
        
        await self.send(text_data=json.dumps({
            'type': 'history',
            'messages': history,
            'has_more': len(history) == self.HISTORY_LIMIT
        }))

    @database_sync_to_async
    def save_message(self, message, username):
        """保存消息到数据库"""
        from .models import Message, Room, RoomMembership
        try:
            room = Room.objects.get(name=self.room_name)
            user = self.scope['user'] if self.scope['user'].is_authenticated else None
            location_label = ''
            appearance = self.get_default_appearance()

            if user:
                membership, _ = RoomMembership.objects.get_or_create(
                    room=room,
                    user=user,
                    defaults={'is_active': True, 'removed_at': None},
                )
                if room.created_by_id != user.id and not membership.is_active:
                    return None
                profile = self.get_or_create_profile(user)
                appearance = profile.to_payload()
                if profile.show_location and hasattr(user, 'location'):
                    location_label = user.location.display_label

            msg = Message.objects.create(
                room=room,
                user=user,
                username=username,
                message=message,
                message_type='chat',
                location_label=location_label,
            )
            return self.serialize_message(msg, appearance_override=appearance)
        except (OperationalError, ProgrammingError):
            try:
                room = Room.objects.get(name=self.room_name)
                user = self.scope['user'] if self.scope['user'].is_authenticated else None
                msg = Message.objects.create(
                    room=room,
                    user=user,
                    username=username,
                    message=message,
                    message_type='chat',
                )
                return self.serialize_message(msg)
            except Exception as inner_error:
                print(f"保存消息失败: {inner_error}")
                return {
                    'message': message,
                    'user': username,
                    'type': 'chat',
                    'timestamp': None,
                    'location': '',
                    'appearance': self.get_default_appearance(),
                    'avatar_label': self.get_avatar_label_for_username(username),
                }
        except Exception as e:
            print(f"保存消息失败: {e}")
            return {
                'message': message,
                'user': username,
                'type': 'chat',
                'timestamp': None,
                'location': '',
                'appearance': self.get_default_appearance(),
                'avatar_label': self.get_avatar_label_for_username(username),
            }

    @database_sync_to_async
    def get_history(self, offset=0, limit=50):
        """获取历史记录"""
        from .models import Message, Room
        try:
            room = Room.objects.get(name=self.room_name)
            messages = Message.objects.filter(room=room).select_related('user', 'user__chat_profile').order_by('-timestamp')[offset:offset + limit]

            return [self.serialize_message(msg) for msg in messages]
        except Exception as e:
            print(f"获取历史记录失败: {e}")
            return []

    async def send_history(self):
        """发送初始历史记录"""
        history = await self.get_history(limit=self.HISTORY_LIMIT)
        
        if history:
            await self.send(text_data=json.dumps({
                'type': 'history',
                'messages': history,
                'has_more': len(history) == self.HISTORY_LIMIT
            }))

    @database_sync_to_async
    def delete_room_from_db(self):
        from .models import Room
        Room.objects.filter(name=self.room_name).delete()

    async def send_welcome(self):
        await self.send(text_data=json.dumps({
            'type': 'system',
            'message': f'欢迎来到房间 {self.room_name}！',
            'user': 'System'
        }))

    async def broadcast_user_list(self):
        users = await self.get_users_dict()
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_list',
                'users': users
            }
        )

    @database_sync_to_async
    def get_users_dict(self):
        from .models import Room, RoomMembership, UserSession

        connected_users = {}
        if self.room_name in self.room_users:
            connected_users = {
                value['username']: value
                for value in self.room_users[self.room_name].values()
            }

        try:
            room = Room.objects.get(name=self.room_name)
        except Room.DoesNotExist:
            return {
                username: {
                    'is_owner': bool(meta.get('is_owner')),
                    'avatar_label': meta.get('avatar_label', self.get_avatar_label_for_username(username)),
                    'friend_id': meta.get('friend_id', ''),
                    'is_online': True,
                }
                for username, meta in connected_users.items()
            }

        memberships = RoomMembership.objects.filter(
            room=room,
            is_active=True,
        ).select_related('user', 'user__chat_profile')
        online_user_ids = set(UserSession.objects.values_list('user_id', flat=True))
        users = {}

        for membership in memberships:
            linked_user = membership.user
            profile = getattr(linked_user, 'chat_profile', None) or self.get_or_create_profile(linked_user)
            connected_meta = connected_users.get(linked_user.username, {})
            users[linked_user.username] = {
                'is_owner': bool(room.created_by_id == linked_user.id),
                'avatar_label': connected_meta.get('avatar_label', profile.get_avatar_label()),
                'friend_id': connected_meta.get('friend_id', profile.friend_id),
                'is_online': linked_user.id in online_user_ids,
            }

        for username, meta in connected_users.items():
            if username in users:
                continue
            users[username] = {
                'is_owner': bool(meta.get('is_owner')),
                'avatar_label': meta.get('avatar_label', self.get_avatar_label_for_username(username)),
                'friend_id': meta.get('friend_id', ''),
                'is_online': True,
            }

        return users

    @database_sync_to_async
    def is_removed_from_room(self):
        from .models import Room, RoomMembership

        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            return False

        try:
            room = Room.objects.get(name=self.room_name)
        except Room.DoesNotExist:
            return False

        try:
            membership, _ = RoomMembership.objects.get_or_create(
                room=room,
                user=user,
                defaults={'is_active': True, 'removed_at': None},
            )
            if room.created_by_id == user.id and not membership.is_active:
                membership.is_active = True
                membership.removed_at = None
                membership.save(update_fields=['is_active', 'removed_at'])
                return False
            return not membership.is_active
        except (OperationalError, ProgrammingError):
            return False

    @database_sync_to_async
    def remove_room_member(self, username):
        from django.contrib.auth.models import User
        from .models import Room, RoomMembership

        try:
            room = Room.objects.get(name=self.room_name)
            target_user = User.objects.get(username=username)
        except (Room.DoesNotExist, User.DoesNotExist):
            return {'ok': False}

        if room.created_by_id == target_user.id:
            return {'ok': False}

        try:
            membership, _ = RoomMembership.objects.get_or_create(
                room=room,
                user=target_user,
                defaults={'is_active': True, 'removed_at': None},
            )
            if not membership.is_active:
                return {'ok': True}

            membership.is_active = False
            membership.removed_at = timezone.now()
            membership.save(update_fields=['is_active', 'removed_at'])
            return {'ok': True}
        except (OperationalError, ProgrammingError):
            return {'ok': False}

    async def check_is_owner(self):
        return await self._check_is_owner()

    @database_sync_to_async
    def _check_is_owner(self):
        from .models import Room
        try:
            room = Room.objects.get(name=self.room_name)
            if self.scope['user'].is_authenticated:
                return room.created_by == self.scope['user']
        except:
            pass
        return False

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    async def system_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'system',
            'message': event['message'],
            'user': 'System'
        }))

    async def user_list(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_list',
            'users': event['users']
        }))

    async def presence_refresh(self, event):
        await self.broadcast_user_list()

    async def kick_message(self, event):
        self.read_only_removed = True
        await self.send(text_data=json.dumps({
            'type': 'kick',
            'message': '您已被房主踢出房间'
        }))

    async def member_removed_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'member_removed',
            'username': event['username'],
        }))

    async def delete_room_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'delete_room',
            'message': '房间已被房主删除'
        }))

    def get_default_appearance(self):
        return {
            'avatar_label': self.get_avatar_label_for_username('用户'),
            'theme': DEFAULT_CHAT_THEME,
            'style': DEFAULT_CHAT_STYLE,
            'bubble_bg': 'linear-gradient(135deg, #c96c43 0%, #9d4528 100%)',
            'bubble_text': '#fff7f0',
            'bubble_accent': 'rgba(255, 247, 240, 0.8)',
            'nameplate_bg': 'rgba(255, 243, 235, 0.18)',
            'radius': '18px',
            'shadow': '0 16px 28px rgba(62, 44, 31, 0.18)',
            'border': '1px solid rgba(255, 255, 255, 0.12)',
            'backdrop_filter': 'none',
        }

    def get_or_create_profile(self, user):
        from .models import UserChatProfile
        profile, _ = UserChatProfile.objects.get_or_create(
            user=user,
            defaults={
                'avatar_label': '',
                'color_theme': DEFAULT_CHAT_THEME,
                'bubble_style': DEFAULT_CHAT_STYLE,
                'show_location': True,
            },
        )
        return profile

    def serialize_message(self, msg, appearance_override=None):
        appearance = appearance_override or self.get_default_appearance()
        avatar_label = self.get_avatar_label_for_username(msg.username)
        location_label = msg.location_label
        if msg.user_id:
            profile = getattr(msg.user, 'chat_profile', None) or self.get_or_create_profile(msg.user)
            appearance = profile.to_payload()
            avatar_label = profile.get_avatar_label()
            user_location = getattr(msg.user, 'location', None)
            if profile.show_location and user_location and (
                not location_label or GeoIPService.location_needs_refresh(user_location) or not GeoIPService.has_chinese_text(location_label)
            ):
                location_label = user_location.display_label

        return {
            'message': msg.message,
            'user': msg.username,
            'type': msg.message_type,
            'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
            'location': location_label,
            'appearance': appearance,
            'avatar_label': avatar_label,
            'friend_id': appearance.get('friend_id', ''),
        }

    @database_sync_to_async
    def get_user_avatar_label(self):
        user = self.scope.get('user')
        if user and user.is_authenticated:
            profile = self.get_or_create_profile(user)
            return profile.get_avatar_label()
        return self.get_avatar_label_for_username(self.user)

    @database_sync_to_async
    def get_user_friend_id(self):
        user = self.scope.get('user')
        if user and user.is_authenticated:
            profile = self.get_or_create_profile(user)
            return profile.friend_id
        return ''

    def get_avatar_label_for_username(self, username):
        normalized = (username or '').strip()
        if not normalized:
            return '用户'
        if any('\u4e00' <= char <= '\u9fff' for char in normalized):
            return normalized[:2]
        return normalized[:2].upper()


class DirectChatConsumer(AsyncWebsocketConsumer):
    @staticmethod
    def build_group_name(username_a, username_b):
        ordered = sorted([(username_a or '').lower(), (username_b or '').lower()])
        room_hash = hashlib.sha256('::'.join(ordered).encode('utf-8')).hexdigest()[:32]
        return f"dm_{room_hash}"

    async def connect(self):
        self.current_user = self.scope.get('user')
        if not self.current_user or not self.current_user.is_authenticated:
            await self.close()
            return

        self.other_username = self.scope['url_route']['kwargs']['username']
        connection = await self.get_connection_data()
        if not connection:
            await self.close()
            return

        self.other_user_id = connection['other_user_id']
        self.room_group_name = self.build_group_name(self.current_user.username, self.other_username)

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data, bytes_data=None):
        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            return

        if payload.get('type') != 'chat':
            return

        message = (payload.get('message') or '').strip()
        if not message:
            return

        saved_payload = await self.save_direct_message(message)
        if not saved_payload:
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'direct_message_event',
                'payload': saved_payload,
            }
        )

    async def direct_message_event(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    @database_sync_to_async
    def get_connection_data(self):
        from django.contrib.auth.models import User
        from .models import DirectConversation, DirectConversationState, Friendship

        try:
            other_user = User.objects.get(username=self.other_username)
        except User.DoesNotExist:
            return None

        if other_user == self.current_user:
            return None

        if not Friendship.objects.filter(user=self.current_user, friend=other_user).exists():
            return None

        ordered_users = sorted([self.current_user, other_user], key=lambda item: item.id)
        conversation, _ = DirectConversation.objects.get_or_create(user1=ordered_users[0], user2=ordered_users[1])
        DirectConversationState.objects.get_or_create(conversation=conversation, user=self.current_user)
        DirectConversationState.objects.get_or_create(conversation=conversation, user=other_user)
        return {
            'conversation_id': conversation.id,
            'other_user_id': other_user.id,
        }

    @database_sync_to_async
    def save_direct_message(self, message):
        from django.contrib.auth.models import User
        from .models import DirectConversation, DirectConversationState, DirectMessage, Friendship

        try:
            other_user = User.objects.get(username=self.other_username)
        except User.DoesNotExist:
            return None

        if not Friendship.objects.filter(user=self.current_user, friend=other_user).exists():
            return None

        ordered_users = sorted([self.current_user, other_user], key=lambda item: item.id)
        conversation, _ = DirectConversation.objects.get_or_create(user1=ordered_users[0], user2=ordered_users[1])
        DirectConversationState.objects.get_or_create(conversation=conversation, user=self.current_user)
        DirectConversationState.objects.get_or_create(conversation=conversation, user=other_user)
        direct_message = DirectMessage.objects.create(
            conversation=conversation,
            sender=self.current_user,
            content=message,
        )

        profile = self.get_or_create_profile(self.current_user)
        return {
            'type': 'chat',
            'message': direct_message.content,
            'user': self.current_user.username,
            'timestamp': direct_message.created_at.isoformat(),
            'avatar_label': profile.get_avatar_label(),
            'appearance': profile.to_payload(),
        }

    def get_or_create_profile(self, user):
        from .models import UserChatProfile
        profile, _ = UserChatProfile.objects.get_or_create(
            user=user,
            defaults={
                'avatar_label': '',
                'color_theme': DEFAULT_CHAT_THEME,
                'bubble_style': DEFAULT_CHAT_STYLE,
                'show_location': True,
            },
        )
        return profile
