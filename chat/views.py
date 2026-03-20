# chat/views.py
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_protect
from django.utils.safestring import mark_safe
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.sessions.models import Session
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import IntegrityError
from .models import Room, UserSession
from .services.geoip_service import GeoIPService
import json


DEFAULT_ROOM_AVATARS = ['💬', '🐱', '🐶', '🐻', '🎮', '📚', '☕', '🌙', '🎵', '🍀']


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
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
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
        form = UserCreationForm()
    
    return render(request, 'chat/register.html', {'form': form})


@login_required
def index(request):
    """聊天室首页"""
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
                messages.success(request, f'房间 "{room_name}" 创建成功')
        return redirect('chat_index')
    
    rooms = Room.objects.all()
    return render(request, 'chat/index.html', {
        'rooms': rooms,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'user': request.user,
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

    if request.method == 'POST':
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

    return render(request, 'chat/room.html', {
        'room': room,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_name': room.name,
        'room_name_json': mark_safe(json.dumps(room.name)),
        'room_total_members': room.total_members,
        'is_owner': is_owner
    })


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
