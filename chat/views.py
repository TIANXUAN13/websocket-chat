import json
import hashlib
import io
import re
from urllib.parse import quote

# chat/views.py
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from PIL import Image, ImageOps
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import password_validators_help_text_html
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.utils.safestring import mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.files.base import ContentFile
from django.contrib.sessions.models import Session
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q
from .forms import AdminUserPasswordForm, ProfilePasswordChangeForm, RegistrationForm, SiteConfigurationForm
from .models import (
    DirectConversation,
    DirectConversationState,
    DirectMessage,
    FriendRequest,
    Friendship,
    Room,
    RoomInvitation,
    RoomJoinRequest,
    RoomMembership,
    SiteConfiguration,
    RoomVisitState,
    UserChatProfile,
    UserSession,
)
from .presets import CHAT_BUBBLE_STYLES, CHAT_COLOR_THEMES, DEFAULT_CHAT_STYLE, DEFAULT_CHAT_THEME
from .services.geoip_service import GeoIPService


DEFAULT_ROOM_AVATARS = ['💬', '🐱', '🐶', '🐻', '🎮', '📚', '☕', '🌙', '🎵', '🍀']
MAX_AVATAR_BYTES = 1024 * 1024
MAX_AVATAR_DIMENSION = 720
MAX_ROOM_ADMIN_COUNT = 10
QUOTED_MESSAGE_PATTERN = re.compile(r'^\[\[quote\|([^|\]]*)\|([^|\]]*)\|([^|\]]*)\]\]\n?([\s\S]*)$')


def build_room_group_name(room_name):
    return f"chat_{hashlib.sha256(room_name.encode('utf-8')).hexdigest()[:32]}"


def get_safe_next_url(request, fallback_name='chat_index'):
    fallback_url = reverse(fallback_name)
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback_url


def get_thread_preview_text(message_text, limit=36):
    raw = (message_text or '').strip()
    if not raw:
        return ''

    match = QUOTED_MESSAGE_PATTERN.match(raw)
    if match:
        raw = (match.group(4) or '').strip()

    if not raw:
        raw = '引用消息'

    return raw[:limit]


def notify_user_presence_changed(user):
    if not user or not user.is_authenticated:
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    room_names = Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True)
    ).values_list('name', flat=True).distinct()

    for room_name in room_names:
        async_to_sync(channel_layer.group_send)(
            build_room_group_name(room_name),
            {
                'type': 'presence_refresh',
            }
        )


def get_or_create_chat_profile(user):
    profile, _ = UserChatProfile.objects.get_or_create(
        user=user,
        defaults={
            'friend_id': UserChatProfile.generate_unique_friend_id(user.username, exclude_user_id=user.id),
            'avatar_label': '',
            'color_theme': DEFAULT_CHAT_THEME,
            'bubble_style': DEFAULT_CHAT_STYLE,
            'show_location': True,
        },
    )
    if not profile.friend_id:
        profile.friend_id = UserChatProfile.generate_unique_friend_id(user.username, exclude_user_id=user.id)
        profile.save(update_fields=['friend_id'])
    return profile


def compress_image_upload(uploaded_file, base_name, upload_dir):
    try:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except Exception:
        raise ValueError('无法识别这张图片，请重新选择 JPG、PNG 或 WebP 图片')

    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    elif image.mode == 'L':
        image = image.convert('RGB')

    image.thumbnail((MAX_AVATAR_DIMENSION, MAX_AVATAR_DIMENSION), Image.Resampling.LANCZOS)
    quality = 88
    working_image = image

    while True:
        buffer = io.BytesIO()
        working_image.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
        content = buffer.getvalue()
        if len(content) <= MAX_AVATAR_BYTES or quality <= 42:
            if len(content) <= MAX_AVATAR_BYTES:
                break

            resized_width = max(160, int(working_image.width * 0.88))
            resized_height = max(160, int(working_image.height * 0.88))
            if resized_width == working_image.width and resized_height == working_image.height:
                break
            working_image = working_image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            quality = min(quality + 6, 88)
            continue
        quality -= 8

    normalized_base = base_name or 'file'
    safe_name = ''.join(ch for ch in normalized_base.lower() if ch.isalnum() or ch == '_') or 'file'
    hashed_suffix = hashlib.sha1(normalized_base.encode('utf-8')).hexdigest()[:10]
    return ContentFile(content, name=f'{upload_dir}/{safe_name}_{hashed_suffix}.jpg')


def compress_avatar_upload(uploaded_file, username):
    return compress_image_upload(uploaded_file, f'{username}_avatar', 'avatars')


def compress_room_avatar_upload(uploaded_file, room_name):
    return compress_image_upload(uploaded_file, f'{room_name}_room_avatar', 'room_avatars')


def are_friends(user, other_user):
    return Friendship.objects.filter(user=user, friend=other_user).exists()


def get_or_create_direct_conversation(user, other_user):
    ordered_users = sorted([user, other_user], key=lambda item: item.id)
    conversation, _ = DirectConversation.objects.get_or_create(user1=ordered_users[0], user2=ordered_users[1])
    DirectConversationState.objects.get_or_create(conversation=conversation, user=user)
    DirectConversationState.objects.get_or_create(conversation=conversation, user=other_user)
    return conversation


def get_or_create_room_visit_state(user, room):
    state, _ = RoomVisitState.objects.get_or_create(room=room, user=user)
    return state


def get_or_create_room_membership(room, user):
    try:
        membership, created = RoomMembership.objects.get_or_create(
            room=room,
            user=user,
            defaults={
                'is_active': True,
                'removed_at': None,
            },
        )
        if room.created_by_id == user.id and not membership.is_active:
            membership.is_active = True
            membership.removed_at = None
            membership.save(update_fields=['is_active', 'removed_at'])
        return membership, created
    except (OperationalError, ProgrammingError):
        return None, False


def get_room_membership(room, user):
    if not room or not user or not user.is_authenticated:
        return None
    return RoomMembership.objects.filter(room=room, user=user).first()


def can_manage_room_avatar(room, user, membership=None):
    if not room or not user or not user.is_authenticated:
        return False
    if room.created_by_id == user.id:
        return True
    membership = membership or get_room_membership(room, user)
    return bool(membership and membership.is_active and membership.is_admin)


def get_accessible_rooms_queryset(user):
    if not user or not user.is_authenticated:
        return Room.objects.none()
    return Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True)
    ).distinct()


def get_pending_room_invites_queryset(user):
    if not user or not user.is_authenticated:
        return RoomInvitation.objects.none()
    return RoomInvitation.objects.filter(
        invited_user=user,
        status=RoomInvitation.STATUS_PENDING,
    ).select_related('room', 'invited_by', 'invited_by__chat_profile')


def get_manageable_rooms_queryset(user):
    if not user or not user.is_authenticated:
        return Room.objects.none()
    return Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True, memberships__is_admin=True)
    ).distinct()


def can_manage_room_members(room, user, membership=None):
    return can_manage_room_avatar(room, user, membership=membership)


def get_direct_visibility_cutoff(state):
    cutoffs = [value for value in [state.cleared_at, state.deleted_at] if value]
    return max(cutoffs) if cutoffs else None


def get_visible_direct_messages(conversation, state):
    messages_qs = conversation.messages.select_related('sender', 'sender__chat_profile')
    cutoff = get_direct_visibility_cutoff(state)
    if cutoff:
        messages_qs = messages_qs.filter(created_at__gt=cutoff)
    return messages_qs


