from django import forms
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User

from .models import SiteConfiguration


class RegistrationForm(UserCreationForm):
    friend_id = forms.CharField(
        max_length=11,
        min_length=8,
        required=False,
        label='好友 ID',
        help_text='留空则自动使用用户名生成一个好友 ID',
        widget=forms.TextInput(
            attrs={
                'minlength': 8,
                'maxlength': 11,
                'placeholder': '8-11 位，留空则自动生成',
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'friend_id', 'password1', 'password2')

    def clean_friend_id(self):
        value = (self.cleaned_data.get('friend_id') or '').strip().lower()
        if not value:
            return ''
        if not all(ch.isalnum() or ch == '_' for ch in value):
            raise forms.ValidationError('好友 ID 只能包含小写字母、数字或下划线')
        if len(value) < 8 or len(value) > 11:
            raise forms.ValidationError('好友 ID 长度需要在 8 到 11 位之间')
        return value


class SiteConfigurationForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = ('trusted_origins', 'cors_allowed_origins', 'allow_all_cors')
        widgets = {
            'trusted_origins': forms.Textarea(attrs={'rows': 6, 'placeholder': '每行一个来源，例如：https://chat.6143443.xyz'}),
            'cors_allowed_origins': forms.Textarea(attrs={'rows': 6, 'placeholder': '每行一个来源，例如：https://app.example.com'}),
        }
        labels = {
            'trusted_origins': 'CSRF 受信任来源',
            'cors_allowed_origins': 'CORS 允许来源',
            'allow_all_cors': '允许所有跨域来源',
        }
        help_texts = {
            'trusted_origins': '用于 Django 的 CSRF Origin 校验。需要带协议头，例如 https://example.com',
            'cors_allowed_origins': '用于响应头 Access-Control-Allow-Origin。需要带协议头，例如 https://example.com',
            'allow_all_cors': '开发调试时可以开启；生产环境建议关闭并只填写明确来源。',
        }

    def clean_trusted_origins(self):
        return self._clean_origin_block('trusted_origins')

    def clean_cors_allowed_origins(self):
        return self._clean_origin_block('cors_allowed_origins')

    def _clean_origin_block(self, field_name):
        raw_value = self.cleaned_data.get(field_name, '')
        origins = SiteConfiguration.parse_origin_lines(raw_value)
        for origin in origins:
            if not (origin.startswith('http://') or origin.startswith('https://')):
                raise forms.ValidationError('来源必须以 http:// 或 https:// 开头')
        return '\n'.join(origins)


class AdminUserPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = '新密码'
        self.fields['new_password2'].label = '确认新密码'


class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].label = '当前密码'
        self.fields['new_password1'].label = '新密码'
        self.fields['new_password2'].label = '确认新密码'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')
            field.widget.attrs.pop('autofocus', None)
