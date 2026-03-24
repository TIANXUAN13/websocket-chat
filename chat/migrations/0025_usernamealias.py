from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0024_alter_useremoji_options_useremoji_last_used_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UsernameAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(max_length=150, unique=True, verbose_name='历史用户名')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='username_aliases', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '用户名别名',
                'verbose_name_plural': '用户名别名',
                'ordering': ['-created_at'],
            },
        ),
    ]