def build_room_threads(user):
    threads = []
    rooms = get_accessible_rooms_queryset(user).prefetch_related('messages')
    embed_version = '20260322n'
    for room in rooms:
        latest_message = room.messages.order_by('-timestamp').first()

        state = get_or_create_room_visit_state(user, room)
        unread_qs = room.messages.exclude(user=user)
        if state.last_read_at:
            unread_qs = unread_qs.filter(timestamp__gt=state.last_read_at)

        if latest_message:
            last_message_preview = get_thread_preview_text(latest_message.message)
            last_message_at = latest_message.timestamp
        else:
            last_message_preview = room.description or '新群聊已创建，来发第一条消息吧'
            last_message_at = room.created_at

        threads.append({
            'type': 'room',
            'name': room.name,
            'avatar_label': room.avatar,
            'avatar_url': room.avatar_url,
            'url': reverse('chat_room', args=[room.name]),
            'embed_url': f"{reverse('chat_room', args=[room.name])}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=room&target={quote(room.name)}",
            'unread_count': unread_qs.count(),
            'last_message_preview': last_message_preview,
            'last_message_at': last_message_at,
        })

    return sorted(threads, key=lambda item: item['last_message_at'], reverse=True)


def build_direct_threads(user):
    threads = []
    embed_version = '20260322n'
    conversations = DirectConversation.objects.filter(
        Q(user1=user) | Q(user2=user)
    ).select_related('user1', 'user2', 'user1__chat_profile', 'user2__chat_profile')

    for conversation in conversations:
        state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=user)
        visible_messages = get_visible_direct_messages(conversation, state)
        latest_message = visible_messages.order_by('-created_at').first()
        if not latest_message:
            continue

        other_user = conversation.other_user(user)
        unread_qs = visible_messages.exclude(sender=user)
        if state.last_read_at:
            unread_qs = unread_qs.filter(created_at__gt=state.last_read_at)

        threads.append({
            'type': 'direct',
            'name': other_user.username,
            'friend_id': getattr(getattr(other_user, 'chat_profile', None), 'friend_id', ''),
            'avatar_label': getattr(getattr(other_user, 'chat_profile', None), 'get_avatar_label', lambda: other_user.username[:2].upper())(),
            'avatar_url': getattr(getattr(other_user, 'chat_profile', None), 'avatar_url', ''),
            'url': reverse('direct_chat', args=[other_user.username]),
            'embed_url': f"{reverse('direct_chat', args=[other_user.username])}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=direct&target={quote(other_user.username)}",
            'delete_url': reverse('delete_direct_conversation', args=[other_user.username]),
            'unread_count': unread_qs.count(),
            'last_message_preview': get_thread_preview_text(latest_message.content),
            'last_message_at': latest_message.created_at,
        })

    return sorted(threads, key=lambda item: item['last_message_at'], reverse=True)


