from django.db import migrations, models
import django.db.models.deletion
import random


def populate_room_ids(apps, schema_editor):
    Room = apps.get_model('chat', 'Room')
    existing_ids = set()

    for room in Room.objects.all().order_by('id'):
        candidate = ''.join(random.SystemRandom().choice('0123456789') for _ in range(12))
        while candidate in existing_ids:
            candidate = ''.join(random.SystemRandom().choice('0123456789') for _ in range(12))
        room.room_id = candidate
        room.save(update_fields=['room_id'])
        existing_ids.add(candidate)


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0017_room_avatar_image_roommembership_is_admin'),
    ]

    operations = [
        migrations.AddField(
            model_name='room',
            name='join_policy',
            field=models.CharField(choices=[('open', '可直接加入'), ('approval', '需要审批')], default='approval', max_length=20, verbose_name='入群方式'),
        ),
        migrations.AddField(
            model_name='room',
            name='room_id',
            field=models.CharField(blank=True, default='', max_length=12, verbose_name='群ID'),
        ),
        migrations.RunPython(populate_room_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='room',
            name='room_id',
            field=models.CharField(blank=True, default='', max_length=12, unique=True, verbose_name='群ID'),
        ),
        migrations.CreateModel(
            name='RoomJoinRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', '待处理'), ('accepted', '已通过'), ('rejected', '已拒绝')], default='pending', max_length=20)),
                ('note', models.CharField(blank=True, default='', max_length=160, verbose_name='申请备注')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('requester', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_join_requests', to='auth.user')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='join_requests', to='chat.room')),
            ],
            options={
                'verbose_name': '入群申请',
                'verbose_name_plural': '入群申请',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='RoomInvitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', '待处理'), ('accepted', '已接受'), ('declined', '已拒绝')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('invited_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_room_invitations', to='auth.user')),
                ('invited_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='room_invitations', to='auth.user')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitations', to='chat.room')),
            ],
            options={
                'verbose_name': '群邀请',
                'verbose_name_plural': '群邀请',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='roomjoinrequest',
            constraint=models.UniqueConstraint(fields=('room', 'requester'), name='unique_room_join_request_pair'),
        ),
        migrations.AddConstraint(
            model_name='roominvitation',
            constraint=models.UniqueConstraint(fields=('room', 'invited_user'), name='unique_room_invitation_pair'),
        ),
    ]
