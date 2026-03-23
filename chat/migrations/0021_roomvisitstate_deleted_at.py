from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0020_siteconfiguration_branding'),
    ]

    operations = [
        migrations.AddField(
            model_name='roomvisitstate',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
