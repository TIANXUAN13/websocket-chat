from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0019_normalize_room_ids'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='site_favicon',
            field=models.ImageField(blank=True, null=True, upload_to='site_assets/', verbose_name='网页图标'),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='site_title',
            field=models.CharField(blank=True, default='animal chat', max_length=80, verbose_name='网页标题'),
        ),
    ]
