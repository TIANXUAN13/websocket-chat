# chat/consumers.py
import hashlib
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


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
        
        if self.scope['user'].is_authenticated:
            self.user = self.scope['user'].username
        else:
            self.user = '匿名用户'

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        if self.room_name not in self.room_users:
            self.room_users[self.room_name] = {}

        self.room_users[self.room_name][self.channel_name] = {
            'username': self.user,
            'is_owner': await self.check_is_owner()
        }

        # 发送欢迎消息和历史记录
        await self.send_welcome()
        await self.send_history()
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
        message = data.get('message', '')
        user = data.get('user', '匿名用户')

        # 保存消息到数据库
        await self.save_message(message, user)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'user': user
            }
        )

    async def handle_join(self, data):
        self.user = data.get('user', '匿名用户')
        if self.room_name in self.room_users:
            self.room_users[self.room_name][self.channel_name] = {
                'username': self.user,
                'is_owner': await self.check_is_owner()
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
            kicked_channel = None
            if self.room_name in self.room_users:
                for channel, user_info in self.room_users[self.room_name].items():
                    if user_info['username'] == kicked_user:
                        kicked_channel = channel
                        break

            if kicked_channel:
                await self.channel_layer.send(
                    kicked_channel,
                    {
                        'type': 'kick_message'
                    }
                )

                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'system_message',
                        'message': f'{kicked_user} 已被房主踢出房间'
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
        history = await self.get_history(offset=offset, limit=self.HISTORY_LIMIT + offset)
        
        await self.send(text_data=json.dumps({
            'type': 'history',
            'messages': history,
            'has_more': len(history) >= self.HISTORY_LIMIT
        }))

    @database_sync_to_async
    def save_message(self, message, username):
        """保存消息到数据库"""
        from .models import Room, Message
        try:
            room = Room.objects.get(name=self.room_name)
            user = self.scope['user'] if self.scope['user'].is_authenticated else None
            
            Message.objects.create(
                room=room,
                user=user,
                username=username,
                message=message,
                message_type='chat'
            )
        except Exception as e:
            print(f"保存消息失败: {e}")

    @database_sync_to_async
    def get_history(self, offset=0, limit=50):
        """获取历史记录"""
        from .models import Room, Message
        try:
            room = Room.objects.get(name=self.room_name)
            messages = Message.objects.filter(room=room).order_by('-timestamp')[offset:limit]
            
            return [{
                'message': msg.message,
                'user': msg.username,
                'type': msg.message_type,
                'timestamp': msg.timestamp.isoformat()
            } for msg in messages]
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
                'has_more': False
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
        users = self.get_users_dict()
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_list',
                'users': users
            }
        )

    def get_users_dict(self):
        if self.room_name in self.room_users:
            return {v['username']: v['is_owner'] for v in self.room_users[self.room_name].values()}
        return {}

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
        await self.send(text_data=json.dumps({
            'type': 'chat',
            'message': event['message'],
            'user': event['user']
        }))

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

    async def kick_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'kick',
            'message': '您已被房主踢出房间'
        }))

    async def delete_room_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'delete_room',
            'message': '房间已被房主删除'
        }))
