from django.db import migrations
import random


def normalize_room_ids(apps, schema_editor):
    Room = apps.get_model('chat', 'Room')
    used_ids = set(
        value for value in Room.objects.exclude(room_id='').values_list('room_id', flat=True)
        if value and len(value) == 12 and value.isdigit()
    )

    for room in Room.objects.all().order_by('id'):
        if room.room_id and len(room.room_id) == 12 and room.room_id.isdigit():
            continue

        candidate = ''.join(random.SystemRandom().choice('0123456789') for _ in range(12))
        while candidate in used_ids:
            candidate = ''.join(random.SystemRandom().choice('0123456789') for _ in range(12))
        room.room_id = candidate
        room.save(update_fields=['room_id'])
        used_ids.add(candidate)


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0018_room_access_and_requests'),
    ]

    operations = [
        migrations.RunPython(normalize_room_ids, migrations.RunPython.noop),
    ]