def get_inbox_context(user):
    pending_requests = FriendRequest.objects.filter(
        recipient=user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')
    pending_room_invites = get_pending_room_invites_queryset(user)
    pending_room_join_requests = RoomJoinRequest.objects.filter(
        room__in=get_manageable_rooms_queryset(user),
        status=RoomJoinRequest.STATUS_PENDING,
    ).select_related('room', 'requester', 'requester__chat_profile').distinct()
    rejected_room_join_requests = RoomJoinRequest.objects.filter(
        requester=user,
        status=RoomJoinRequest.STATUS_REJECTED,
    ).select_related('room')[:12]
    room_threads = build_room_threads(user)
    direct_threads = build_direct_threads(user)
    conversation_threads = sorted(room_threads + direct_threads, key=lambda item: item['last_message_at'], reverse=True)

    return {
        'pending_requests': pending_requests,
        'pending_friend_requests_count': pending_requests.count(),
        'pending_room_invites': pending_room_invites,
        'pending_room_invites_count': pending_room_invites.count(),
        'pending_room_join_requests': pending_room_join_requests,
        'pending_room_join_requests_count': pending_room_join_requests.count(),
        'rejected_room_join_requests': rejected_room_join_requests,
        'room_threads': room_threads,
        'direct_threads': direct_threads,
        'conversation_threads': conversation_threads,
        'total_unread_count': sum(item['unread_count'] for item in room_threads + direct_threads),
        'friend_requests_history': FriendRequest.objects.filter(
            recipient=user,
        ).exclude(status=FriendRequest.STATUS_PENDING).select_related('sender', 'sender__chat_profile')[:12],
    }


def build_room_member_records(room, current_user):
    member_records = []
    try:
        active_memberships = room.memberships.filter(is_active=True).select_related('user', 'user__chat_profile')
        online_user_ids = set(UserSession.objects.values_list('user_id', flat=True))

        if not active_memberships.exists():
            fallback_usernames = set(room.messages.exclude(username='').values_list('username', flat=True))
            if room.created_by:
                fallback_usernames.add(room.created_by.username)
            fallback_users = User.objects.filter(username__in=fallback_usernames)
            for linked_user in fallback_users:
                get_or_create_room_membership(room, linked_user)
            active_memberships = room.memberships.filter(is_active=True).select_related('user', 'user__chat_profile')

        for membership in active_memberships:
            linked_user = membership.user
            profile = get_or_create_chat_profile(linked_user)
            member_records.append({
                'username': linked_user.username,
                'avatar_label': profile.get_avatar_label(),
                'avatar_url': profile.avatar_url,
                'friend_id': profile.friend_id,
                'is_owner': bool(room.created_by and room.created_by_id == linked_user.id),
                'is_admin': bool(membership.is_admin),
                'is_self': bool(current_user and current_user.id == linked_user.id),
                'is_online': linked_user.id in online_user_ids,
            })
    except (OperationalError, ProgrammingError):
        usernames = set(room.messages.exclude(username='').values_list('username', flat=True))
        if room.created_by:
            usernames.add(room.created_by.username)
        if current_user and current_user.is_authenticated:
            usernames.add(current_user.username)

        users_by_username = {
            item.username: item
            for item in User.objects.filter(username__in=usernames).select_related('chat_profile')
        }
        for username in sorted(usernames, key=lambda item: ((item or '').lower(), item)):
            linked_user = users_by_username.get(username)
            profile = get_or_create_chat_profile(linked_user) if linked_user else None
            member_records.append({
                'username': username,
                'avatar_label': profile.get_avatar_label() if profile else username[:2],
                'avatar_url': profile.avatar_url if profile else '',
                'friend_id': profile.friend_id if profile else '',
                'is_owner': bool(room.created_by and room.created_by.username == username),
                'is_admin': False,
                'is_self': bool(current_user and current_user.username == username),
                'is_online': bool(linked_user and linked_user.sessions.exists()),
            })

    member_records.sort(key=lambda item: (
        not item['is_owner'],
        not item['is_self'],
        not item['is_admin'],
        item['username'].lower(),
    ))
    return member_records


@never_cache
@ensure_csrf_cookie
@csrf_protect
def login_view(request):
    """登录页面"""
    if request.user.is_authenticated:
        return redirect('chat_index')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            
            # 先登录，确保会话正确创建
            login(request, user)
            
            # 强制保存会话，确保会话键存在
            request.session.save()
            
            # 获取当前会话键
            current_session_key = request.session.session_key
            if not current_session_key:
                # 如果仍然没有会话键，手动创建
                request.session.create()
                request.session.save()
                current_session_key = request.session.session_key
            
            # 删除该用户的所有现有会话记录
            UserSession.objects.filter(user=user).delete()
            # 创建新的会话记录
            try:
                UserSession.objects.create(user=user, session_key=current_session_key)
            except Exception as e:
                # 如果创建失败，记录错误但不阻止登录
                print(f"创建 UserSession 记录时出错: {e}")
            notify_user_presence_changed(user)
            
            # 获取并保存用户地理位置信息
            try:
                ip_address = GeoIPService.get_client_ip(request)
                GeoIPService.save_user_location(user, ip_address)
            except Exception as e:
                # 如果地理位置获取失败，记录错误但不阻止登录
                print(f"获取用户地理位置时出错: {e}")
            
            # 再次保存会话，确保所有更改都被保存
            request.session.save()
            
            next_url = request.GET.get('next', 'chat_index')
            return redirect(next_url)
    else:
        form = AuthenticationForm()
    
    return render(request, 'chat/login.html', {'form': form})


@never_cache
@ensure_csrf_cookie
def register_view(request):
    """注册页面"""
    if request.user.is_authenticated:
        return redirect('chat_index')
    
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            profile = get_or_create_chat_profile(user)
            requested_friend_id = form.cleaned_data.get('friend_id', '').strip().lower()
            if requested_friend_id:
                if UserChatProfile.objects.exclude(user=user).filter(friend_id=requested_friend_id).exists():
                    form.add_error('friend_id', '这个好友 ID 已经被使用了')
                    user.delete()
                    return render(request, 'chat/register.html', {'form': form})
                profile.friend_id = requested_friend_id
                profile.save(update_fields=['friend_id'])
            login(request, user)
            
            # 强制保存会话，确保会话键存在
            request.session.save()
            
            # 获取当前会话键
            current_session_key = request.session.session_key
            if not current_session_key:
                # 如果仍然没有会话键，手动创建
                request.session.create()
                request.session.save()
                current_session_key = request.session.session_key
            
            # 创建 UserSession 记录
            try:
                UserSession.objects.create(user=user, session_key=current_session_key)
            except Exception as e:
                # 如果创建失败，记录错误但不阻止登录
                print(f"创建 UserSession 记录时出错: {e}")
            notify_user_presence_changed(user)
            
            # 获取并保存用户地理位置信息
            try:
                ip_address = GeoIPService.get_client_ip(request)
                GeoIPService.save_user_location(user, ip_address)
            except Exception as e:
                # 如果地理位置获取失败，记录错误但不阻止登录
                print(f"获取用户地理位置时出错: {e}")
            
            # 再次保存会话，确保所有更改都被保存
            request.session.save()
            
            return redirect('chat_index')
    else:
        form = RegistrationForm()
    
    return render(request, 'chat/register.html', {'form': form})


@login_required
def index(request):
    """聊天室首页"""
    get_or_create_chat_profile(request.user)

    if request.method == 'POST':
        room_name = request.POST.get('room_name', '').strip()
        room_avatar = request.POST.get('room_avatar', '💬').strip() or '💬'
        room_description = request.POST.get('room_description', '').strip()

        if room_avatar not in DEFAULT_ROOM_AVATARS:
            room_avatar = '💬'
        if not room_description:
            room_description = '一起聊聊吧'

        if room_name:
            if Room.objects.filter(name=room_name).exists():
                messages.error(request, '房间已存在')
            else:
                Room.objects.create(
                    name=room_name,
                    avatar=room_avatar,
                    description=room_description[:120],
                    created_by=request.user,
                )
                room = Room.objects.get(name=room_name)
                get_or_create_room_membership(room, request.user)
                messages.success(request, f'房间 "{room_name}" 创建成功')
        return redirect('chat_index')
    
    rooms = get_accessible_rooms_queryset(request.user)
    profile = get_or_create_chat_profile(request.user)
    incoming_friend_requests = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')[:5]
    inbox_context = get_inbox_context(request.user)
    room_unread_map = {item['name']: item['unread_count'] for item in inbox_context['room_threads']}
    direct_unread_map = {item['name']: item['unread_count'] for item in inbox_context['direct_threads']}
    room_items = [
        {
            'room': room,
            'unread_count': room_unread_map.get(room.name, 0),
            'inbox_url': next((item['inbox_url'] for item in inbox_context['room_threads'] if item['name'] == room.name), reverse('inbox')),
        }
        for room in rooms
    ]
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile')
    friend_items = [
        {
            'friendship': item,
            'unread_count': direct_unread_map.get(item.friend.username, 0),
            'inbox_url': next((thread['inbox_url'] for thread in inbox_context['direct_threads'] if thread['name'] == item.friend.username), reverse('inbox')),
        }
        for item in friends
    ]
    active_thread = None
    active_type = request.GET.get('thread_type', '').strip()
    active_target = request.GET.get('target', '').strip()
    if active_type and active_target:
        active_thread = next(
            (
                item for item in inbox_context['conversation_threads']
                if item['type'] == active_type and item['name'] == active_target
            ),
            None,
        )

    return render(request, 'chat/index.html', {
        'rooms': rooms,
        'room_items': room_items,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'user': request.user,
        'chat_profile': profile,
        'pending_friend_requests_count': inbox_context['pending_friend_requests_count'],
        'pending_room_invites_count': inbox_context['pending_room_invites_count'],
        'pending_room_join_requests_count': inbox_context['pending_room_join_requests_count'],
        'inbox_badge_count': inbox_context['pending_friend_requests_count'] + inbox_context['pending_room_invites_count'] + inbox_context['pending_room_join_requests_count'],
        'incoming_friend_requests': incoming_friend_requests,
        'friends': friends,
        'friend_items': friend_items,
        'friends_count': friends.count(),
        'conversation_threads': inbox_context['conversation_threads'],
        'active_thread': active_thread,
    })


@login_required
def delete_room(request, room_name):
    """删除房间"""
    try:
        room = Room.objects.get(name=room_name)
        if room.created_by == request.user:
            room.delete()
            messages.success(request, f'房间 "{room_name}" 已删除')
        else:
            messages.error(request, '只有房主才能删除房间')
    except Room.DoesNotExist:
        messages.error(request, '房间不存在')
    
    return redirect('chat_index')


@login_required
@xframe_options_sameorigin
def room(request, room_name):
    """具体聊天室页面"""
    embed_mode = request.GET.get('embed') == '1'

    def redirect_to_room(target_room_name):
        target_url = reverse('chat_room', args=[target_room_name])
        if embed_mode:
            target_url = f'{target_url}?embed=1'
        return redirect(target_url)

    try:
        room = Room.objects.get(name=room_name)
        room_membership = get_room_membership(room, request.user)
        is_owner = room.created_by == request.user
        is_admin = bool(room_membership and room_membership.is_active and room_membership.is_admin)
    except Room.DoesNotExist:
        is_owner = False
        is_admin = False
        room_membership = None
        room = None

    if not room:
        messages.error(request, '房间不存在')
        return redirect('chat_index')

    if not is_owner and not (room_membership and room_membership.is_active):
        messages.error(request, '你还不是这个群聊的成员，暂时不能查看群内容')
        return redirect('chat_index')

    try:
        GeoIPService.refresh_user_location_if_needed(request.user)
    except Exception as e:
        print(f"刷新用户地理位置时出错: {e}")

    if request.method == 'POST':
        action = request.POST.get('action', 'room_settings')

        if action == 'room_avatar':
            if not can_manage_room_avatar(room, request.user, room_membership):
                messages.error(request, '只有房主或群管理员才能修改群头像')
                return redirect_to_room(room.name)

            next_avatar = request.POST.get('room_avatar', room.avatar).strip() or room.avatar
            if next_avatar not in DEFAULT_ROOM_AVATARS:
                next_avatar = '💬'

            remove_room_avatar = request.POST.get('remove_room_avatar') == 'on'
            uploaded_room_avatar = request.FILES.get('room_avatar_image')
            update_fields = ['avatar']
            room.avatar = next_avatar

            if remove_room_avatar and room.avatar_image:
                room.delete_avatar_image_file()
                room.avatar_image = None
                update_fields.append('avatar_image')

            if uploaded_room_avatar:
                try:
                    optimized_room_avatar = compress_room_avatar_upload(uploaded_room_avatar, room.name)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect_to_room(room.name)

                if room.avatar_image:
                    room.delete_avatar_image_file()
                room.avatar_image.save(optimized_room_avatar.name, optimized_room_avatar, save=False)
                if 'avatar_image' not in update_fields:
                    update_fields.append('avatar_image')

            room.save(update_fields=update_fields)
            messages.success(request, '群头像已更新')
            return redirect_to_room(room.name)

        if action == 'set_admin':
            if not is_owner:
                messages.error(request, '只有房主才能设置群管理员')
                return redirect_to_room(room.name)

            target_username = request.POST.get('target_username', '').strip()
            try:
                target_user = User.objects.get(username=target_username)
                target_membership, _ = RoomMembership.objects.get_or_create(
                    room=room,
                    user=target_user,
                    defaults={'is_active': True, 'removed_at': None},
                )
            except User.DoesNotExist:
                messages.error(request, '目标成员不存在')
                return redirect_to_room(room.name)

            if room.created_by_id == target_user.id:
                messages.error(request, '房主不需要设置为管理员')
                return redirect_to_room(room.name)
            if not target_membership.is_active:
                messages.error(request, '只能设置仍在群内的成员为管理员')
                return redirect_to_room(room.name)
            if target_membership.is_admin:
                messages.info(request, f'{target_username} 已经是群管理员')
                return redirect_to_room(room.name)

            admin_count = room.memberships.filter(is_active=True, is_admin=True).count()
            if admin_count >= MAX_ROOM_ADMIN_COUNT:
                messages.error(request, f'群管理员最多只能设置 {MAX_ROOM_ADMIN_COUNT} 个')
                return redirect_to_room(room.name)

            target_membership.is_admin = True
            target_membership.save(update_fields=['is_admin'])
            messages.success(request, f'已将 {target_username} 设为群管理员')
            return redirect_to_room(room.name)

        if action == 'revoke_admin':
            if not is_owner:
                messages.error(request, '只有房主才能取消群管理员')
                return redirect_to_room(room.name)

            target_username = request.POST.get('target_username', '').strip()
            try:
                target_user = User.objects.get(username=target_username)
                target_membership = RoomMembership.objects.get(room=room, user=target_user)
            except (User.DoesNotExist, RoomMembership.DoesNotExist):
                messages.error(request, '目标管理员不存在')
                return redirect_to_room(room.name)

            if not target_membership.is_admin:
                messages.info(request, f'{target_username} 目前不是群管理员')
                return redirect_to_room(room.name)

            target_membership.is_admin = False
            target_membership.save(update_fields=['is_admin'])
            messages.success(request, f'已取消 {target_username} 的群管理员身份')
            return redirect_to_room(room.name)

        if not is_owner:
            messages.error(request, '只有房主才能编辑房间资料')
            return redirect_to_room(room.name)

        new_room_name = request.POST.get('room_name', room.name).strip() or room.name
        new_join_policy = request.POST.get('join_policy', room.join_policy).strip() or room.join_policy
        if new_join_policy not in dict(Room.JOIN_POLICY_CHOICES):
            new_join_policy = room.join_policy
        room.description = request.POST.get('room_description', room.description).strip()[:120] or '一起聊聊吧'
        room.name = new_room_name
        room.join_policy = new_join_policy

        try:
            room.save()
        except IntegrityError:
            messages.error(request, '这个房间名已经被用了')
            return redirect_to_room(room_name)
        messages.success(request, '房间资料已更新')
        return redirect_to_room(room.name)

    chat_profile = get_or_create_chat_profile(request.user)
    room_membership = room_membership or get_room_membership(room, request.user)
    room_member_records = build_room_member_records(room, request.user)
    visit_state = get_or_create_room_visit_state(request.user, room)
    visit_state.last_read_at = timezone.now()
    visit_state.save(update_fields=['last_read_at'])
    pending_friend_requests_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    inviteable_friends = Friendship.objects.filter(user=request.user).exclude(
        friend__room_memberships__room=room,
        friend__room_memberships__is_active=True,
    ).select_related('friend', 'friend__chat_profile').distinct()
    return render(request, 'chat/room.html', {
        'room': room,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_admin_count': room.memberships.filter(is_active=True, is_admin=True).count(),
        'room_name': room.name,
        'room_name_json': mark_safe(json.dumps(room.name)),
        'room_total_members': room.total_members,
        'room_online_members': sum(1 for item in room_member_records if item.get('is_online')),
        'room_members_json': mark_safe(json.dumps(room_member_records)),
        'is_removed_from_room': bool(room_membership and (not room_membership.is_active) and room.created_by_id != request.user.id),
        'is_owner': is_owner,
        'is_admin': is_admin,
        'can_manage_room_avatar': can_manage_room_avatar(room, request.user, room_membership),
        'max_room_admin_count': MAX_ROOM_ADMIN_COUNT,
        'chat_profile': chat_profile,
        'chat_profile_payload_json': mark_safe(json.dumps(chat_profile.to_payload())),
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'inviteable_friends': inviteable_friends,
        'pending_join_requests': RoomJoinRequest.objects.filter(room=room, status=RoomJoinRequest.STATUS_PENDING).select_related('requester', 'requester__chat_profile'),
        'pending_friend_requests_count': pending_friend_requests_count,
        'inbox_badge_count': pending_friend_requests_count + get_pending_room_invites_queryset(request.user).count(),
        'embed_mode': embed_mode,
    })


@login_required
def profile_settings(request):
    """个人聊天设置"""
    try:
        GeoIPService.refresh_user_location_if_needed(request.user)
    except Exception as e:
        print(f"刷新用户地理位置时出错: {e}")

    chat_profile = get_or_create_chat_profile(request.user)
    password_form = ProfilePasswordChangeForm(request.user)
    profile_settings_url = reverse('profile_settings')

    if request.method == 'POST':
        form_type = request.POST.get('form_type', 'profile')
        if form_type == 'password':
            password_form = ProfilePasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                updated_user = password_form.save()
                update_session_auth_hash(request, updated_user)
                messages.success(request, '密码已更新')
                return redirect('profile_settings')
            messages.error(request, '密码修改失败，请检查输入内容')
        else:
            requested_friend_id = request.POST.get('friend_id', '').strip().lower()
            if requested_friend_id and UserChatProfile.objects.exclude(user=request.user).filter(friend_id=requested_friend_id).exists():
                messages.error(request, '这个好友 ID 已经被别人使用了')
                return redirect(profile_settings_url)

            if requested_friend_id and (len(requested_friend_id) < 8 or len(requested_friend_id) > 11):
                messages.error(request, '好友 ID 长度需要在 8 到 11 位之间')
                return redirect(profile_settings_url)
            if requested_friend_id and not all(ch.isalnum() or ch == '_' for ch in requested_friend_id):
                messages.error(request, '好友 ID 只能包含小写字母、数字或下划线')
                return redirect(profile_settings_url)

            chat_profile.friend_id = requested_friend_id or UserChatProfile.generate_unique_friend_id(
                request.user.username,
                exclude_user_id=request.user.id,
            )
            chat_profile.avatar_label = request.POST.get('avatar_label', '').strip()[:24]
            chat_profile.bio = request.POST.get('bio', '').strip()[:160]
            color_theme = request.POST.get('color_theme', DEFAULT_CHAT_THEME).strip()
            bubble_style = request.POST.get('bubble_style', DEFAULT_CHAT_STYLE).strip()
            chat_profile.color_theme = color_theme if color_theme in CHAT_COLOR_THEMES else DEFAULT_CHAT_THEME
            chat_profile.bubble_style = bubble_style if bubble_style in CHAT_BUBBLE_STYLES else DEFAULT_CHAT_STYLE
            chat_profile.show_location = request.POST.get('show_location') == 'on'
            remove_avatar_image = request.POST.get('remove_avatar_image') == 'on'
            uploaded_avatar = request.FILES.get('avatar_image')
            update_fields = ['friend_id', 'avatar_label', 'bio', 'color_theme', 'bubble_style', 'show_location']

            if remove_avatar_image and chat_profile.avatar_image:
                chat_profile.delete_avatar_image_file()
                chat_profile.avatar_image = None
                update_fields.append('avatar_image')

            if uploaded_avatar:
                try:
                    optimized_avatar = compress_avatar_upload(uploaded_avatar, request.user.username)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(profile_settings_url)

                if chat_profile.avatar_image:
                    chat_profile.delete_avatar_image_file()
                chat_profile.avatar_image.save(optimized_avatar.name, optimized_avatar, save=False)
                if 'avatar_image' not in update_fields:
                    update_fields.append('avatar_image')

            chat_profile.save(update_fields=update_fields)
            messages.success(request, '个人聊天设置已更新')
            return redirect('profile_settings')

    return render(request, 'chat/profile.html', {
        'chat_profile': chat_profile,
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'chat_theme_choices_json': mark_safe(json.dumps(CHAT_COLOR_THEMES)),
        'chat_style_choices_json': mark_safe(json.dumps(CHAT_BUBBLE_STYLES)),
        'default_chat_theme': DEFAULT_CHAT_THEME,
        'default_chat_style': DEFAULT_CHAT_STYLE,
        'current_location': getattr(request.user, 'location', None),
        'user': request.user,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'friendships': Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile'),
        'password_form': password_form,
        'password_help_html': mark_safe(password_validators_help_text_html()),
    })


@login_required
@require_POST
def update_precise_location(request):
    """通过浏览器经纬度更新用户更精确的位置"""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    latitude = payload.get('latitude')
    longitude = payload.get('longitude')
    if latitude is None or longitude is None:
        return JsonResponse({'ok': False, 'error': 'missing_coordinates'}, status=400)

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'invalid_coordinates'}, status=400)

    ip_address = GeoIPService.get_client_ip(request) or ''
    success = GeoIPService.save_precise_user_location(request.user, latitude, longitude, ip_address=ip_address)
    if not success:
        return JsonResponse({'ok': False, 'error': 'reverse_geocode_failed'}, status=502)

    current_location = getattr(request.user, 'location', None)
    return JsonResponse({
        'ok': True,
        'location': current_location.display_label if current_location else '',
    })


