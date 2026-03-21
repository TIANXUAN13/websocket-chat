import json
from urllib.parse import quote

# chat/views.py
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils.safestring import mark_safe
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.sessions.models import Session
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q
from .forms import RegistrationForm
from .models import (
    DirectConversation,
    DirectConversationState,
    DirectMessage,
    FriendRequest,
    Friendship,
    Room,
    RoomMembership,
    RoomVisitState,
    UserChatProfile,
    UserSession,
)
from .presets import CHAT_BUBBLE_STYLES, CHAT_COLOR_THEMES, DEFAULT_CHAT_STYLE, DEFAULT_CHAT_THEME
from .services.geoip_service import GeoIPService


DEFAULT_ROOM_AVATARS = ['💬', '🐱', '🐶', '🐻', '🎮', '📚', '☕', '🌙', '🎵', '🍀']


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
    rooms = Room.objects.all().prefetch_related('messages')
    embed_version = '20260322n'
    for room in rooms:
        latest_message = room.messages.order_by('-timestamp').first()
        if not latest_message:
            continue

        state = get_or_create_room_visit_state(user, room)
        unread_qs = room.messages.exclude(user=user)
        if state.last_read_at:
            unread_qs = unread_qs.filter(timestamp__gt=state.last_read_at)

        threads.append({
            'type': 'room',
            'name': room.name,
            'url': reverse('chat_room', args=[room.name]),
            'embed_url': f"{reverse('chat_room', args=[room.name])}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=room&target={quote(room.name)}",
            'unread_count': unread_qs.count(),
            'last_message_preview': latest_message.message[:36],
            'last_message_at': latest_message.timestamp,
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
            'url': reverse('direct_chat', args=[other_user.username]),
            'embed_url': f"{reverse('direct_chat', args=[other_user.username])}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=direct&target={quote(other_user.username)}",
            'delete_url': reverse('delete_direct_conversation', args=[other_user.username]),
            'unread_count': unread_qs.count(),
            'last_message_preview': latest_message.content[:36],
            'last_message_at': latest_message.created_at,
        })

    return sorted(threads, key=lambda item: item['last_message_at'], reverse=True)


