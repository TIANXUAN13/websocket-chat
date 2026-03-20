from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0004_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='room',
            name='avatar',
            field=models.CharField(default='💬', max_length=8, verbose_name='房间头像'),
        ),
        migrations.AddField(
            model_name='room',
            name='description',
            field=models.CharField(blank=True, default='一起聊聊吧', max_length=120, verbose_name='房间简介'),
        ),
    ]
