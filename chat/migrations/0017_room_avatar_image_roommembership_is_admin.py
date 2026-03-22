from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0016_userchatprofile_avatar_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='room',
            name='avatar_image',
            field=models.ImageField(blank=True, null=True, upload_to='room_avatars/', verbose_name='房间头像图片'),
        ),
        migrations.AddField(
            model_name='roommembership',
            name='is_admin',
            field=models.BooleanField(default=False, verbose_name='是否为群管理员'),
        ),
    ]
