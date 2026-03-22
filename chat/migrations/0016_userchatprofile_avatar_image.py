from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0015_siteconfiguration'),
    ]

    operations = [
        migrations.AddField(
            model_name='userchatprofile',
            name='avatar_image',
            field=models.ImageField(blank=True, null=True, upload_to='avatars/', verbose_name='头像图片'),
        ),
    ]
