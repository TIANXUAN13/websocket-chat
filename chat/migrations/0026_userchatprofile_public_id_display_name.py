from django.db import migrations, models
import random


def populate_profile_identity(apps, schema_editor):
    UserChatProfile = apps.get_model('chat', 'UserChatProfile')
    alphabet = '23456789abcdefghjkmnpqrstuvwxyz'
    generator = random.SystemRandom()
    used_ids = set(UserChatProfile.objects.exclude(public_id='').values_list('public_id', flat=True))

    for profile in UserChatProfile.objects.select_related('user'):
        changed = []
        if not profile.display_name:
            profile.display_name = profile.user.username
            changed.append('display_name')
        if not profile.public_id:
            candidate = ''.join(generator.choice(alphabet) for _ in range(12))
            while candidate in used_ids:
                candidate = ''.join(generator.choice(alphabet) for _ in range(12))
            used_ids.add(candidate)
            profile.public_id = candidate
            changed.append('public_id')
        if changed:
            profile.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0025_usernamealias'),
    ]

    operations = [
        migrations.AddField(
            model_name='userchatprofile',
            name='display_name',
            field=models.CharField(blank=True, default='', max_length=40, verbose_name='展示名称'),
        ),
        migrations.AddField(
            model_name='userchatprofile',
            name='public_id',
            field=models.CharField(blank=True, default='', max_length=12, verbose_name='公开用户ID'),
        ),
        migrations.RunPython(populate_profile_identity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='userchatprofile',
            name='public_id',
            field=models.CharField(blank=True, default='', max_length=12, unique=True, verbose_name='公开用户ID'),
        ),
    ]