def get_inbox_context(user):
    pending_requests = FriendRequest.objects.filter(
        recipient=user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')
    room_threads = build_room_threads(user)
    direct_threads = build_direct_threads(user)
    conversation_threads = sorted(room_threads + direct_threads, key=lambda item: item['last_message_at'], reverse=True)

    return {
        'pending_requests': pending_requests,
        'pending_friend_requests_count': pending_requests.count(),
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
                'friend_id': profile.friend_id,
                'is_owner': bool(room.created_by and room.created_by_id == linked_user.id),
                'is_self': bool(current_user and current_user.id == linked_user.id),
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
                'friend_id': profile.friend_id if profile else '',
                'is_owner': bool(room.created_by and room.created_by.username == username),
                'is_self': bool(current_user and current_user.username == username),
            })

    member_records.sort(key=lambda item: (
        not item['is_owner'],
        not item['is_self'],
        item['username'].lower(),
    ))
    return member_records


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
    
    rooms = Room.objects.all()
    profile = get_or_create_chat_profile(request.user)
    incoming_friend_requests = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')[:5]
    pending_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
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
        'pending_friend_requests_count': pending_count,
        'inbox_badge_count': pending_count,
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
    try:
        room = Room.objects.get(name=room_name)
        is_owner = room.created_by == request.user
    except Room.DoesNotExist:
        is_owner = False
        room = None

    if not room:
        messages.error(request, '房间不存在')
        return redirect('chat_index')

    try:
        GeoIPService.refresh_user_location_if_needed(request.user)
    except Exception as e:
        print(f"刷新用户地理位置时出错: {e}")

    if request.method == 'POST':
        action = request.POST.get('action', 'room_settings')

        if not is_owner:
            messages.error(request, '只有房主才能编辑房间资料')
            return redirect('chat_room', room_name=room.name)

        new_room_name = request.POST.get('room_name', room.name).strip() or room.name
        room.avatar = request.POST.get('room_avatar', room.avatar).strip() or room.avatar
        if room.avatar not in DEFAULT_ROOM_AVATARS:
            room.avatar = '💬'
        room.description = request.POST.get('room_description', room.description).strip()[:120] or '一起聊聊吧'
        room.name = new_room_name

        try:
            room.save()
        except IntegrityError:
            messages.error(request, '这个房间名已经被用了')
            return redirect('chat_room', room_name=room_name)
        messages.success(request, '房间资料已更新')
        return redirect('chat_room', room_name=room.name)

    chat_profile = get_or_create_chat_profile(request.user)
    room_membership, _ = get_or_create_room_membership(room, request.user)
    visit_state = get_or_create_room_visit_state(request.user, room)
    visit_state.last_read_at = timezone.now()
    visit_state.save(update_fields=['last_read_at'])
    pending_friend_requests_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    embed_mode = request.GET.get('embed') == '1'

    return render(request, 'chat/room.html', {
        'room': room,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_name': room.name,
        'room_name_json': mark_safe(json.dumps(room.name)),
        'room_total_members': room.total_members,
        'room_members_json': mark_safe(json.dumps(build_room_member_records(room, request.user))),
        'is_removed_from_room': bool(room_membership and (not room_membership.is_active) and room.created_by_id != request.user.id),
        'is_owner': is_owner,
        'chat_profile': chat_profile,
        'chat_profile_payload_json': mark_safe(json.dumps(chat_profile.to_payload())),
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'pending_friend_requests_count': pending_friend_requests_count,
        'inbox_badge_count': pending_friend_requests_count,
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

    if request.method == 'POST':
        requested_friend_id = request.POST.get('friend_id', '').strip().lower()
        if requested_friend_id and UserChatProfile.objects.exclude(user=request.user).filter(friend_id=requested_friend_id).exists():
            messages.error(request, '这个好友 ID 已经被别人使用了')
            return redirect('profile_settings')

        if requested_friend_id and (len(requested_friend_id) < 8 or len(requested_friend_id) > 11):
            messages.error(request, '好友 ID 长度需要在 8 到 11 位之间')
            return redirect('profile_settings')
        if requested_friend_id and not all(ch.isalnum() or ch == '_' for ch in requested_friend_id):
            messages.error(request, '好友 ID 只能包含小写字母、数字或下划线')
            return redirect('profile_settings')

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
        chat_profile.save(update_fields=['friend_id', 'avatar_label', 'bio', 'color_theme', 'bubble_style', 'show_location'])
        messages.success(request, '个人聊天设置已更新')
        return redirect('profile_settings')

    return render(request, 'chat/profile.html', {
        'chat_profile': chat_profile,
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'current_location': getattr(request.user, 'location', None),
        'user': request.user,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'friendships': Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile'),
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
        'total_unread_count': context['total_unread_count'],
        'room_threads': [
            {
                'name': item['name'],
                'url': item['url'],
                'unread_count': item['unread_count'],
            }
            for item in context['room_threads']
        ],
        'direct_threads': [
            {
                'name': item['name'],
                'url': item['url'],
                'delete_url': item['delete_url'],
                'friend_id': item['friend_id'],
                'unread_count': item['unread_count'],
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

    return render(request, 'chat/user_profile.html', {
        'chat_profile': own_profile,
        'target_user': target_user,
        'target_profile': target_profile,
        'is_self': is_self,
        'is_friend': is_friend,
        'outgoing_request': outgoing_request,
        'incoming_request': incoming_request,
        'current_location': getattr(target_user, 'location', None),
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

    if request.method == 'POST':
        action = request.POST.get('action', 'send')
        if action == 'clear_history':
            state.cleared_at = timezone.now()
            state.save(update_fields=['cleared_at'])
            messages.success(request, '已清空你这边看到的私聊历史')
            return redirect('direct_chat', username=other_user.username)

        content = request.POST.get('content', '').strip()
        if content:
            DirectMessage.objects.create(conversation=conversation, sender=request.user, content=content)
            return redirect('direct_chat', username=other_user.username)

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

        if room_avatar not in DEFAULT_ROOM_AVATARS:
            room_avatar = '💬'
        if not room_description:
            room_description = '一起聊聊吧'

        if room_name:
            if Room.objects.filter(name=room_name).exists():
                messages.error(request, '房间已存在')
            else:
                room = Room.objects.create(
                    name=room_name,
                    avatar=room_avatar,
                    description=room_description[:120],
                    created_by=request.user,
                )
                get_or_create_room_membership(room, request.user)
                messages.success(request, f'房间 "{room_name}" 创建成功')
                return redirect('chat_room', room_name=room.name)

    return render(request, 'chat/create_room.html', {
        'chat_profile': get_or_create_chat_profile(request.user),
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


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