@login_required
def inbox(request):
    context = get_inbox_context(request.user)
    active_thread = None
    active_type = request.GET.get('thread_type', '').strip()
    active_target = request.GET.get('target', '').strip()
    if active_type and active_target:
        active_thread = next(
            (item for item in context['conversation_threads'] if item['type'] == active_type and item['name'] == active_target),
            None,
        )
    if not active_thread and context['conversation_threads']:
        active_thread = context['conversation_threads'][0]
    context.update({
        'chat_profile': get_or_create_chat_profile(request.user),
        'friends': Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile'),
        'active_thread': active_thread,
    })
    return render(request, 'chat/inbox.html', context)


@login_required
def inbox_summary(request):
    context = get_inbox_context(request.user)
    return JsonResponse({
        'pending_friend_requests_count': context['pending_friend_requests_count'],
        'pending_room_invites_count': context['pending_room_invites_count'],
        'pending_room_join_requests_count': context['pending_room_join_requests_count'],
        'total_unread_count': context['total_unread_count'],
        'room_threads': [
            {
                'type': item['type'],
                'name': item['name'],
                'url': item['url'],
                'unread_count': item['unread_count'],
                'preview': item['last_message_preview'],
                'timestamp': timezone.localtime(item['last_message_at']).strftime('%m-%d %H:%M') if item['last_message_at'] else '',
            }
            for item in context['room_threads']
        ],
        'direct_threads': [
            {
                'type': item['type'],
                'name': item['name'],
                'url': item['url'],
                'delete_url': item['delete_url'],
                'friend_id': item['friend_id'],
                'unread_count': item['unread_count'],
                'preview': item['last_message_preview'],
                'timestamp': timezone.localtime(item['last_message_at']).strftime('%m-%d %H:%M') if item['last_message_at'] else '',
            }
            for item in context['direct_threads']
        ],
    })


