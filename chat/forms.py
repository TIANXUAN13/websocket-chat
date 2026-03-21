from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


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
