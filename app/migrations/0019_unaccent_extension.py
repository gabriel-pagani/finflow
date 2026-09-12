from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0018_remove_transaction_transaction_transfer_leg_is_internal_and_more'),
    ]

    operations = [
        UnaccentExtension(),
    ]