@login_required
@require_POST
def mark_room_read(request, room_name):
    try:
        room = Room.objects.get(name=room_name)
    except Room.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'room_not_found'}, status=404)

    state = get_or_create_room_visit_state(request.user, room)
    state.last_read_at = timezone.now()
    state.save(update_fields=['last_read_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def mark_direct_read(request, username):
    try:
        other_user = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    state.deleted_at = None
    state.last_read_at = timezone.now()
    state.save(update_fields=['deleted_at', 'last_read_at'])
    return JsonResponse({'ok': True})


@login_required
def friends_view(request):
    profile = get_or_create_chat_profile(request.user)
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile')
    selected_friend = friends.first()
    recent_requests = FriendRequest.objects.filter(
        recipient=request.user,
    ).exclude(status=FriendRequest.STATUS_PENDING).select_related('sender', 'sender__chat_profile')[:12]
    pending_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    return render(request, 'chat/friends.html', {
        'chat_profile': profile,
        'friends': friends,
        'selected_friend': selected_friend.friend if selected_friend else None,
        'recent_requests': recent_requests,
        'pending_friend_requests_count': pending_count,
    })


@login_required
def moments_view(request):
    profile = get_or_create_chat_profile(request.user)
    pending_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    friends_count = Friendship.objects.filter(user=request.user).count()
    return render(request, 'chat/moments.html', {
        'chat_profile': profile,
        'pending_friend_requests_count': pending_count,
        'friends_count': friends_count,
    })


@login_required
@require_POST
def remove_friend(request, username):
    try:
        other_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('friends')

    deleted_count = 0
    deleted_count += Friendship.objects.filter(user=request.user, friend=other_user).delete()[0]
    deleted_count += Friendship.objects.filter(user=other_user, friend=request.user).delete()[0]

    if deleted_count:
        messages.success(request, f'已将 {other_user.username} 从好友列表中移除')
    else:
        messages.info(request, '你们当前不是好友关系')

    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('friends')


@login_required
def user_profile(request, username):
    try:
        target_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')

    target_profile = get_or_create_chat_profile(target_user)
    own_profile = get_or_create_chat_profile(request.user)
    is_self = target_user == request.user
    is_friend = are_friends(request.user, target_user) if not is_self else False
    outgoing_request = None
    incoming_request = None
    if not is_self and not is_friend:
        outgoing_request = FriendRequest.objects.filter(
            sender=request.user,
            recipient=target_user,
            status=FriendRequest.STATUS_PENDING,
        ).first()
        incoming_request = FriendRequest.objects.filter(
            sender=target_user,
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).first()
    next_url = get_safe_next_url(request)

    return render(request, 'chat/user_profile.html', {
        'chat_profile': own_profile,
        'target_user': target_user,
        'target_profile': target_profile,
        'is_self': is_self,
        'is_friend': is_friend,
        'outgoing_request': outgoing_request,
        'incoming_request': incoming_request,
        'current_location': getattr(target_user, 'location', None),
        'next_url': next_url,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
@xframe_options_sameorigin
def direct_chat(request, username):
    try:
        other_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')

    if other_user == request.user:
        messages.info(request, '不能和自己发起私聊')
        return redirect('chat_index')

    if not are_friends(request.user, other_user):
        messages.error(request, '你们还不是好友，暂时不能私聊')
        return redirect('user_profile', username=other_user.username)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    state.deleted_at = None
    state.last_read_at = timezone.now()
    state.save(update_fields=['deleted_at', 'last_read_at'])
    own_profile = get_or_create_chat_profile(request.user)
    other_profile = get_or_create_chat_profile(other_user)
    next_url = get_safe_next_url(request, fallback_name='chat_index')

    if request.method == 'POST':
        action = request.POST.get('action', 'send')
        if action == 'clear_history':
            state.cleared_at = timezone.now()
            state.save(update_fields=['cleared_at'])
            messages.success(request, '已清空你这边看到的私聊历史')
            return redirect(f"{reverse('direct_chat', args=[other_user.username])}?next={quote(next_url)}")

        content = request.POST.get('content', '').strip()
        if content:
            DirectMessage.objects.create(conversation=conversation, sender=request.user, content=content)
            return redirect(f"{reverse('direct_chat', args=[other_user.username])}?next={quote(next_url)}")

    messages_qs = get_visible_direct_messages(conversation, state)
    messages_list = list(messages_qs)
    for item in messages_list:
        if item.sender_id and not hasattr(item.sender, 'chat_profile'):
            get_or_create_chat_profile(item.sender)

    inbox_context = get_inbox_context(request.user)
    embed_mode = request.GET.get('embed') == '1'

    return render(request, 'chat/direct_chat.html', {
        'chat_profile': own_profile,
        'chat_profile_payload_json': mark_safe(json.dumps(own_profile.to_payload())),
        'other_user': other_user,
        'other_profile': other_profile,
        'other_profile_payload_json': mark_safe(json.dumps(other_profile.to_payload())),
        'conversation': conversation,
        'messages_list': messages_list,
        'other_username_json': mark_safe(json.dumps(other_user.username)),
        'cleared_at': state.cleared_at,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'inbox_badge_count': inbox_context['pending_friend_requests_count'],
        'embed_mode': embed_mode,
        'next_url': next_url,
    })


@login_required
@require_POST
def delete_direct_conversation(request, username):
    try:
        other_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('inbox')

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    now = timezone.now()
    state.deleted_at = now
    state.last_read_at = now
    state.save(update_fields=['deleted_at', 'last_read_at'])
    messages.success(request, f'已从你的消息列表移除与 {other_user.username} 的私聊')
    return redirect('inbox')


@login_required
def create_room_page(request):
    get_or_create_chat_profile(request.user)
    if request.method == 'POST':
        room_name = request.POST.get('room_name', '').strip()
        room_avatar = request.POST.get('room_avatar', '💬').strip() or '💬'
        room_description = request.POST.get('room_description', '').strip()
        join_policy = request.POST.get('join_policy', Room.JOIN_POLICY_APPROVAL).strip() or Room.JOIN_POLICY_APPROVAL

        if room_avatar not in DEFAULT_ROOM_AVATARS:
            room_avatar = '💬'
        if join_policy not in dict(Room.JOIN_POLICY_CHOICES):
            join_policy = Room.JOIN_POLICY_APPROVAL
        if not room_description:
            room_description = '一起聊聊吧'

        if room_name:
            if Room.objects.filter(name=room_name).exists():
                messages.error(request, '房间已存在')
            else:
                room = Room.objects.create(
                    name=room_name,
                    join_policy=join_policy,
                    avatar=room_avatar,
                    description=room_description[:120],
                    created_by=request.user,
                )
                get_or_create_room_membership(room, request.user)
                messages.success(request, f'房间 "{room_name}" 创建成功')
                return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    return render(request, 'chat/create_room.html', {
        'chat_profile': get_or_create_chat_profile(request.user),
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_join_policy_choices': Room.JOIN_POLICY_CHOICES,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
def discover_rooms_page(request):
    profile = get_or_create_chat_profile(request.user)
    query = request.GET.get('q', '').strip()
    accessible_rooms = get_accessible_rooms_queryset(request.user)
    accessible_room_ids = list(accessible_rooms.values_list('id', flat=True))
    pending_request_room_ids = set(
        RoomJoinRequest.objects.filter(
            requester=request.user,
            status=RoomJoinRequest.STATUS_PENDING,
        ).values_list('room_id', flat=True)
    )
    pending_invite_room_ids = set(
        RoomInvitation.objects.filter(
            invited_user=request.user,
            status=RoomInvitation.STATUS_PENDING,
        ).values_list('room_id', flat=True)
    )

    room_results = Room.objects.order_by('-created_at')
    if query:
        room_results = room_results.filter(
            Q(name__icontains=query) | Q(room_id__icontains=query) | Q(description__icontains=query)
        )
    else:
        room_results = room_results.none()

    return render(request, 'chat/discover_rooms.html', {
        'chat_profile': profile,
        'query': query,
        'room_results': room_results[:30],
        'accessible_room_ids': accessible_room_ids,
        'pending_request_room_ids': pending_request_room_ids,
        'pending_invite_room_ids': pending_invite_room_ids,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
@require_POST
def join_room(request, room_id):
    next_url = request.POST.get('next') or reverse('discover_rooms')
    try:
        room = Room.objects.get(room_id=room_id)
    except Room.DoesNotExist:
        messages.error(request, '群聊不存在')
        return redirect(next_url)

    membership = get_room_membership(room, request.user)
    if membership and membership.is_active:
        messages.info(request, '你已经在这个群里了')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    pending_invitation = RoomInvitation.objects.filter(
        room=room,
        invited_user=request.user,
        status=RoomInvitation.STATUS_PENDING,
    ).first()
    if pending_invitation:
        messages.info(request, '你已经收到了这个群聊的邀请，请去消息中心处理')
        return redirect('inbox')

    if room.join_policy == Room.JOIN_POLICY_OPEN:
        membership, _ = RoomMembership.objects.get_or_create(
            room=room,
            user=request.user,
            defaults={'is_active': True, 'removed_at': None},
        )
        membership.is_active = True
        membership.removed_at = None
        membership.save(update_fields=['is_active', 'removed_at'])
        RoomJoinRequest.objects.filter(room=room, requester=request.user).exclude(
            status=RoomJoinRequest.STATUS_ACCEPTED
        ).delete()
        messages.success(request, f'已加入群聊「{room.name}」')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    join_request, created = RoomJoinRequest.objects.get_or_create(
        room=room,
        requester=request.user,
        defaults={
            'status': RoomJoinRequest.STATUS_PENDING,
            'note': request.POST.get('note', '').strip()[:160],
        },
    )
    if not created and join_request.status == RoomJoinRequest.STATUS_REJECTED:
        join_request.status = RoomJoinRequest.STATUS_PENDING
        join_request.note = request.POST.get('note', '').strip()[:160]
        join_request.responded_at = None
        join_request.save(update_fields=['status', 'note', 'responded_at'])
    elif not created and join_request.status == RoomJoinRequest.STATUS_ACCEPTED:
        join_request.status = RoomJoinRequest.STATUS_PENDING
        join_request.note = request.POST.get('note', '').strip()[:160]
        join_request.responded_at = None
        join_request.save(update_fields=['status', 'note', 'responded_at'])
    elif not created and join_request.status == RoomJoinRequest.STATUS_PENDING:
        messages.info(request, '你已经提交过入群申请了')
        return redirect(next_url)

    messages.success(request, f'已提交加入「{room.name}」的申请')
    return redirect(next_url)


@login_required
@require_POST
def invite_to_room(request):
    room_id = request.POST.get('room_id', '').strip()
    next_url = request.POST.get('next') or reverse('chat_index')
    try:
        room = Room.objects.get(room_id=room_id)
    except Room.DoesNotExist:
        messages.error(request, '群聊不存在')
        return redirect(next_url)

    membership = get_room_membership(room, request.user)
    if not can_manage_room_members(room, request.user, membership=membership):
        messages.error(request, '只有群主或群管理员才能邀请成员')
        return redirect(next_url)

    target_friend_id = request.POST.get('friend_id', '').strip().lower() or request.POST.get('manual_friend_id', '').strip().lower()
    target_username = request.POST.get('username', '').strip()
    target_user = None
    if target_friend_id:
        target_profile = UserChatProfile.objects.filter(friend_id=target_friend_id).select_related('user').first()
        target_user = target_profile.user if target_profile else None
    elif target_username:
        target_user = User.objects.filter(username=target_username).first()

    if not target_user:
        messages.error(request, '没有找到要邀请的用户')
        return redirect(next_url)
    if target_user == request.user:
        messages.error(request, '不能邀请自己')
        return redirect(next_url)
    if not are_friends(request.user, target_user):
        messages.error(request, '只能邀请你的好友入群')
        return redirect(next_url)

    target_membership = get_room_membership(room, target_user)
    if target_membership and target_membership.is_active:
        messages.info(request, '对方已经在群里了')
        return redirect(next_url)

    invitation, created = RoomInvitation.objects.get_or_create(
        room=room,
        invited_user=target_user,
        defaults={
            'invited_by': request.user,
            'status': RoomInvitation.STATUS_PENDING,
        },
    )
    if not created and invitation.status == RoomInvitation.STATUS_DECLINED:
        invitation.status = RoomInvitation.STATUS_PENDING
        invitation.invited_by = request.user
        invitation.responded_at = None
        invitation.save(update_fields=['status', 'invited_by', 'responded_at'])
    elif not created and invitation.status == RoomInvitation.STATUS_PENDING:
        messages.info(request, '已经发过邀请了')
        return redirect(next_url)

    messages.success(request, f'已邀请 {target_user.username} 加入「{room.name}」')
    return redirect(next_url)


@login_required
@require_POST
def respond_room_invitation(request, invitation_id):
    try:
        invitation = RoomInvitation.objects.select_related('room').get(id=invitation_id, invited_user=request.user)
    except RoomInvitation.DoesNotExist:
        messages.error(request, '群邀请不存在')
        return redirect('inbox')

    if invitation.status != RoomInvitation.STATUS_PENDING:
        messages.info(request, '这条群邀请已经处理过了')
        return redirect('inbox')

    action = request.POST.get('action', '').strip()
    if action == 'accept':
        membership, _ = RoomMembership.objects.get_or_create(
            room=invitation.room,
            user=request.user,
            defaults={'is_active': True, 'removed_at': None},
        )
        membership.is_active = True
        membership.removed_at = None
        membership.save(update_fields=['is_active', 'removed_at'])
        invitation.status = RoomInvitation.STATUS_ACCEPTED
        invitation.responded_at = timezone.now()
        invitation.save(update_fields=['status', 'responded_at'])
        messages.success(request, f'已加入群聊「{invitation.room.name}」')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(invitation.room.name)}")

    invitation.status = RoomInvitation.STATUS_DECLINED
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=['status', 'responded_at'])
    messages.success(request, '已拒绝群邀请')
    return redirect('inbox')


@login_required
@require_POST
def respond_room_join_request(request, request_id):
    try:
        join_request = RoomJoinRequest.objects.select_related('room', 'requester').get(id=request_id)
    except RoomJoinRequest.DoesNotExist:
        messages.error(request, '入群申请不存在')
        return redirect('inbox')

    membership = get_room_membership(join_request.room, request.user)
    if not can_manage_room_members(join_request.room, request.user, membership=membership):
        messages.error(request, '只有群主或群管理员才能处理入群申请')
        return redirect('inbox')
    if join_request.status != RoomJoinRequest.STATUS_PENDING:
        messages.info(request, '这条入群申请已经处理过了')
        return redirect('inbox')

    action = request.POST.get('action', '').strip()
    if action == 'accept':
        requester_membership, _ = RoomMembership.objects.get_or_create(
            room=join_request.room,
            user=join_request.requester,
            defaults={'is_active': True, 'removed_at': None},
        )
        requester_membership.is_active = True
        requester_membership.removed_at = None
        requester_membership.save(update_fields=['is_active', 'removed_at'])
        join_request.status = RoomJoinRequest.STATUS_ACCEPTED
        join_request.responded_at = timezone.now()
        join_request.save(update_fields=['status', 'responded_at'])
        messages.success(request, f'已通过 {join_request.requester.username} 的入群申请')
        return redirect('inbox')

    join_request.status = RoomJoinRequest.STATUS_REJECTED
    join_request.responded_at = timezone.now()
    join_request.save(update_fields=['status', 'responded_at'])
    messages.success(request, f'已拒绝 {join_request.requester.username} 的入群申请')
    return redirect('inbox')


@login_required
def add_friend_page(request):
    profile = get_or_create_chat_profile(request.user)
    if request.method == 'POST':
        friend_id = request.POST.get('friend_id', '').strip().lower()
        if not friend_id:
            messages.error(request, '请输入好友 ID')
            return redirect('add_friend')

        if profile.friend_id == friend_id:
            messages.error(request, '不能添加自己为好友')
            return redirect('add_friend')

        try:
            recipient_profile = UserChatProfile.objects.select_related('user').get(friend_id=friend_id)
        except UserChatProfile.DoesNotExist:
            messages.error(request, '没有找到这个好友 ID')
            return redirect('add_friend')

        if Friendship.objects.filter(user=request.user, friend=recipient_profile.user).exists():
            messages.info(request, '你们已经是好友了')
            return redirect('add_friend')

        friend_request, created = FriendRequest.objects.get_or_create(
            sender=request.user,
            recipient=recipient_profile.user,
            defaults={'status': FriendRequest.STATUS_PENDING},
        )
        if not created and friend_request.status == FriendRequest.STATUS_REJECTED:
            friend_request.status = FriendRequest.STATUS_PENDING
            friend_request.responded_at = None
            friend_request.save(update_fields=['status', 'responded_at'])
        elif not created:
            messages.info(request, '好友申请已经发出，请等待对方处理')
            return redirect('add_friend')

        messages.success(request, f'已向 {recipient_profile.user.username} 发送好友申请')
        return redirect('add_friend')

    return render(request, 'chat/add_friend.html', {
        'chat_profile': profile,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'recent_requests': FriendRequest.objects.filter(
            sender=request.user,
        ).select_related('recipient', 'recipient__chat_profile')[:10],
    })


@login_required
@require_POST
def send_friend_request(request):
    friend_id = request.POST.get('friend_id', '').strip().lower()
    if not friend_id:
        messages.error(request, '请输入好友 ID')
        return redirect(request.POST.get('next') or 'chat_index')

    sender_profile = get_or_create_chat_profile(request.user)
    if sender_profile.friend_id == friend_id:
        messages.error(request, '不能添加自己为好友')
        return redirect(request.POST.get('next') or 'chat_index')

    try:
        recipient_profile = UserChatProfile.objects.select_related('user').get(friend_id=friend_id)
    except UserChatProfile.DoesNotExist:
        messages.error(request, '没有找到这个好友 ID')
        return redirect(request.POST.get('next') or 'chat_index')

    if Friendship.objects.filter(user=request.user, friend=recipient_profile.user).exists():
        messages.info(request, '你们已经是好友了')
        return redirect(request.POST.get('next') or 'chat_index')

    friend_request, created = FriendRequest.objects.get_or_create(
        sender=request.user,
        recipient=recipient_profile.user,
        defaults={'status': FriendRequest.STATUS_PENDING},
    )
    if not created and friend_request.status == FriendRequest.STATUS_REJECTED:
        friend_request.status = FriendRequest.STATUS_PENDING
        friend_request.responded_at = None
        friend_request.save(update_fields=['status', 'responded_at'])
    elif not created:
        messages.info(request, '好友申请已经发出，请等待对方处理')
        return redirect(request.POST.get('next') or 'chat_index')

    messages.success(request, f'已向 {recipient_profile.user.username} 发送好友申请')
    return redirect(request.POST.get('next') or 'chat_index')


@login_required
@require_POST
def respond_friend_request(request, request_id):
    action = request.POST.get('action')
    try:
        friend_request = FriendRequest.objects.get(id=request_id, recipient=request.user)
    except FriendRequest.DoesNotExist:
        messages.error(request, '好友申请不存在')
        return redirect('inbox')

    if friend_request.status != FriendRequest.STATUS_PENDING:
        messages.info(request, '这条好友申请已经处理过了')
        return redirect('inbox')

    if action == 'accept':
        friend_request.status = FriendRequest.STATUS_ACCEPTED
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=['status', 'responded_at'])
        Friendship.objects.get_or_create(user=request.user, friend=friend_request.sender)
        Friendship.objects.get_or_create(user=friend_request.sender, friend=request.user)
        messages.success(request, f'已通过 {friend_request.sender.username} 的好友申请')
    else:
        friend_request.status = FriendRequest.STATUS_REJECTED
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=['status', 'responded_at'])
        messages.info(request, f'已拒绝 {friend_request.sender.username} 的好友申请')
    return redirect('inbox')


def logout_view(request):
    """注销登录"""
    if request.user.is_authenticated:
        UserSession.objects.filter(user=request.user).delete()
        notify_user_presence_changed(request.user)
    logout(request)
    return redirect('login')


def is_admin_user(user):
    """检查用户是否为管理员"""
    return user.is_authenticated and user.is_superuser


@user_passes_test(is_admin_user)
def admin_dashboard(request):
    """管理员仪表板"""
    # 获取统计数据
    total_users = User.objects.count()
    total_rooms = Room.objects.count()
    total_sessions = UserSession.objects.count()
    
    # 获取最近注册的用户
    recent_users = User.objects.order_by('-date_joined')[:10]
    
    # 获取所有房间
    rooms = Room.objects.all()
    
    # 获取所有用户会话
    user_sessions = UserSession.objects.select_related('user').all()
    
    context = {
        'total_users': total_users,
        'total_rooms': total_rooms,
        'total_sessions': total_sessions,
        'recent_users': recent_users,
        'rooms': rooms,
        'user_sessions': user_sessions,
        'site_config': SiteConfiguration.get_solo(),
    }
    
    return render(request, 'chat/admin/dashboard.html', context)


@user_passes_test(is_admin_user)
def admin_users(request):
    """用户管理"""
    users = User.objects.all()
    
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action = request.POST.get('action')
        
        if user_id and action:
            try:
                user = User.objects.get(id=user_id)
                if action == 'delete':
                    # 删除用户及其相关数据
                    UserSession.objects.filter(user=user).delete()
                    Room.objects.filter(created_by=user).update(created_by=None)
                    user.delete()
                    messages.success(request, f'用户 {user.username} 已删除')
                elif action == 'toggle_superuser':
                    user.is_superuser = not user.is_superuser
                    user.is_staff = user.is_superuser
                    user.save()
                    messages.success(request, f'用户 {user.username} 的管理员状态已更改')
                elif action == 'toggle_active':
                    user.is_active = not user.is_active
                    user.save()
                    messages.success(request, f'用户 {user.username} 的激活状态已更改')
            except User.DoesNotExist:
                messages.error(request, '用户不存在')
        
        return redirect('admin_users')
    
    return render(request, 'chat/admin/users.html', {'users': users})


@user_passes_test(is_admin_user)
def admin_rooms(request):
    """房间管理"""
    rooms = Room.objects.select_related('created_by').all()
    
    if request.method == 'POST':
        room_name = request.POST.get('room_name')
        action = request.POST.get('action')
        
        if room_name and action:
            try:
                room = Room.objects.get(name=room_name)
                if action == 'delete':
                    room.delete()
                    messages.success(request, f'房间 {room_name} 已删除')
            except Room.DoesNotExist:
                messages.error(request, '房间不存在')
        
        return redirect('admin_rooms')
    
    return render(request, 'chat/admin/rooms.html', {'rooms': rooms})


@user_passes_test(is_admin_user)
def admin_sessions(request):
    """会话管理"""
    sessions = UserSession.objects.select_related('user').all()
    
    if request.method == 'POST':
        session_id = request.POST.get('session_id')
        action = request.POST.get('action')
        
        if session_id and action:
            try:
                session = UserSession.objects.get(id=session_id)
                if action == 'delete':
                    session.delete()
                    messages.success(request, f'会话 {session.session_key[:8]}... 已删除')
            except UserSession.DoesNotExist:
                messages.error(request, '会话不存在')
        
        return redirect('admin_sessions')
    
    return render(request, 'chat/admin/sessions.html', {'sessions': sessions})


@user_passes_test(is_admin_user)
def admin_user_password(request, user_id):
    """管理员修改用户密码"""
    try:
        managed_user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('admin_users')

    form = AdminUserPasswordForm(managed_user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'用户 {managed_user.username} 的密码已更新')
        return redirect('admin_users')

    return render(request, 'chat/admin/user_password.html', {
        'managed_user': managed_user,
        'form': form,
        'password_help_html': mark_safe(password_validators_help_text_html()),
    })


@user_passes_test(is_admin_user)
def admin_site_settings(request):
    """站点设置"""
    site_config = SiteConfiguration.get_solo()
    if site_config is None:
        messages.error(request, '当前数据库尚未完成站点配置初始化，请先执行 migrate')
        return redirect('admin_dashboard')

    form = SiteConfigurationForm(request.POST or None, instance=site_config)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, '站点设置已更新，新的受信任来源会在后续请求中生效')
        return redirect('admin_site_settings')

    return render(request, 'chat/admin/site_settings.html', {
        'form': form,
        'site_config': site_config,
        'default_admin_username': 'xyadmin',
        'default_admin_password': 'xyadmin123',
    })
