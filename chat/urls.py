# chat/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('delete/<str:room_name>/', views.delete_room, name='delete_room'),
    path('', views.index, name='chat_index'),
    path('<str:room_name>/', views.room, name='chat_room'),
    
    # 管理员界面
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/users/', views.admin_users, name='admin_users'),
    path('admin/rooms/', views.admin_rooms, name='admin_rooms'),
    path('admin/sessions/', views.admin_sessions, name='admin_sessions'),
]