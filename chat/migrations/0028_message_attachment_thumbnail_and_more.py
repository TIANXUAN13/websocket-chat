from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0027_siteconfiguration_chat_attachment_max_mb'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='attachment_thumbnail',
            field=models.ImageField(blank=True, null=True, upload_to='chat_attachments/rooms/%Y/%m/thumbs/', verbose_name='附件缩略图'),
        ),
        migrations.AddField(
            model_name='directmessage',
            name='attachment_thumbnail',
            field=models.ImageField(blank=True, null=True, upload_to='chat_attachments/direct/%Y/%m/thumbs/', verbose_name='附件缩略图'),
        ),
    ]
